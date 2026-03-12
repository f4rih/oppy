import json
import ipaddress
import os
import random
import re
import socket
import ssl
import struct
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Iterable, List

from .constants import (
    DEFAULT_CURL_TIMEOUT_SECONDS,
    DEFAULT_DNS_RETRIES,
    DEFAULT_DNS_RETRY_INTERVAL_MS,
    DEFAULT_PROXY_BASE_PORT,
    DEFAULT_TEST_URL,
    LOG_DIR,
)
from .proxy_links import parse_link
from .vless import generate_config, parse_vless
from .vmess import generate_vmess_config, parse_vmess
from .xray import extract_exit_ip, has_tunnel_established, read_text_if_exists


def _build_dns_query(domain: str) -> tuple[int, bytes]:
    txid = random.randint(0, 0xFFFF)
    header = struct.pack("!HHHHHH", txid, 0x0100, 1, 0, 0, 0)
    qname = b"".join(
        bytes([len(label)]) + label.encode("ascii")
        for label in domain.strip(".").split(".")
        if label
    ) + b"\x00"
    question = qname + struct.pack("!HH", 1, 1)  # QTYPE=A, QCLASS=IN
    return txid, header + question


def _parse_dns_response(txid: int, payload: bytes) -> tuple[bool, int, int]:
    if len(payload) < 12:
        return False, -1, -1
    resp_txid, flags, _qdcount, ancount, _nscount, _arcount = struct.unpack(
        "!HHHHHH", payload[:12]
    )
    if resp_txid != txid:
        return False, -1, -1
    is_response = bool(flags & 0x8000)
    rcode = flags & 0x000F
    if not is_response:
        return False, rcode, ancount
    return True, rcode, ancount


def _humanize_reason(reason: str, status_code: str = "") -> str:
    raw = (reason or "").strip()
    code = (status_code or "").strip()
    low = raw.lower()

    if low.startswith("http_status="):
        http_code = raw.split("=", 1)[1].strip()
        if http_code in {"", "000", "none"}:
            return "No HTTP response from test URL."
        return f"Test URL returned HTTP {http_code}."

    curl_code = None
    match = re.search(r"curl:\s*\((\d+)\)", raw, flags=re.IGNORECASE)
    if match:
        try:
            curl_code = int(match.group(1))
        except Exception:
            curl_code = None

    if "operation not permitted" in low:
        return "Network operation blocked by the current environment."
    if "timed out" in low or curl_code == 28:
        return "Connection timed out while testing this link."
    if "could not resolve host" in low or curl_code == 6:
        return "DNS resolution failed for the test URL."
    if "could not resolve proxy" in low or curl_code == 5:
        return "DNS resolution failed for the proxy server."
    if (
        "failed to connect" in low
        or "connection refused" in low
        or "no route to host" in low
        or curl_code == 7
    ):
        return "Could not connect to proxy server (refused or unreachable)."
    if (
        "ssl_connect" in low
        or "ssl error" in low
        or "certificate verify failed" in low
        or "unexpected_eof_while_reading" in low
        or "eof occurred in violation of protocol" in low
        or curl_code in {35, 60}
    ):
        return "TLS/SSL handshake failed (check SNI, TLS mode, or certificate)."
    if "empty reply from server" in low or curl_code == 52:
        return "Connected, but the server sent no response."
    if "recv failure" in low or "connection reset" in low or curl_code == 56:
        return "Connection was reset while receiving data."
    if "can't complete socks5 connection" in low or curl_code == 97:
        return "SOCKS5 handshake failed (auth/protocol mismatch)."

    if code and code not in {"", "000", "none"}:
        return f"Test URL returned HTTP {code}."
    if code in {"000", "none"} and not raw:
        return "No HTTP response from test URL."
    if raw:
        cleaned = re.sub(r"^curl:\s*\(\d+\)\s*", "", raw, flags=re.IGNORECASE).strip()
        return cleaned[0].upper() + cleaned[1:] if cleaned else raw
    return ""


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("unexpected EOF while reading from socket")
        data.extend(chunk)
    return bytes(data)


def _socks5_reply_message(code: int) -> str:
    mapping = {
        1: "general SOCKS server failure",
        2: "connection blocked by ruleset",
        3: "network unreachable",
        4: "host unreachable",
        5: "connection refused",
        6: "TTL expired",
        7: "command not supported",
        8: "address type not supported",
    }
    return mapping.get(code, f"SOCKS5 error code {code}")


def _connect_via_socks5(
    proxy_host: str,
    proxy_port: int,
    target_host: str,
    target_port: int,
    timeout_seconds: float,
    username: str = "",
    password: str = "",
) -> socket.socket:
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout_seconds)
    sock.settimeout(timeout_seconds)
    try:
        methods = [0x00]  # no-auth
        if username or password:
            methods.append(0x02)  # user/pass
        sock.sendall(bytes([0x05, len(methods), *methods]))
        ver, method = _recv_exact(sock, 2)
        if ver != 0x05:
            raise ConnectionError("invalid SOCKS5 server response")
        if method == 0xFF:
            raise PermissionError("SOCKS5 auth method rejected")
        if method == 0x02:
            user_b = username.encode("utf-8", errors="ignore")
            pass_b = password.encode("utf-8", errors="ignore")
            if len(user_b) > 255 or len(pass_b) > 255:
                raise ValueError("SOCKS5 username/password too long")
            auth_req = bytes([0x01, len(user_b)]) + user_b + bytes([len(pass_b)]) + pass_b
            sock.sendall(auth_req)
            auth_ver, auth_status = _recv_exact(sock, 2)
            if auth_ver != 0x01 or auth_status != 0x00:
                raise PermissionError("SOCKS5 authentication failed")
        elif method != 0x00:
            raise PermissionError("unsupported SOCKS5 authentication method")

        try:
            ip_obj = ipaddress.ip_address(target_host)
            if isinstance(ip_obj, ipaddress.IPv4Address):
                atyp = 0x01
            else:
                atyp = 0x04
            addr = ip_obj.packed
        except ValueError:
            try:
                host_b = target_host.encode("idna")
            except UnicodeError as exc:
                raise ValueError(f"invalid target host: {exc}") from exc
            if not host_b or len(host_b) > 255:
                raise ValueError("invalid target host for SOCKS5")
            atyp = 0x03
            addr = bytes([len(host_b)]) + host_b

        req = b"\x05\x01\x00" + bytes([atyp]) + addr + struct.pack("!H", target_port)
        sock.sendall(req)
        ver, rep, _rsv, bnd_atyp = _recv_exact(sock, 4)
        if ver != 0x05:
            raise ConnectionError("invalid SOCKS5 connect reply")
        if rep != 0x00:
            raise ConnectionError(f"SOCKS5 connect failed: {_socks5_reply_message(rep)}")

        if bnd_atyp == 0x01:
            _recv_exact(sock, 4)
        elif bnd_atyp == 0x03:
            domain_len = _recv_exact(sock, 1)[0]
            _recv_exact(sock, domain_len)
        elif bnd_atyp == 0x04:
            _recv_exact(sock, 16)
        _recv_exact(sock, 2)  # bound port
        return sock
    except Exception:
        try:
            sock.close()
        except Exception:
            pass
        raise


def _http_probe_via_socks(
    test_url: str,
    proxy_host: str,
    proxy_port: int,
    timeout_seconds: float,
    username: str = "",
    password: str = "",
) -> tuple[str, str, int | None, str]:
    parsed_url = urllib.parse.urlparse(test_url.strip())
    scheme = (parsed_url.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return "", "", None, "unsupported test URL scheme (use http/https)"

    target_host = (parsed_url.hostname or "").strip()
    if not target_host:
        return "", "", None, "invalid test URL host"
    target_port = parsed_url.port or (443 if scheme == "https" else 80)
    path = parsed_url.path or "/"
    if parsed_url.query:
        path = f"{path}?{parsed_url.query}"

    start = time.time()
    raw_response = b""
    status_code = ""
    conn: socket.socket | ssl.SSLSocket | None = None
    try:
        proxy_sock = _connect_via_socks5(
            proxy_host=proxy_host,
            proxy_port=int(proxy_port),
            target_host=target_host,
            target_port=int(target_port),
            timeout_seconds=timeout_seconds,
            username=username,
            password=password,
        )
        conn = proxy_sock
        if scheme == "https":
            context = ssl.create_default_context()
            conn = context.wrap_socket(proxy_sock, server_hostname=target_host)
            conn.settimeout(timeout_seconds)

        request = (
            f"GET {path} HTTP/1.0\r\n"
            f"Host: {target_host}\r\n"
            "User-Agent: OPPY/1.0\r\n"
            "Accept: */*\r\n"
            "Accept-Encoding: identity\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii", errors="ignore")
        conn.sendall(request)

        chunks: list[bytes] = []
        total_bytes = 0
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            total_bytes += len(chunk)
            # Guard memory for malformed endless streams.
            if total_bytes > 5 * 1024 * 1024:
                break
        raw_response = b"".join(chunks)
        latency = int((time.time() - start) * 1000)
    except socket.timeout:
        latency = int((time.time() - start) * 1000)
        return "", "", latency, "timed out"
    except Exception as exc:
        latency = int((time.time() - start) * 1000)
        return "", "", latency, str(exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    if not raw_response:
        return "", "", latency, "empty reply from server"

    head, sep, body = raw_response.partition(b"\r\n\r\n")
    if not sep:
        # Not a valid HTTP response split.
        return "", raw_response.decode("utf-8", errors="replace"), latency, "invalid http response"

    first_line = head.split(b"\r\n", 1)[0].decode("iso-8859-1", errors="replace")
    status_match = re.match(r"HTTP/\d+(?:\.\d+)?\s+(\d{3})", first_line)
    if status_match:
        status_code = status_match.group(1)

    body_text = body.decode("utf-8", errors="replace")
    return status_code, body_text, latency, ""


def _test_xray_proxy(
    parsed_link: dict,
    config_builder,
    local_port=DEFAULT_PROXY_BASE_PORT,
    test_url=DEFAULT_TEST_URL,
    timeout_seconds=DEFAULT_CURL_TIMEOUT_SECONDS,
):
    proc = None
    config_path = None
    access_log_path = None
    error_log_path = None

    try:
        safe_name = parsed_link["name"] or parsed_link["server"] or "node"
        safe_name = "".join(
            c if c.isalnum() or c in ("-", "_") else "_" for c in safe_name
        )
        safe_name = f"{safe_name}_{local_port}"

        access_log_path = LOG_DIR / f"{safe_name}_access.log"
        error_log_path = LOG_DIR / f"{safe_name}_error.log"
        access_log_path.write_text("", encoding="utf-8")
        error_log_path.write_text("", encoding="utf-8")

        config = config_builder(parsed_link, access_log_path, error_log_path, local_port)

        with tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".json") as f:
            json.dump(config, f, indent=2)
            f.flush()
            config_path = f.name

        proc = subprocess.Popen(
            ["xray", "run", "-c", config_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        time.sleep(2)

        if proc.poll() is not None:
            return (
                False,
                None,
                {
                    "reason": "xray exited early",
                    "xray_error": read_text_if_exists(error_log_path),
                    "status_code": "",
                    "exit_ip": "",
                    "partial": False,
                },
            )

        status_code, response_body, latency, probe_error = _http_probe_via_socks(
            test_url=test_url,
            proxy_host="127.0.0.1",
            proxy_port=int(local_port),
            timeout_seconds=timeout_seconds,
        )
        xray_error = read_text_if_exists(error_log_path)
        exit_ip = extract_exit_ip(response_body)
        tunnel_established = has_tunnel_established(xray_error)

        if status_code.startswith(("2", "3")):
            return (
                True,
                latency,
                {
                    "reason": _humanize_reason(probe_error, status_code=status_code),
                    "xray_error": xray_error,
                    "status_code": status_code,
                    "exit_ip": exit_ip,
                    "partial": False,
                },
            )

        reason = _humanize_reason(
            probe_error or f"http_status={status_code or 'none'}",
            status_code=status_code,
        )
        if tunnel_established:
            return (
                False,
                latency,
                {
                    "reason": reason,
                    "xray_error": xray_error,
                    "status_code": status_code,
                    "exit_ip": exit_ip,
                    "partial": True,
                },
            )

        return (
            False,
            latency,
            {
                "reason": reason,
                "xray_error": xray_error,
                "status_code": status_code,
                "exit_ip": exit_ip,
                "partial": False,
            },
        )

    except FileNotFoundError as exc:
        reason = "xray not found in PATH" if (exc.filename or "") == "xray" else repr(exc)
        xray_error = read_text_if_exists(error_log_path) if error_log_path else ""
        return (
            False,
            None,
            {
                "reason": _humanize_reason(reason),
                "xray_error": xray_error,
                "status_code": "",
                "exit_ip": "",
                "partial": False,
            },
        )
    except Exception as exc:
        xray_error = read_text_if_exists(error_log_path) if error_log_path else ""
        return (
            False,
            None,
            {
                "reason": _humanize_reason(repr(exc)),
                "xray_error": xray_error,
                "status_code": "",
                "exit_ip": "",
                "partial": False,
            },
        )

    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)

        if config_path and os.path.exists(config_path):
            os.remove(config_path)


def test_vless(
    vless_url,
    local_port=DEFAULT_PROXY_BASE_PORT,
    test_url=DEFAULT_TEST_URL,
    timeout_seconds=DEFAULT_CURL_TIMEOUT_SECONDS,
):
    try:
        parsed = parse_vless(vless_url)
    except Exception as exc:
        return (
            False,
            None,
            {
                "reason": f"invalid vless url: {exc}",
                "xray_error": "",
                "status_code": "",
                "exit_ip": "",
                "partial": False,
            },
        )
    if parsed.get("parse_error"):
        return (
            False,
            None,
            {
                "reason": f"invalid vless url: {parsed['parse_error']}",
                "xray_error": "",
                "status_code": "",
                "exit_ip": "",
                "partial": False,
            },
        )
    return _test_xray_proxy(
        parsed,
        generate_config,
        local_port=local_port,
        test_url=test_url,
        timeout_seconds=timeout_seconds,
    )


def test_vmess(
    vmess_url,
    local_port=DEFAULT_PROXY_BASE_PORT,
    test_url=DEFAULT_TEST_URL,
    timeout_seconds=DEFAULT_CURL_TIMEOUT_SECONDS,
):
    try:
        parsed = parse_vmess(vmess_url)
    except Exception as exc:
        return (
            False,
            None,
            {
                "reason": f"invalid vmess url: {exc}",
                "xray_error": "",
                "status_code": "",
                "exit_ip": "",
                "partial": False,
            },
        )
    return _test_xray_proxy(
        parsed,
        generate_vmess_config,
        local_port=local_port,
        test_url=test_url,
        timeout_seconds=timeout_seconds,
    )


def test_socks(
    config: dict,
    test_url=DEFAULT_TEST_URL,
    timeout_seconds=DEFAULT_CURL_TIMEOUT_SECONDS,
):
    server = config.get("server", "")
    port = config.get("port")
    if not server or not port:
        return (
            False,
            None,
            {
                "reason": "missing server/port",
                "xray_error": "",
                "status_code": "",
                "exit_ip": "",
                "partial": False,
            },
        )

    user = str(config.get("username", "") or "")
    password = str(config.get("password", "") or "")
    status_code, response_body, latency, probe_error = _http_probe_via_socks(
        test_url=test_url,
        proxy_host=server,
        proxy_port=int(port),
        timeout_seconds=timeout_seconds,
        username=user,
        password=password,
    )
    exit_ip = extract_exit_ip(response_body)

    if status_code.startswith(("2", "3")):
        return (
            True,
            latency,
            {
                "reason": _humanize_reason(probe_error, status_code=status_code),
                "xray_error": "",
                "status_code": status_code,
                "exit_ip": exit_ip,
                "partial": False,
            },
        )

    return (
        False,
        latency,
        {
            "reason": _humanize_reason(
                probe_error or f"http_status={status_code or 'none'}",
                status_code=status_code,
            ),
            "xray_error": "",
            "status_code": status_code,
            "exit_ip": exit_ip,
            "partial": False,
        },
    )


def test_mtproto(
    config: dict,
    timeout_seconds=DEFAULT_CURL_TIMEOUT_SECONDS,
):
    server = config.get("server", "")
    port = config.get("port")
    secret = (config.get("secret", "") or "").strip()
    if not server or not port:
        return (
            False,
            None,
            {
                "reason": "missing server/port",
                "xray_error": "",
                "status_code": "",
                "exit_ip": "",
                "partial": False,
            },
        )
    if not secret:
        return (
            False,
            None,
            {
                "reason": "missing secret",
                "xray_error": "",
                "status_code": "",
                "exit_ip": "",
                "partial": False,
            },
        )

    start = time.time()
    try:
        with socket.create_connection((server, int(port)), timeout=timeout_seconds):
            latency = int((time.time() - start) * 1000)
    except Exception as exc:
        return (
            False,
            None,
            {
                "reason": _humanize_reason(str(exc)),
                "xray_error": "",
                "status_code": "",
                "exit_ip": "",
                "partial": False,
            },
        )

    # MTProto needs Telegram-specific handshake; TCP-open check is useful but not definitive.
    return (
        False,
        latency,
        {
            "reason": "TCP reachable; MTProto handshake not validated",
            "xray_error": "",
            "status_code": "",
            "exit_ip": "",
            "partial": True,
        },
    )


def test_dns(
    config: dict,
    timeout_seconds=DEFAULT_CURL_TIMEOUT_SECONDS,
    dns_retries=DEFAULT_DNS_RETRIES,
    dns_retry_interval_ms=DEFAULT_DNS_RETRY_INTERVAL_MS,
):
    server = (config.get("server") or "").strip()
    port = config.get("port") or 53
    if not server:
        return (
            False,
            None,
            {
                "reason": "missing server",
                "xray_error": "",
                "status_code": "",
                "exit_ip": "",
                "partial": False,
            },
        )

    try:
        port = int(port)
    except Exception:
        return (
            False,
            None,
            {
                "reason": "invalid port",
                "xray_error": "",
                "status_code": "",
                "exit_ip": "",
                "partial": False,
            },
        )

    if not (1 <= port <= 65535):
        return (
            False,
            None,
            {
                "reason": "port out of range",
                "xray_error": "",
                "status_code": "",
                "exit_ip": "",
                "partial": False,
            },
        )

    try:
        dns_retries = int(dns_retries)
    except Exception:
        dns_retries = DEFAULT_DNS_RETRIES
    try:
        dns_retry_interval_ms = int(dns_retry_interval_ms)
    except Exception:
        dns_retry_interval_ms = DEFAULT_DNS_RETRY_INTERVAL_MS
    dns_retries = max(0, dns_retries)
    dns_retry_interval_ms = max(0, dns_retry_interval_ms)

    attempts = dns_retries + 1
    last_error_reason = ""

    for attempt in range(1, attempts + 1):
        query_name = "example.com"
        txid, payload = _build_dns_query(query_name)
        start = time.time()
        try:
            addr_infos = socket.getaddrinfo(
                server,
                port,
                type=socket.SOCK_DGRAM,
                proto=socket.IPPROTO_UDP,
            )
            if not addr_infos:
                raise OSError("unable to resolve dns server address")
            last_error: Exception | None = None
            response = b""
            for family, socktype, proto, _canonname, sockaddr in addr_infos:
                try:
                    with socket.socket(family, socktype, proto) as sock:
                        sock.settimeout(timeout_seconds)
                        sock.sendto(payload, sockaddr)
                        response, _ = sock.recvfrom(2048)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
            if last_error is not None:
                raise last_error
            latency = int((time.time() - start) * 1000)
        except socket.timeout:
            last_error_reason = _humanize_reason(
                f"dns query timed out after {timeout_seconds:g}s"
            )
            if attempt < attempts and dns_retry_interval_ms:
                time.sleep(dns_retry_interval_ms / 1000.0)
            continue
        except Exception as exc:
            last_error_reason = _humanize_reason(str(exc))
            if attempt < attempts and dns_retry_interval_ms:
                time.sleep(dns_retry_interval_ms / 1000.0)
            continue

        valid_response, rcode, answers = _parse_dns_response(txid, response)
        if not valid_response:
            last_error_reason = "invalid dns response packet"
            if attempt < attempts and dns_retry_interval_ms:
                time.sleep(dns_retry_interval_ms / 1000.0)
            continue

        if rcode == 0 and answers > 0:
            return (
                True,
                latency,
                {
                    "reason": "",
                    "xray_error": "",
                    "status_code": "dns-ok",
                    "exit_ip": "",
                    "partial": False,
                },
            )

        # Resolver is reachable but returned non-success payload.
        return (
            False,
            latency,
            {
                "reason": f"dns responded with rcode={rcode}, answers={answers}",
                "xray_error": "",
                "status_code": f"dns-rcode-{rcode}",
                "exit_ip": "",
                "partial": True,
            },
        )

    return (
        False,
        None,
        {
            "reason": f"{last_error_reason} (attempts={attempts})",
            "xray_error": "",
            "status_code": "",
            "exit_ip": "",
            "partial": False,
        },
    )


def test_link(
    link: str,
    local_port=DEFAULT_PROXY_BASE_PORT,
    test_url=DEFAULT_TEST_URL,
    timeout_seconds=DEFAULT_CURL_TIMEOUT_SECONDS,
    dns_retries=DEFAULT_DNS_RETRIES,
    dns_retry_interval_ms=DEFAULT_DNS_RETRY_INTERVAL_MS,
):
    parsed = parse_link(link)
    protocol = (parsed.get("protocol") or "").lower()

    if protocol == "vless":
        return test_vless(
            link,
            local_port=local_port,
            test_url=test_url,
            timeout_seconds=timeout_seconds,
        )
    if protocol == "vmess":
        return test_vmess(
            link,
            local_port=local_port,
            test_url=test_url,
            timeout_seconds=timeout_seconds,
        )
    if protocol == "socks":
        return test_socks(
            parsed,
            test_url=test_url,
            timeout_seconds=timeout_seconds,
        )
    if protocol == "mtproto":
        return test_mtproto(parsed, timeout_seconds=timeout_seconds)
    if protocol == "dns":
        return test_dns(
            parsed,
            timeout_seconds=timeout_seconds,
            dns_retries=dns_retries,
            dns_retry_interval_ms=dns_retry_interval_ms,
        )

    return (
        False,
        None,
        {
            "reason": f"unsupported link type: {protocol or 'unknown'}",
            "xray_error": "",
            "status_code": "",
            "exit_ip": "",
            "partial": False,
        },
    )


def load_links(input_file: str) -> List[str]:
    path = Path(input_file)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")
    links: List[str] = []
    seen = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        link = line.strip()
        if not link or link in seen:
            continue
        seen.add(link)
        links.append(link)
    return links


def run_cli(links: Iterable[str]) -> None:
    for link in links:
        ok, latency, details = test_link(link)
        reason = details.get("reason", "")
        xray_error = details.get("xray_error", "")
        exit_ip = details.get("exit_ip", "")
        is_partial = details.get("partial", False)

        if ok:
            suffix = f" | exit_ip={exit_ip}" if exit_ip else ""
            print(f"✅ WORKING {latency}ms{suffix} -> {link}")
        elif is_partial:
            suffix = f" | exit_ip={exit_ip}" if exit_ip else ""
            shown_latency = f" {latency}ms" if latency is not None else ""
            print(f"⚠️ PARTIAL{shown_latency}{suffix} -> {link}")
            if reason:
                print(f"   reason: {reason}")
            if xray_error:
                print("   xray log tail:")
                lines = [
                    line for line in xray_error.strip().splitlines() if line.strip()
                ]
                for line in lines[-8:]:
                    print(f"     {line}")
        else:
            print(f"❌ FAILED -> {link}")
            if reason:
                print(f"   reason: {reason}")
            if xray_error:
                print("   xray log tail:")
                lines = [
                    line for line in xray_error.strip().splitlines() if line.strip()
                ]
                for line in lines[-8:]:
                    print(f"     {line}")

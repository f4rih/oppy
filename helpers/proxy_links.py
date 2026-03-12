import urllib.parse
import re
from typing import Any

from .vless import parse_vless
from .vmess import parse_vmess


def _get_qs_value(params: dict[str, list[str]], key: str, default: str = "") -> str:
    return params.get(key, [default])[0] or default


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _safe_url_port(parsed: urllib.parse.ParseResult) -> int | None:
    try:
        return parsed.port
    except ValueError:
        return None


def _build_dns_config(server: str, port: int, source: str) -> dict[str, Any]:
    return {
        "protocol": "dns",
        "name": "DNS Resolver",
        "server": server,
        "port": port,
        "type": "udp",
        "security": "none",
        "sni": "",
        "flow": "",
        "source": source,
    }


def _parse_dns(raw_url: str) -> dict[str, Any] | None:
    raw = raw_url.strip()
    if raw.lower().startswith("udp://"):
        parsed = urllib.parse.urlparse(raw)
        server = (parsed.hostname or "").strip()
        if not server:
            return None
        try:
            port = parsed.port or 53
        except ValueError:
            return None
        if not (1 <= int(port) <= 65535):
            return None
        return _build_dns_config(server, int(port), "dns")

    if "://" in raw:
        return None

    match = re.fullmatch(r"(?P<server>[A-Za-z0-9.-]+):(?P<port>\d{1,5})", raw)
    if not match:
        return None
    port = int(match.group("port"))
    if not (1 <= port <= 65535):
        return None
    return _build_dns_config(match.group("server"), port, "dns")


def _parse_telegram_like_proxy(path: str, query: str) -> dict[str, Any] | None:
    params = urllib.parse.parse_qs(query)
    server = _get_qs_value(params, "server")
    port = _parse_int(_get_qs_value(params, "port"))

    if not server:
        return None

    if path == "socks":
        user = _get_qs_value(params, "user")
        password = _get_qs_value(params, "pass")
        return {
            "protocol": "socks",
            "name": "Telegram SOCKS",
            "server": server,
            "port": port,
            "type": "socks5",
            "security": "auth" if (user or password) else "none",
            "sni": "",
            "flow": "",
            "username": user,
            "password": password,
            "source": "telegram",
        }

    if path == "proxy":
        return {
            "protocol": "mtproto",
            "name": "Telegram MTProto",
            "server": server,
            "port": port,
            "type": "tcp",
            "security": "secret",
            "sni": "",
            "flow": "",
            "secret": _get_qs_value(params, "secret"),
            "source": "telegram",
        }

    return None


def parse_link(url: str) -> dict[str, Any]:
    raw = url.strip()
    dns_config = _parse_dns(raw)
    if dns_config is not None:
        return dns_config

    parsed = urllib.parse.urlparse(raw)

    if parsed.scheme == "vless":
        try:
            return parse_vless(raw)
        except Exception as exc:
            return {
                "protocol": "vless",
                "name": "",
                "server": parsed.hostname or "",
                "port": _safe_url_port(parsed),
                "type": "",
                "security": "",
                "sni": "",
                "flow": "",
                "source": "vless",
                "parse_error": f"invalid vless url: {exc}",
            }
    if parsed.scheme == "vmess":
        try:
            return parse_vmess(raw)
        except Exception:
            return {
                "protocol": "vmess",
                "name": "",
                "server": "",
                "port": None,
                "type": "",
                "security": "",
                "sni": "",
                "flow": "",
                "source": "vmess",
            }

    host = (parsed.hostname or "").lower()
    path = parsed.path.strip("/").lower()
    if parsed.scheme in ("http", "https") and host in ("t.me", "telegram.me"):
        tg_link = _parse_telegram_like_proxy(path, parsed.query)
        if tg_link is not None:
            return tg_link
    if parsed.scheme == "tg" and (parsed.netloc or "").lower() == "proxy":
        tg_link = _parse_telegram_like_proxy("proxy", parsed.query)
        if tg_link is not None:
            return tg_link

    return {
        "protocol": parsed.scheme or "unknown",
        "name": "",
        "server": parsed.hostname or "",
        "port": _safe_url_port(parsed),
        "type": "",
        "security": "",
        "sni": "",
        "flow": "",
        "source": "unknown",
    }

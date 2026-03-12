import base64
import json
import urllib.parse

from .constants import DEFAULT_PROXY_BASE_PORT
from .vless import build_stream_settings


def _parse_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _decode_vmess_payload(payload: str) -> dict:
    text = urllib.parse.unquote(payload.strip())
    padding = "=" * (-len(text) % 4)
    raw = text + padding
    errors = []

    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            decoded = decoder(raw)
            return json.loads(decoded.decode("utf-8"))
        except Exception as exc:
            errors.append(exc)

    raise ValueError(f"invalid vmess payload: {errors[-1] if errors else 'decode error'}")


def parse_vmess(url: str) -> dict:
    raw = url.strip()
    if raw.startswith("vmess://"):
        payload = raw[len("vmess://") :]
    else:
        parsed = urllib.parse.urlparse(raw)
        payload = f"{parsed.netloc}{parsed.path}"

    payload_no_fragment, _, _ = payload.partition("#")
    decoded = _decode_vmess_payload(payload_no_fragment)

    name = urllib.parse.unquote(urllib.parse.urlparse(raw).fragment) or decoded.get("ps", "")
    security = (decoded.get("tls") or "none").strip() or "none"
    network = (decoded.get("net") or "tcp").strip() or "tcp"

    return {
        "protocol": "vmess",
        "uuid": decoded.get("id", ""),
        "server": decoded.get("add", ""),
        "port": _parse_int(decoded.get("port"), 0),
        "name": name,
        "type": network,
        "security": security,
        "host": decoded.get("host", ""),
        "path": urllib.parse.unquote(decoded.get("path", "")),
        "sni": decoded.get("sni", "") or decoded.get("serverName", ""),
        "alpn": decoded.get("alpn", ""),
        "fp": decoded.get("fp", ""),
        "headerType": decoded.get("type", ""),
        "flow": decoded.get("flow", ""),
        "serviceName": decoded.get("serviceName", ""),
        "alterId": _parse_int(decoded.get("aid"), 0),
        "cipher": decoded.get("scy", "auto") or "auto",
    }


def generate_vmess_config(
    v, access_log_path, error_log_path, local_port=DEFAULT_PROXY_BASE_PORT
):
    stream = build_stream_settings(v)

    outbound = {
        "protocol": "vmess",
        "settings": {
            "vnext": [
                {
                    "address": v["server"],
                    "port": v["port"],
                    "users": [
                        {
                            "id": v["uuid"],
                            "alterId": int(v.get("alterId", 0)),
                            "security": v.get("cipher") or "auto",
                        }
                    ],
                }
            ]
        },
        "streamSettings": stream,
    }

    config = {
        "log": {
            "loglevel": "debug",
            "access": str(access_log_path),
            "error": str(error_log_path),
        },
        "inbounds": [
            {
                "port": local_port,
                "listen": "127.0.0.1",
                "protocol": "socks",
                "settings": {"auth": "noauth"},
            }
        ],
        "outbounds": [outbound],
    }

    return config

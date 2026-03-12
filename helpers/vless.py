import urllib.parse

from .constants import DEFAULT_PROXY_BASE_PORT


def _safe_port(parsed: urllib.parse.ParseResult) -> int | None:
    try:
        return parsed.port
    except ValueError:
        return None


def parse_vless(url):
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)

    def get(key, default=None):
        return params.get(key, [default])[0]

    config = {
        "protocol": parsed.scheme or "vless",
        "uuid": parsed.username,
        "server": parsed.hostname,
        "port": _safe_port(parsed),
        "name": urllib.parse.unquote(parsed.fragment),
        "type": get("type", "tcp"),
        "security": get("security", "none"),
        "host": get("host", ""),
        "path": urllib.parse.unquote(get("path", "")),
        "sni": get("sni", ""),
        "alpn": get("alpn", ""),
        "fp": get("fp", ""),
        "headerType": get("headerType", ""),
        "flow": get("flow", ""),
        "serviceName": get("serviceName", ""),
        "encryption": get("encryption", "none"),
        "publicKey": get("pbk", ""),
        "shortId": get("sid", ""),
        "spiderX": urllib.parse.unquote(get("spx", "")),
    }

    if config["port"] is None:
        config["parse_error"] = "invalid or missing port"
    if not config["server"]:
        config["parse_error"] = config.get("parse_error", "missing server")
    if not config["uuid"]:
        config["parse_error"] = config.get("parse_error", "missing uuid")

    return config


def build_stream_settings(v):
    stream = {"network": v["type"], "security": v["security"]}

    if v["security"] == "tls":
        stream["tlsSettings"] = {}

        if v["sni"]:
            stream["tlsSettings"]["serverName"] = v["sni"]

        if v["alpn"]:
            stream["tlsSettings"]["alpn"] = v["alpn"].split(",")

        if v["fp"]:
            stream["tlsSettings"]["fingerprint"] = v["fp"]

    if v["security"] == "reality":
        stream["realitySettings"] = {}

        if v["sni"]:
            stream["realitySettings"]["serverName"] = v["sni"]

        if v["fp"]:
            stream["realitySettings"]["fingerprint"] = v["fp"]

        if v["publicKey"]:
            stream["realitySettings"]["publicKey"] = v["publicKey"]

        if v["shortId"]:
            stream["realitySettings"]["shortId"] = v["shortId"]

        spider_x = v["spiderX"] or v["path"]
        if spider_x:
            stream["realitySettings"]["spiderX"] = spider_x

    if v["type"] == "ws":
        stream["wsSettings"] = {"path": v["path"], "headers": {}}

        if v["host"]:
            stream["wsSettings"]["headers"]["Host"] = v["host"]

    if v["type"] == "tcp" and v["headerType"] == "http":
        stream["tcpSettings"] = {
            "header": {
                "type": "http",
                "request": {
                    "path": ["/"],
                    "headers": {"Host": [v["host"]] if v["host"] else []},
                },
            }
        }

    if v["type"] == "grpc":
        stream["grpcSettings"] = {"serviceName": v["serviceName"]}

    return stream


def generate_config(
    v, access_log_path, error_log_path, local_port=DEFAULT_PROXY_BASE_PORT
):
    stream = build_stream_settings(v)

    outbound = {
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": v["server"],
                    "port": v["port"],
                    "users": [
                        {
                            "id": v["uuid"],
                            "encryption": v["encryption"] or "none",
                        }
                    ],
                }
            ]
        },
        "streamSettings": stream,
    }

    if v["flow"]:
        outbound["settings"]["vnext"][0]["users"][0]["flow"] = v["flow"]

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

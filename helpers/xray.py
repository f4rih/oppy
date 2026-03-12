import json
import re
import shutil
import subprocess
from pathlib import Path


def read_text_if_exists(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def extract_exit_ip(response_body):
    try:
        data = json.loads(response_body)
    except Exception:
        return ""

    origin = data.get("origin", "")
    if not origin:
        return ""

    return str(origin).split(",")[0].strip()


def has_tunnel_established(xray_log_text):
    return "proxy/vless/outbound: tunneling request to" in xray_log_text


def _detect_binary(binary: str, version_args: list[str]) -> tuple[bool, str]:
    path = shutil.which(binary)
    if not path:
        return False, ""

    try:
        result = subprocess.run(
            [path, *version_args],
            capture_output=True,
            text=True,
            timeout=3,
        )
        output = (result.stdout or result.stderr or "").strip()
        for line in output.splitlines():
            match = re.search(r"\b(\d+\.\d+\.\d+(?:[-+.\w]*)?)\b", line)
            if match:
                return True, match.group(1)
    except Exception:
        pass

    return True, ""


def detect_xray() -> tuple[bool, str]:
    return _detect_binary("xray", ["version"])


def detect_curl() -> tuple[bool, str]:
    return _detect_binary("curl", ["--version"])

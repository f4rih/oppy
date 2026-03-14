import json
import os
import re
import shutil
import subprocess
import sys
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
    path = binary if Path(binary).exists() else shutil.which(binary)
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


def _is_executable(path: Path) -> bool:
    if not path.exists() or path.is_dir():
        return False
    if os.name == "nt":
        return True
    try:
        if os.access(path, os.X_OK):
            return True
        path.chmod(path.stat().st_mode | 0o111)
        return os.access(path, os.X_OK)
    except Exception:
        return False


def get_xray_binary() -> str:
    filename = "xray.exe" if os.name == "nt" else "xray"
    candidates: list[Path] = []

    env_path = os.environ.get("OPPY_XRAY_PATH", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())

    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            base = Path(meipass)
            candidates.append(base / "bin" / filename)
            candidates.append(base / filename)
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "bin" / filename)
        candidates.append(exe_dir / filename)

    candidates.append(Path.cwd() / "bin" / filename)
    candidates.append(Path.cwd() / filename)

    for candidate in candidates:
        candidate = candidate.resolve()
        if _is_executable(candidate):
            return str(candidate)

    found = shutil.which("xray")
    return found or ""


def detect_xray() -> tuple[bool, str]:
    xray_bin = get_xray_binary()
    if not xray_bin:
        return False, ""
    return _detect_binary(xray_bin, ["version"])


def detect_curl() -> tuple[bool, str]:
    return _detect_binary("curl", ["--version"])

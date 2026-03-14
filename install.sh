#!/usr/bin/env bash
set -euo pipefail

REPO_OWNER="${OPPY_REPO_OWNER:-f4rih}"
REPO_NAME="${OPPY_REPO_NAME:-oppy}"
REPO_BRANCH="${OPPY_REPO_BRANCH:-main}"
REMOTE_ZIP_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}/archive/refs/heads/${REPO_BRANCH}.zip"
PYTHON_BIN="${PYTHON_BIN:-python3}"

say() {
  printf '[OPPY] %s\n' "$*"
}

fail() {
  printf '[OPPY] ERROR: %s\n' "$*" >&2
  exit 1
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

ensure_cmd() {
  local cmd="$1"
  local hint="${2:-Install ${cmd} and run this script again.}"
  have_cmd "$cmd" || fail "$hint"
}

detect_platform() {
  case "$(uname -s)" in
    Darwin) echo "macos" ;;
    Linux) echo "linux" ;;
    *) fail "Unsupported OS. This installer currently supports Linux and macOS only." ;;
  esac
}

ensure_python_pip() {
  ensure_cmd "$PYTHON_BIN" "Python 3 is required (missing: ${PYTHON_BIN})."
  if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
    say "pip not found for ${PYTHON_BIN}; trying ensurepip..."
    "$PYTHON_BIN" -m ensurepip --upgrade >/dev/null 2>&1 || fail "pip is missing and ensurepip failed."
  fi
}

install_xray() {
  local platform="$1"
  if have_cmd xray; then
    say "xray already installed: $(xray version 2>/dev/null | head -n 1 || echo 'unknown version')"
    return
  fi

  if [[ "$platform" == "macos" ]]; then
    fail "xray is not installed. Please install xray manually on macOS, ensure it's in PATH, then rerun this script."
  else
    say "Installing xray..."
    # Prefer distro package on Debian/Ubuntu when available.
    if have_cmd apt-get; then
      say "Trying apt package: xray-core"
      if have_cmd sudo; then
        if sudo apt-get update && sudo apt-get install -y xray-core; then
          if have_cmd xray; then
            say "xray installed via apt: $(xray version 2>/dev/null | head -n 1 || echo 'unknown version')"
            return
          fi
        fi
      else
        if apt-get update && apt-get install -y xray-core; then
          if have_cmd xray; then
            say "xray installed via apt: $(xray version 2>/dev/null | head -n 1 || echo 'unknown version')"
            return
          fi
        fi
      fi
      say "apt install did not provide xray command; falling back to official installer."
    fi

    ensure_cmd curl "curl is required to install xray."
    if have_cmd sudo; then
      sudo bash -c "$(curl -fsSL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
    else
      bash -c "$(curl -fsSL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
    fi
  fi

  have_cmd xray || fail "xray installation finished but xray is still not found in PATH."
  say "xray installed: $(xray version 2>/dev/null | head -n 1 || echo 'unknown version')"
}

install_oppy_python() {
  ensure_python_pip

  local source_target
  if [[ -f "./pyproject.toml" && -f "./oppy.py" ]]; then
    say "Using local repository: $(pwd)"
    source_target=".[clipboard]"
  else
    ensure_cmd curl "curl is required."
    ensure_cmd unzip "unzip is required."
    local tmpdir
    tmpdir="$(mktemp -d)"
    trap 'rm -rf "$tmpdir"' EXIT
    say "Downloading source from ${REMOTE_ZIP_URL}"
    curl -fsSL "$REMOTE_ZIP_URL" -o "${tmpdir}/oppy.zip"
    unzip -q "${tmpdir}/oppy.zip" -d "$tmpdir"
    local extracted
    extracted="$(find "$tmpdir" -mindepth 1 -maxdepth 1 -type d -name "${REPO_NAME}-*" | head -n 1)"
    [[ -n "$extracted" ]] || fail "Could not locate extracted source directory."
    source_target="${extracted}[clipboard]"
  fi

  say "Installing OPPY with pip (user scope)..."
  "$PYTHON_BIN" -m pip install --user --upgrade pip setuptools wheel
  "$PYTHON_BIN" -m pip install --user --upgrade "$source_target"
}

print_path_hint() {
  local user_bin
  user_bin="$("$PYTHON_BIN" - <<'PY'
import site
print(site.USER_BASE + "/bin")
PY
)"
  if ! have_cmd oppy; then
    say "OPPY was installed, but 'oppy' is not in your PATH."
    say "Add this line to your shell profile (~/.zshrc or ~/.bashrc):"
    printf 'export PATH="%s:$PATH"\n' "$user_bin"
  fi
}

main() {
  local platform
  platform="$(detect_platform)"
  say "Detected platform: ${platform}"

  install_xray "$platform"
  install_oppy_python
  print_path_hint

  say "Install complete."
  say "Run: oppy --help"
}

main "$@"

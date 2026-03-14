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

run_pip_install() {
  local args=("$@")
  if "$PYTHON_BIN" -m pip "${args[@]}"; then
    return 0
  fi

  # Debian/Ubuntu (PEP 668): allow user-scope installs when pip enforces externally-managed env.
  if "$PYTHON_BIN" -m pip --help 2>/dev/null | grep -q -- '--break-system-packages'; then
    say "Retrying pip with --break-system-packages (externally managed Python environment detected)."
    "$PYTHON_BIN" -m pip --break-system-packages "${args[@]}"
    return $?
  fi

  return 1
}

linux_xray_asset_name() {
  case "$(uname -m)" in
    x86_64|amd64) echo "Xray-linux-64.zip" ;;
    aarch64|arm64) echo "Xray-linux-arm64-v8a.zip" ;;
    armv7l|armv7) echo "Xray-linux-arm32-v7a.zip" ;;
    i386|i686) echo "Xray-linux-32.zip" ;;
    *) return 1 ;;
  esac
}

install_xray_manual_linux() {
  local asset
  asset="$(linux_xray_asset_name)" || {
    say "Manual xray fallback: unsupported architecture $(uname -m)."
    return 1
  }

  ensure_cmd curl "curl is required to install xray."
  ensure_cmd unzip "unzip is required to install xray."

  local tmpdir
  tmpdir="$(mktemp -d)"
  local zip_path="${tmpdir}/xray.zip"
  local download_url="https://github.com/XTLS/Xray-core/releases/latest/download/${asset}"
  local install_dir="/usr/local/bin"

  say "Manual xray fallback: downloading ${asset}"
  if ! curl -fsSL "$download_url" -o "$zip_path"; then
    rm -rf "$tmpdir"
    return 1
  fi
  if ! unzip -q -o "$zip_path" xray -d "$tmpdir"; then
    rm -rf "$tmpdir"
    return 1
  fi

  if [[ -w "$install_dir" ]]; then
    install -m 0755 "${tmpdir}/xray" "${install_dir}/xray"
  elif have_cmd sudo; then
    sudo install -m 0755 "${tmpdir}/xray" "${install_dir}/xray"
  else
    install_dir="${HOME}/.local/bin"
    mkdir -p "$install_dir"
    install -m 0755 "${tmpdir}/xray" "${install_dir}/xray"
    export PATH="${install_dir}:$PATH"
    say "Installed xray to ${install_dir}. Make sure it is in PATH."
  fi

  rm -rf "$tmpdir"
  return 0
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
    local official_ok=0
    if have_cmd sudo; then
      if sudo bash -c "$(curl -fsSL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install; then
        official_ok=1
      fi
    else
      if bash -c "$(curl -fsSL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install; then
        official_ok=1
      fi
    fi

    if [[ "$official_ok" -eq 0 ]] || ! have_cmd xray; then
      say "Official xray installer failed or xray not found. Trying manual binary fallback..."
      install_xray_manual_linux || fail "Manual xray fallback failed. Install xray manually, ensure it is in PATH, then rerun."
    fi
  fi

  have_cmd xray || fail "xray installation finished but xray is still not found in PATH."
  say "xray installed: $(xray version 2>/dev/null | head -n 1 || echo 'unknown version')"
}

install_oppy_python() {
  ensure_python_pip

  local tmpdir=""
  local source_target
  if [[ -f "./pyproject.toml" && -f "./oppy.py" ]]; then
    say "Using local repository: $(pwd)"
    source_target=".[clipboard]"
  else
    ensure_cmd curl "curl is required."
    ensure_cmd unzip "unzip is required."
    tmpdir="$(mktemp -d)"
    say "Downloading source from ${REMOTE_ZIP_URL}"
    curl -fsSL "$REMOTE_ZIP_URL" -o "${tmpdir}/oppy.zip"
    unzip -q "${tmpdir}/oppy.zip" -d "$tmpdir"
    local extracted
    extracted="$(find "$tmpdir" -mindepth 1 -maxdepth 1 -type d -name "${REPO_NAME}-*" | head -n 1)"
    [[ -n "$extracted" ]] || fail "Could not locate extracted source directory."
    source_target="${extracted}[clipboard]"
  fi

  say "Installing OPPY with pip (user scope)..."
  run_pip_install install --user --upgrade pip setuptools wheel || say "Skipping pip toolchain upgrade."
  run_pip_install install --user --upgrade "$source_target" || fail "Failed to install OPPY with pip."

  if [[ -n "$tmpdir" ]]; then
    rm -rf "$tmpdir"
  fi
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

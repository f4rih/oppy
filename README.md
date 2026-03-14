# OPPY - Mission Control for Proxy Links

<p align="center">
  <img src="assets/oppy_logo_alt.svg" alt="OPPY Logo">
</p>

OPPY helps you validate proxy links quickly with live status, latency, export tools, and per-row diagnostics.

Supported link types:

- `vless://...`
- `vmess://...`
- Telegram SOCKS (`https://t.me/socks?...`)
- Telegram MTProto (`https://t.me/proxy?...`, `tg://proxy?...`)
- DNS resolvers (`udp://ip[:port]`, `ip:port`)

## Requirements

- Python 3.10+
- `xray` installed and available in `PATH` (required for VLESS / VMESS checks)

---

## Install Xray

### macOS (Homebrew)

```bash
brew install xray
```

### Ubuntu / Debian (APT)

```bash
sudo apt update
sudo apt install -y xray-core
```

If `xray-core` is unavailable in your configured repositories, use the official install method from the Xray project and ensure `xray` is in `PATH`.

### Windows

1. Download Xray release zip from the official project.
2. Extract `xray.exe`.
3. Add its folder to `PATH` in System Environment Variables.
4. Open a new terminal and verify:

```powershell
xray version
```

---

## Install OPPY

### One-liner (Linux / macOS)

```bash
curl -fsSL https://raw.githubusercontent.com/f4rih/oppy/main/install.sh | bash
```

What this script does:

- installs `xray` (via Homebrew on macOS or official installer on Linux)
- installs OPPY with pip in user scope
- installs optional clipboard support (`pyperclip`)
- prints PATH hint if `oppy` command is not yet visible

### pip

From local source:

```bash
pip install .
```

Editable for development:

```bash
pip install -e .
```

With optional clipboard support:

```bash
pip install ".[clipboard]"
```

### uv

From local source:

```bash
uv pip install .
```

Editable:

```bash
uv pip install -e .
```

Tool-style install:

```bash
uv tool install .
```

### Homebrew

This repo includes a formula template at `Formula/oppy.rb`.

Recommended flow:

1. Publish a GitHub release archive (tag + tarball).
2. Update `Formula/oppy.rb` with real `url`, `sha256`, and `homepage`.
3. Add formula to your tap and install:

```bash
brew tap <you>/<tap>
brew install oppy
```

For full guide, see comments inside `Formula/oppy.rb`.

---

## Run

With input file:

```bash
oppy --input-file output.txt
```

Without file (import in app with `i`):

```bash
oppy
```

CLI mode:

```bash
oppy --input-file output.txt --no-tui
```

## Terminal Rendering Note (macOS)

On macOS, the built-in Terminal app may render some borders/buttons with visual artifacts.
For a cleaner UI, prefer a modern third-party terminal such as Ghostty, iTerm2, or WezTerm.

---
## Highlights

- Multi-type checks in one table: VLESS, VMESS, Telegram SOCKS, MTProto, DNS.
- Live status meters + latency trend with running updates.
- Filter modal (`f`) for type/name/server and drop-matching records.
- Import modal (`i`) with paste/file tabs and clipboard load.
- Config modal (`c`) for concurrency, timeout, DNS retry settings, test URL, base port.
- Export modal (`e`) with type selection and optional partial export (green/orange only).
- Row details modal (`Enter`) with full payload and copy URL.

---

## Tutorial

See `TUTORIAL.md` for a step-by-step workflow.

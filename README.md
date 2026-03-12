# OPPY - Mission Control for Proxy Links

![OPPY Logo](assets/oppy_logo_alt.svg)

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

No external `curl` dependency is required anymore (HTTP probing is built-in Python).

---

## Install

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

---

## Highlights

- Live table with status, latency, exit IP, reason.
- Status meters + latency trend visualization.
- Config modal for concurrency, timeout, DNS retry settings, test URL, base port.
- Import links by paste or file browser.
- Export healthy links with optional filtered partial export.
- Details modal per row + URL copy.

---

## Assets

![OPPY Icon](assets/oppy_icon.svg)

---

## Tutorial

See `TUTORIAL.md` for a step-by-step workflow.

# OPPY Tutorial

This guide walks through a practical workflow for daily use.

## 1) Install prerequisites

- Install Python 3.10+
- Install `xray` and ensure `xray version` works in terminal

Quick one-go install (Linux / macOS):

```bash
curl -fsSL https://raw.githubusercontent.com/f4rih/oppy/main/install.sh | bash
```

This one-liner installs both Xray and OPPY.

### Install Xray by operating system

#### macOS

```bash
brew install xray
xray version
```

#### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y xray-core
xray version
```

If `xray-core` is unavailable in your repositories, use the official installer script from Xray project and ensure `xray` is in `PATH`.

#### Windows

1. Download the Windows ZIP from Xray-core releases.
2. Extract it and keep `xray.exe` in a folder in your system `PATH` (or next to your app binary).
3. Verify:

```powershell
xray version
```

Install OPPY from source:

```bash
pip install .
```

## 2) Start OPPY

Option A: start with an input file:

```bash
oppy --input-file output.txt
```

Option B: start empty and import in app:

```bash
oppy
```

## 3) Import links

Press `i` to open **Import Links** modal.

You can:

- Paste links directly in **Paste Links** tab
- Use **From File** tab and pick a text file
- In Paste tab, click **Load Clipboard** to pull clipboard text

Press **Import**.

## 4) Configure checks

Press `c` to open runtime config and adjust:

- Concurrency
- Request timeout
- DNS retries / retry interval
- Test URL
- Xray local base port

Press Enter (or Save) to apply.

## 5) Run / pause / stop

- `s` -> Start/Stop checks
- `p` -> Pause/Resume
- `a` -> Toggle auto-follow row

Useful shortcuts:

- `f` -> Filter links (type/name/server), with Reset and Drop Matches
- `r` -> Reset scan (clear table + loaded links)

## 6) Inspect details

Select a row and press `Enter`:

- Full parsed details
- Reason
- URL copy button (`c` also copies URL in details modal)

## 7) Export results

Press `e`:

- Choose output directory + filename
- Optional partial export checkbox

Partial export currently includes only partial links in green/orange latency zone.

## 8) Logs and terminal

- `l` -> live logs modal
- `t` -> temporary shell access, type `exit` to return

## 9) Troubleshooting

- `xray exited early`:
  - link config is invalid/incompatible, or xray cannot start
- `TLS/SSL handshake failed`:
  - usually SNI/TLS/fingerprint mismatch or blocked endpoint
- `Connection timed out while testing this link`:
  - unreachable host / very slow route / blocked network path

## 10) Daily usage pattern

1. Import new links (`i`)
2. Optional filter (`f`) to focus the list or drop unwanted records
3. Run checks (`s`)
4. Review partial/fail reasons
5. Export healthy (`e`)

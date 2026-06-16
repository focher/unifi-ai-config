# 🛡️ UniFi AI Config Auditor

A self-contained, cross-platform **desktop application** (macOS / Windows / Linux) that
uses a **configurable LLM** to audit a Ubiquiti UniFi network for security issues. It
reads the **full controller configuration** (networks/VLANs, WLANs, firewall rules &
groups, port forwards, routing, traffic rules, device/client inventory, controller
settings) and **all traffic flows** (DPI application/category breakdowns, IPS/IDS
events, alarms), then has an LLM of your choice analyze everything for known and
potential security problems.

The app runs in a **native desktop window** — there is no browser tab and no external
dependencies. Findings are categorized **Critical / High / Medium / Low**, and for each
one you can choose to **Ignore**, **Remediate** (with step-by-step instructions or
one-click automation), or **Leave for later**.

## Download & run

Grab the latest build from the [**Releases**](https://github.com/focher/unifi-ai-config/releases/latest)
page. Each download is a standalone application — no Python or other dependencies
required.

### macOS — `unifi-ai-auditor-macos.dmg`

Universal (Apple Silicon + Intel), delivered as a `.app` inside a DMG.

1. Open the DMG and drag **UniFi AI Config Auditor.app** into **Applications**.
2. The app is **ad-hoc signed** but not notarized, so Gatekeeper quarantines it on
   first launch. **Remove the quarantine flag** (run this once in Terminal):

   ```bash
   xattr -dr com.apple.quarantine "/Applications/UniFi AI Config Auditor.app"
   ```

3. Launch it. (If you skip step 2, you can instead **right-click the app → Open →
   Open** the first time.)

The app opens its own window. To quit, close the window or right-click its Dock icon → Quit.

### Windows — `unifi-ai-auditor-windows.exe`

A single self-contained executable. On first run Windows SmartScreen may warn:
click **More info → Run anyway**.

### Linux — `unifi-ai-auditor-linux`

A single self-contained binary:

```bash
chmod +x ./unifi-ai-auditor-linux
./unifi-ai-auditor-linux
```

The native window uses system **WebKitGTK**; if it isn't installed the app falls back
to opening the UI in your default browser.

## Features

- **Native desktop window** — runs completely self-contained (WKWebView on macOS,
  WebView2 on Windows, WebKitGTK on Linux); falls back to the browser if no webview
  backend is available.
- **Full config + traffic ingestion** directly from the UniFi Network controller API
  (works with UniFi OS consoles — UDM/UDM-Pro/Cloud Key Gen2+/Dream Machine — and
  classic self-hosted controllers). **MFA-aware login** and self-signed certs handled.
- **Two-step workflow** — *Collect* the configuration & traffic into a reusable
  snapshot (with live progress), then *Analyze* it. Analysis is **chunked** so even a
  small local model isn't overwhelmed.
- **Choose what to analyze** — add/remove individual collected sections (with Add all /
  Remove all) so only the sections you pick are sent to the LLM.
- **Browse / copy / download** every collected section (firewall rules, WLANs, DPI
  traffic flows, devices, …) as JSON — per section or the whole snapshot at once — with
  a **Redact secrets** toggle.
- **Any LLM you want:**
  - Cloud: **Anthropic (Claude)**, **OpenAI**, **Google (Gemini)**
  - Local / private: **Ollama** and **LM Studio** (auto-detects installed models)
- **Severity triage UI** with per-issue dispositions (Ignore / Remediate / Later),
  notes, evidence, and remediation steps.
- **Optional automated remediation** — when the LLM proposes a safe, structured change,
  one click applies it to the controller (always behind a confirmation prompt).
- **Secrets never leave your machine to the LLM** — passwords/PSKs/keys are redacted
  from the payload before analysis; credentials are stored locally only.

## Using the app

1. Open **Settings**, enter your UniFi controller host (e.g. `https://10.0.0.1`), a
   **read-only admin** account, and site. Click **Test Connection** (enter your MFA
   code if prompted).
2. Choose an **LLM provider** and model. For cloud providers paste an API key; for
   Ollama/LM Studio set the base URL if it's not local and click **Detect local models**.
3. On the **Dashboard**, click **⬇ Collect from controller** (Step 1) — this pulls the
   config + traffic and saves a snapshot.
4. In **Step 2**, the collected sections are pre-selected; add/remove any you want, then
   click **🔍 Analyze selected**.
5. Click any finding to review evidence/remediation and set its disposition.

## Provider notes

| Provider  | API key | Base URL default            | Notes |
|-----------|---------|-----------------------------|-------|
| Ollama    | no      | `http://localhost:11434`    | `ollama serve` running (default) |
| LM Studio | no      | `http://localhost:1234`     | Start its local server |
| Anthropic | yes     | `https://api.anthropic.com` | e.g. `claude-opus-4-8` |
| OpenAI    | yes     | `https://api.openai.com`    | e.g. `gpt-4o` |
| Google    | yes     | `…/generativelanguage…`     | e.g. `gemini-2.5-pro` |

Local models keep the entire audit on your own hardware — nothing leaves the network.
For a **remote** Ollama, set the base URL to `http://<host>:11434` (note the **colon**
before the port — a malformed `…/11434` is auto-corrected) and make sure Ollama is
started with `OLLAMA_HOST=0.0.0.0` so it's reachable on the network.

## Run from source

```bash
# Python 3.10+
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python run.py            # opens the native window
python run.py --browser  # use the default browser instead
python run.py --no-browser   # headless: serve only (no window/browser)
```

If the default port (8765) is busy the launcher picks the next free one; if an instance
is already running it just refocuses it. Fatal startup errors are written to
`~/.unifi-ai-config/launch.log` (and shown in a dialog on macOS).

## Build standalone packages

Build a native package on each OS with PyInstaller (Python + all dependencies bundled):

```bash
pip install pyinstaller
python build.py                       # one self-contained binary in ./dist/
```

A local `python build.py` produces a binary for the host CPU. The release CI builds a
**universal2 (Apple Silicon + Intel)** macOS binary and packages it as a `.app` inside a
DMG ([`packaging/macos_package.sh`](packaging/macos_package.sh)), and produces
self-contained `.exe` / ELF binaries for Windows / Linux. Every `v*` tag triggers the
[release workflow](.github/workflows/release.yml).

## Code signing

The published binaries are **code-signed to the extent possible without paid
certificates**:

- **macOS** — ad-hoc signed (valid, not "damaged"); first-launch quarantine cleared via
  the `xattr` command above or right-click → Open.
- **Windows** — unsigned; SmartScreen → More info → Run anyway.
- **Linux** — no signing required to run.

The release workflow auto-signs with **real** certificates if you add these repo secrets
— no workflow edits needed:

| Platform | Secrets | Effect |
|----------|---------|--------|
| macOS    | `MACOS_CERTIFICATE_BASE64`, `MACOS_CERTIFICATE_PWD`, `MACOS_SIGN_IDENTITY` (+ `AC_NOTARY_USER`, `AC_NOTARY_PASSWORD`, `AC_NOTARY_TEAM_ID` for notarization) | Developer ID signature + Apple notarization |
| Windows  | `WINDOWS_CERTIFICATE_BASE64`, `WINDOWS_CERTIFICATE_PWD` | Authenticode signature via `signtool` |

## Data & security

- All settings and results are stored locally under `~/.unifi-ai-config/`. Credentials
  (UniFi password, LLM API key) are stored in plaintext in that directory — protect it
  with normal filesystem permissions; it never leaves your machine.
- The server **binds to loopback only** (`127.0.0.1`) and enforces a **Host-header
  allowlist**, so only `localhost` can reach the API — this blocks DNS-rebinding
  attacks from malicious web pages in your browser.
- Secrets (passwords, PSKs, keys) are **redacted from the data before it is sent to the
  LLM**. The masked placeholder is never round-tripped back into storage.
- Use a **read-only** UniFi admin account unless you intend to use auto-remediation,
  which requires write access.
- The app talks only to (a) your controller and (b) the LLM endpoint you configure.
- Review every auto-remediation action before confirming — it shows the exact API
  call that will be made.

### Security scanning

Every push and PR runs a CI security scan ([`.github/workflows/security.yml`](.github/workflows/security.yml)):
- **bandit** — static analysis of the Python code.
- **pip-audit** — dependency vulnerability audit against the advisory database.

Run them locally with:

```bash
pip install bandit pip-audit
bandit -r backend run.py build.py -ll
pip-audit -r requirements.txt
```

## Architecture

```
run.py                 launcher: serves the app + shows it in a native window
build.py               PyInstaller build (per-platform, universal2 on macOS)
packaging/             macOS .app + DMG packaging
backend/
  main.py              FastAPI routes + static serving + host-header guard
  unifi_client.py      UniFi Network API client (UniFi OS + classic, MFA)
  analyzer.py          payload shaping, redaction, chunked prompts, finding parsing
  llm/providers.py     unified cloud + local LLM client
  models.py            pydantic models
  storage.py           local JSON persistence (settings, snapshots, results)
frontend/              vanilla HTML/CSS/JS UI (no build step)
```

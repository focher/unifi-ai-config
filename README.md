# 🛡️ UniFi AI Config Auditor

A self-contained, cross-platform desktop app (macOS / Windows / Linux) that uses a
**configurable LLM** to audit a Ubiquiti UniFi network for security issues. It reads
the **full controller configuration** (networks/VLANs, WLANs, firewall rules &
groups, port forwards, routing, traffic rules, device/client inventory, controller
settings) and **all traffic flows** (DPI application/category breakdowns, IPS/IDS
events, alarms), then has an LLM of your choice analyze everything for known and
potential security problems.

Findings are categorized **Critical / High / Medium / Low**, and for each one you can
choose to **Ignore**, **Remediate** (with step-by-step instructions or one-click
automation), or **Leave for later**.

## Features

- **Full config + traffic ingestion** directly from the UniFi Network controller API
  (works with UniFi OS consoles — UDM/UDM-Pro/Cloud Key Gen2+/Dream Machine — and
  classic self-hosted controllers).
- **Browse / copy / download** every collected section (firewall rules, WLANs, DPI
  traffic flows, devices, …) as JSON — per section or the whole snapshot at once.
- **Any LLM you want:**
  - Cloud: **Anthropic (Claude)**, **OpenAI**, **Google (Gemini)**
  - Local / private: **Ollama** and **LM Studio** (auto-detects installed models)
- **Severity triage UI** with per-issue dispositions (Ignore / Remediate / Later),
  notes, evidence, and remediation steps.
- **Optional automated remediation** — when the LLM proposes a safe, structured change,
  one click applies it to the controller (always behind a confirmation prompt).
- **Secrets never leave your machine to the LLM** — passwords/PSKs/keys are redacted
  from the payload before analysis; credentials are stored locally only.

## Quick start

```bash
# 1. Install dependencies (Python 3.10+)
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Run — opens the UI in your browser at http://127.0.0.1:8765
python run.py
```

Then:
1. Open **Settings**, enter your UniFi controller host, a **read-only admin** account,
   and site. Click **Test Connection**.
2. Choose an **LLM provider** and model. For cloud providers paste an API key; for
   Ollama/LM Studio just make sure the runtime is running and click **Detect local models**.
3. Go to **Dashboard** and click **▶ Run Analysis**.
4. Click any finding to review evidence/remediation and set its disposition.

## Provider notes

| Provider  | API key | Base URL default            | Notes |
|-----------|---------|-----------------------------|-------|
| Anthropic | yes     | `https://api.anthropic.com` | e.g. `claude-opus-4-8` |
| OpenAI    | yes     | `https://api.openai.com`    | e.g. `gpt-4o` |
| Google    | yes     | `…/generativelanguage…`     | e.g. `gemini-2.5-pro` |
| Ollama    | no      | `http://localhost:11434`    | `ollama serve` running |
| LM Studio | no      | `http://localhost:1234`     | Start its local server |

Local models keep the entire audit on your own hardware — nothing leaves the network.

## Packaging into a standalone binary (optional)

To ship a true double-clickable app with no Python required, use PyInstaller on each OS:

```bash
pip install pyinstaller
pyinstaller --onefile --name unifi-ai-auditor \
  --add-data "frontend:frontend" run.py      # Windows: use "frontend;frontend"
```

The resulting binary in `dist/` runs on its build OS (macOS, Windows `.exe`, or a
Linux ELF binary). A local `python build.py` produces a binary for the host CPU only;
the release CI builds macOS for **both** architectures and `lipo`-merges them into a
single **universal2 (Apple Silicon + Intel)** binary.

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
run.py                 launcher (uvicorn + auto-open browser)
backend/
  main.py              FastAPI routes + static serving
  unifi_client.py      UniFi Network API client (UniFi OS + classic)
  analyzer.py          payload shaping, redaction, prompt, finding parsing
  llm/providers.py     unified cloud + local LLM client
  models.py            pydantic models
  storage.py           local JSON persistence
frontend/              vanilla HTML/CSS/JS UI (no build step)
```

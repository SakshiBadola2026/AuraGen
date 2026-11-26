# AuraGen — Insurance Proposal UI Demo

**Live Demo:** https://auragen-ycuq.onrender.com/

AuraGen is a privacy-first adaptive UI demo for a 25-field insurance proposal (plus consent declaration).
Its vanilla HTML/CSS/JavaScript frontend observes interaction friction and turns the form into a guided five-section wizard. FastAPI serves the page, auto-discovers form fields from `index.html`, generates Gemini field guidance and wizard shells over WebSockets, and runs anonymized AI underwriting on demo submission.

## Privacy and demo limitations

- **Do not enter real PAN, Aadhaar, health, financial, or contact details.**
  This is a UI demo, not an insurer or an application processing system.
- **Zero PII sent to Gemini:** The WebSocket sends only a friction event, cognitive-load score, and static form version. Gemini receives the auto-discovered field schema and trigger metadata for wizard/guide generation, and only anonymized risk attributes (never name, email, phone, address, PAN/ID, or nominee details) during demo underwriting assessment.
  - The wizard displays a provenance badge: live/cached Gemini output is labeled `Gemini Guided Shell`; fallback output says `Offline Safe Shell`.
- Submitting the form saves a local demo record in `data/applications.json` and shows a preliminary AI underwriting receipt. Nothing is transmitted or submitted to an insurer.
- Before collecting real data, add an authorized backend, authentication, consent and retention policies, encryption, audit controls, and the insurer's approved submission integration.

## Run locally (Windows PowerShell)

For the quickest launch, double-click `run.bat`. It creates `.venv` if needed, installs the requirements, and starts the app at `http://127.0.0.1:8765/`.

Alternatively, from the project directory in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$secureKey = Read-Host "Google GenAI API key" -AsSecureString
$env:GOOGLE_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
uvicorn server:app --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/`. Port 8765 is used because port 8000 was already occupied in the development workspace. Copy `env.example` to `env.env` to enable Gemini locally; without a key, the server uses its built-in fallback wizard and rules. Do not commit `env.env` or put real keys in source files.

## Deploy to a Python ASGI host (Render)

Use the host's native Python runtime (Python 3.12 or newer):

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn server:app --host 0.0.0.0 --port $PORT`
- Set `GEMINI_API_KEYS` (or `GOOGLE_API_KEY`) in the host's secret/environment-variable settings.
- Enable WebSocket proxying and HTTPS. The browser automatically switches to `wss:` on HTTPS deployments.
- Configure the platform health check to `GET /health`.

## Test the app

1. **Dropdown & field guidance:** Click or change any field or dropdown (e.g., **Insurance Type**) to view live Gemini AI guidance in the **Field Guidance** panel and inline card.
2. **Three-second hesitation:** Choose **Simulate Confusion (3s Pause)** and wait three seconds, or interact with a form field and then pause.
3. **Form friction:** Choose **Simulate Friction (>3 Clicks)**, or click/edit/change controls in the full proposal more than three times.
4. **Rapid submit:** Click **Submit Proposal & Get Quote** three times within two seconds.
5. **Edit and restore:** Trigger the wizard, edit fields across its five sections, then choose **Go Back to Full Form** (or **Back to Full Form**). Edits are copied back in the browser.
6. **Confirm & AI Underwriting:** Complete required fields and consent, then choose **Confirm & Finish** (in the wizard) or **Submit Proposal & Get Quote** (in the full form).
7. **Offline fallback:** Stop the server and use a simulation button. The browser renders the guided flow without a WebSocket.
8. **Reset:** Choose **Reset** to return to the standard portal and clear telemetry.
9. **Health check:** Run `Invoke-RestMethod http://127.0.0.1:8765/health`.

# AuraGen — Insurance Proposal UI Demo

AuraGen is a privacy-first adaptive UI demo for a 25-field insurance proposal.
Its vanilla HTML/CSS/JavaScript frontend observes interaction friction and
turns the form into a guided five-section wizard. FastAPI serves the page and
returns a Gemini-generated wizard shell over WebSockets.

## Privacy and demo limitations

- **Do not enter real PAN, Aadhaar, health, financial, or contact details.**
  This is a UI demo, not an insurer or an application processing system.
- Form values stay in the browser. The WebSocket sends only a friction event,
  cognitive-load score, and static form version. Gemini receives the
  server-side field schema and trigger metadata, never form values.
  - The wizard displays a provenance badge: live/cached Gemini output is labeled
    as Gemini-generated; fallback output explicitly says Gemini was not used.
- Submitting the form shows a local demo confirmation. Nothing is transmitted,
  stored, or submitted to an insurer.
- Before collecting real data, add an authorized backend, authentication,
  consent and retention policies, encryption, audit controls, and the
  insurer's approved submission integration.

## Run locally (Windows PowerShell)

From the project directory:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$secureKey = Read-Host "Google GenAI API key" -AsSecureString
$env:GOOGLE_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
uvicorn server:app --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/`. Port 8765 is used because port 8000 was already
occupied in the development workspace. The API key is optional for testing:
without it, the server uses its validated fallback wizard. Do not put keys in
source files or commit them.

## Deploy to a Python ASGI host

Use the host's native Python runtime (Python 3.12 or newer):

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn server:app --host 0.0.0.0 --port $PORT`
- Set `GOOGLE_API_KEY` in the host's secret/environment-variable settings.
- Enable WebSocket proxying and HTTPS. The browser automatically switches to
  `wss:` on HTTPS deployments.
- Configure the platform health check to `GET /health`.

The app is stateless and does not persist proposal data. Keep the platform's
port variable (`PORT`) available to the start command.

## Test the app

1. **Three-second hesitation:** Choose **Simulate Confusion (3s Pause)** and
   wait three seconds, or interact with a form field and then pause.
2. **Form friction:** Choose **Simulate >3 Form Clicks**, or click/edit/change
   controls in the full proposal more than three times.
3. **Rapid submit:** Click **Review Demo Proposal** three times within two
   seconds.
4. **Edit and restore:** Trigger the wizard, edit fields across its five
   sections, then choose **Go Back to Full Form**. Edits are copied back in
   the browser.
5. **Confirm:** Complete required fields and consent, then choose
   **Confirm & Finish**. The confirmation is local only.
6. **Offline fallback:** Stop the server and use a simulation button. The
   browser renders the guided flow without a WebSocket.
7. **Reset:** Choose **Reset to Full Form** to return and clear telemetry.
8. **Health check:** Run `Invoke-RestMethod http://127.0.0.1:8765/health`.
9. **Backend tests:** Run `python -m unittest test_server -v`.

The telemetry console never logs field values.

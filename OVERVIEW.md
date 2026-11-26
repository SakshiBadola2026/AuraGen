# AuraGen Project Overview

**Live Demo:** https://auragen-ycuq.onrender.com/

AuraGen is a local and cloud-ready demo of an adaptive insurance proposal form. It starts as a 25-field form (plus consent declaration) and can switch to a guided five-step wizard when the demo detects interaction friction.

This is only a UI demonstration. It is not an insurance application and does not submit proposals to an insurer.

## Run the application

On Windows, double-click `run.bat`. On its first run, it creates a Python virtual environment and installs the required packages. Later runs start the app without reinstalling them.

When the server is running, open <http://127.0.0.1:8765/> in a browser. Keep the server window open while using the app.

## Gemini configuration

The app uses Gemini (`gemini-3.5-flash-lite`) to auto-generate field guidance at startup, render the guided wizard layout, and perform preliminary anonymized underwriting assessment. To enable Gemini locally, copy `env.example` to `env.env` and add your API key(s):

```text
GEMINI_API_KEYS=your_key_1,your_key_2
GOOGLE_API_KEY=your_primary_key
```

`GEMINI_API_KEY` is also accepted. Restart the app after changing the file. Without a key, the app still runs using its built-in fallback interface and rules. Never commit `env.env` to Git.

## What the demo does

- Auto-discovers all form fields and labels directly from `index.html` at startup and pre-generates Gemini tips/examples.
- Shows live Gemini guidance for every input and dropdown in both the standard form and wizard (pinned in the **Gemini Field Copilot** sidebar as well as inline above open dropdowns).
- Tracks form interaction and hesitation in the browser.
- Switches to the wizard after a supported friction event, such as a three-second pause after interaction, more than three form interactions, or rapid repeated submission.
- Copies edits between the full form and wizard locally in the browser.
- Runs anonymized Gemini underwriting on submission and shows a downloadable demo receipt (`data/applications.json` locally; nothing is sent to an insurer).

The browser sends friction metadata, not entered form values, over WebSockets. Gemini receives the auto-discovered field schema and trigger metadata for wizard/guide generation, and only anonymized risk attributes (never PII) on proposal submission.

## Backend endpoints

- `GET /` serves the web application.
- `GET /health` reports server status, loaded keys, discovered fields, and cached Gemini guides.
- `GET /api/field-guidance` returns Gemini-generated guidance for a field name and label.
- `POST /api/proposals/submit` runs anonymized AI underwriting and returns the preliminary quote receipt.
- `WS /ws/telemetry` receives friction events / field guidance requests and returns a generated or fallback response.

## Project files

- `index.html` — frontend, form, styling, Gemini Field Copilot, and browser-side behavior.
- `server.py` — FastAPI backend, HTML field auto-discovery, Gemini integration, AST HTML validator, and fallback responses.
- `requirements.txt` — Python package dependencies.
- `run.bat` — Windows launcher.
- `env.example` — template for local API key configuration.
- `env.env` — local API key settings (git-ignored; keep real keys private).

## Privacy and limitations

Do not enter real names, government ID numbers, health details, contact details, or financial information. The demo has no authentication or insurer integration. Do not use it to collect real insurance applications.

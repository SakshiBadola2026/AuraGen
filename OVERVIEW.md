# AuraGen Project Overview

AuraGen is a local demo of an adaptive insurance proposal form. It starts as a
25-field form and can switch to a guided five-step wizard when the demo detects
interaction friction.

This is only a UI demonstration. It is not an insurance application and does
not submit proposals to an insurer.

## Run the application

On Windows, double-click `run.bat`. On its first run, it creates a Python
virtual environment and installs the required packages. Later runs start the
app without reinstalling them.

When the server is running, open <http://127.0.0.1:8765/> in a browser. Keep
the server window open while using the app.

## Gemini configuration

The app can use Gemini to generate wizard layout and field guidance. To enable
Gemini, add an API key to the project's `env.env` file:

```text
GOOGLE_API_KEY=your_api_key
```

`GEMINI_API_KEY` is also accepted. Restart the app after changing the file.
Without a key, the app still runs using its built-in fallback interface and
guidance.

## What the demo does

- Tracks form interaction and hesitation in the browser.
- Switches to the wizard after a supported friction event, such as a three
  second pause after interaction, more than three form interactions, or rapid
  repeated submission.
- Copies edits between the full form and wizard locally in the browser.
- Shows a local demo confirmation; nothing is submitted to an insurer.

The browser sends friction metadata, not entered form values, to the backend.
Gemini receives the static field schema and trigger information for wizard
generation, not the user's form values. The generated wizard markup is
validated before it is displayed.

## Backend endpoints

- `GET /` serves the web application.
- `GET /health` reports whether the backend is running.
- `GET /api/field-guidance` returns guidance for a field name and label.
- `WS /ws/telemetry` receives friction events and returns a generated or
  fallback wizard shell.

## Project files

- `index.html` — frontend, form, styling, and browser-side behavior.
- `server.py` — FastAPI backend, Gemini integration, and fallback responses.
- `requirements.txt` — Python package dependencies.
- `run.bat` — Windows launcher.
- `env.env` — optional local API key settings; keep real keys private.

## Privacy and limitations

Do not enter real names, government ID numbers, health details, contact details,
or financial information. The demo has no authentication, insurer integration,
or proposal storage. Do not use it to collect real insurance applications.

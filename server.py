import os
import json
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from google import genai
from fastapi.responses import FileResponse  # <-- 1. Add this import
from google.genai import types

app = FastAPI()

# Picks up $env:GOOGLE_API_KEY automatically from your PowerShell session
client = genai.Client()

SYSTEM_PROMPT = """You generate clean HTML panels for the AuraGen dashboard.
Use ONLY these CSS classes: .panel, .eyebrow, .actions, .primary, .secondary.
Format:
<div class="panel">
  <div>
    <p class="eyebrow">SHORT STATUS</p>
    <h3>Helpful Heading</h3>
    <p>Short guidance based on user behavior.</p>
  </div>
  <div class="actions">
    <button class="primary" data-action="reset">Reset Session</button>
    <button class="secondary" data-action="acknowledge">Dismiss</button>
  </div>
</div>
Output ONLY raw HTML. Do NOT use markdown (```), <script>, or onclick."""

# <-- 2. Add this route so http://127.0.0.1:8000 opens your HTML page
@app.get("/")
async def serve_frontend():
    return FileResponse("AuraGen_Week1.html")

@app.websocket("/ws/telemetry")
async def telemetry_ws(ws: WebSocket):
    await ws.accept()
    last_run = 0

    try:
        while True:
            data = json.loads(await ws.receive_text())
            events = data.get("events", [])
            score = data.get("cognitiveScore", 0)

            if time.time() - last_run < 6:
                continue

            trigger = None
            if any(e.get("type") == "rage_click" for e in events):
                trigger = "User is rage-clicking rapidly out of frustration"
            elif any(e.get("type") == "hesitation" for e in events):
                trigger = "User paused and is hesitating"

            if trigger:
                last_run = time.time()
                print(f"Generating UI for: {trigger}...")

                response = await client.aio.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=f"User behavior: {trigger}. Cognitive load score: {score}/100.",
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.3
                    )
                )

                html = response.text.strip().replace("```html", "").replace("```", "").strip()

                # Safety gate: block script tags or inline JS
                if "<script" not in html.lower() and "onclick" not in html.lower():
                    await ws.send_text(json.dumps({
                        "type": "RENDER_UI",
                        "html": html,
                        "reason": trigger
                    }))
                    print("Sent generated HTML to browser!")

    except WebSocketDisconnect:
        print("Browser disconnected.")
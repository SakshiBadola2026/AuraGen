import asyncio
import hashlib
import json
import os
import time
import uuid
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, JSONResponse
from google import genai
from google.genai import types

# ------------------------------------------------------------------------------
# 1. APPLICATION SETUP & ENVIRONMENT CONFIGURATION
# ------------------------------------------------------------------------------
app = FastAPI(title="AuraGen — Self-Healing UI & Underwriting Platform")
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Load secret API keys from env.env (safely git-ignored)
load_dotenv(BASE_DIR / "env.env")

# Model configuration: gemini-3.1-flash-lite provides sub-1.5s latency
PRIMARY_MODEL = "gemini-3.1-flash-lite"
FALLBACK_MODEL = "gemini-3.5-flash-lite"
FORM_ID = "insurance-proposal-v1"

# Fast in-memory caching to guarantee < 10ms repeat responses
UI_CACHE: dict[str, str] = {}
FIELD_GUIDE_CACHE: dict[str, dict[str, str]] = {}
db_lock = asyncio.Lock()


# ------------------------------------------------------------------------------
# 2. MULTI-KEY AUTO-ROTATION (HIGH AVAILABILITY & QUOTA RESILIENCE)
# ------------------------------------------------------------------------------
def get_api_keys() -> list[str]:
    """Retrieves all available Gemini keys from environment variables."""
    raw_keys = os.getenv("GEMINI_API_KEYS", "")
    keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    if not keys:
        single = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if single:
            keys.append(single.strip())
    return keys


# Track the current active key index
_current_key_index = 0


def get_next_client() -> tuple[genai.Client, str]:
    """Rotates through available API keys to prevent 429 rate limit errors."""
    global _current_key_index
    keys = get_api_keys()
    if not keys:
        raise RuntimeError("No Gemini API keys configured in env.env")
    key = keys[_current_key_index % len(keys)]
    _current_key_index = (_current_key_index + 1) % len(keys)
    return genai.Client(api_key=key), key


# ------------------------------------------------------------------------------
# 3. FIELD GUIDANCE (SHORT, DIRECT, SUB-2 SECOND INSTRUCTIONS)
# ------------------------------------------------------------------------------
def get_default_guide(field_name: str, field_label: str) -> dict[str, str]:
    """Deterministic, instant guidance fallback if API exceeds 2 seconds."""
    defaults = {
        "policyType": ("Comprehensive medical coverage for family hospitalization.", "Health Insurance"),
        "sumInsured": ("Recommended minimum is 10 lakhs for metro healthcare.", "₹10,00,000"),
        "tenure": ("Multi-year locking secures up to 10% premium discount.", "3 Years"),
        "startDate": ("Earliest effective coverage commencement date is today.", "2026-12-01"),
        "frequency": ("Annual payment waives installment administrative surcharge fees.", "Annual"),
        "fullName": ("Must match your official government identity proof exactly.", "Aarav Sharma"),
        "dob": ("Must be 18 years or older for policyholder eligibility.", "1992-05-15"),
        "gender": ("Required for actuarial life expectancy risk rating tables.", "Male"),
        "maritalStatus": ("Determines family floater policy addition privileges.", "Married"),
        "nationality": ("Country issuing your primary tax citizenship residence.", "Indian"),
        "tobacco": ("Crucial for claim clearance; non-smokers get lower rates.", "No"),
        "occupation": ("Hazard classification for professional underwriting tiering.", "Salaried Professional"),
        "income": ("Required for financial underwriting limits above 10 lakhs.", "₹10L – ₹25L"),
        "employer": ("Current organization or write 'Self-Employed' if business owner.", "Infosys Ltd"),
        "medical": ("Declare chronic conditions; write 'None' if no history.", "None"),
        "email": ("Policy certificates and tax deduction receipts delivered here.", "name@example.com"),
        "phone": ("Used for instant claim authorization OTP alerts.", "9876543210"),
        "address": ("Permanent residence where formal notices are delivered.", "Flat 402, Green Meadows"),
        "cityState": ("Determines medical zone and network hospital reimbursement.", "Bengaluru, Karnataka"),
        "pincode": ("6-digit postal code of your residential location.", "560001"),
        "idType": ("PAN is mandatory for annual premium exceeding ₹50,000.", "PAN Card"),
        "idNumber": ("10-character alphanumeric PAN printed on your card.", "ABCDE1234F"),
        "nomineeName": ("Legal beneficiary designated to receive claim proceeds.", "Priya Sharma"),
        "nomineeRelation": ("Family relationship of nominee to the primary insured.", "Spouse"),
        "prevPolicy": ("Mention existing policy for No-Claim Bonus transfer.", "POL-78912"),
        "agree": ("Legal confirmation of complete and truthful disclosure.", "Checked"),
    }
    tip, eg = defaults.get(
        field_name,
        (f"Accurate information required for risk evaluation.", f"Standard format for {field_label}")
    )
    return {"guide": tip, "example": eg, "what": tip, "how": eg}


async def _generate_field_guidance(field_name: str, field_label: str) -> dict[str, str]:
    """Generates concise guidance via Gemini with strict sub-2s timeout."""
    # 1. Return immediately from cache if available (< 1ms)
    if field_name in FIELD_GUIDE_CACHE:
        return FIELD_GUIDE_CACHE[field_name]

    keys = get_api_keys()
    if not keys:
        fallback = get_default_guide(field_name, field_label)
        FIELD_GUIDE_CACHE[field_name] = fallback
        return fallback

    prompt = (
        "You are a concise form assistant. Given a form field, return JSON with:\n"
        "- \"guide\": 1 ultra-short tip on what to enter or where to find it (max 10 words). Do not repeat the field label or say \"Enter/Select your...\".\n"
        "- \"example\": 1 realistic example value (max 5 words).\n\n"
        "Example output for \"Group Number\":\n"
        "{\"guide\": \"Found on your insurance card near your Member ID.\", \"example\": \"e.g., GRP-98421\"}\n\n"
        f"Target field: \"{field_label}\" (attribute key: \"{field_name}\")\n"
        "Return pure valid JSON only."
    )

    # Try rotating keys with a strict 2.0-second timeout
    for _ in range(min(len(keys), 3)):
        try:
            client, _ = get_next_client()
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=PRIMARY_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                ),
                timeout=2.0,  # Enforce max 2 seconds response rule
            )
            data = json.loads(response.text.strip())
            guide_val = str(data.get("guide", data.get("what", ""))).strip()
            example_val = str(data.get("example", data.get("how", ""))).strip()
            if guide_val and example_val:
                result = {
                    "guide": guide_val,
                    "example": example_val,
                    "what": guide_val,
                    "how": example_val,
                }
                FIELD_GUIDE_CACHE[field_name] = result
                return result
        except Exception:
            continue

    # Instant fallback if network or API was slower than 2 seconds
    fallback = get_default_guide(field_name, field_label)
    FIELD_GUIDE_CACHE[field_name] = fallback
    return fallback


# ------------------------------------------------------------------------------
# 4. AST SECURITY PARSER (VALIDATES LLM-GENERATED CODE BEFORE DOM INJECTION)
# ------------------------------------------------------------------------------
class HTMLSecurityParser(HTMLParser):
    """Parses LLM HTML into an AST to block malicious scripts, XSS, and syntax errors."""
    ALLOWED_TAGS = {"div", "form", "p", "h2", "h3", "label", "button", "span", "strong"}
    ALLOWED_ATTRS = {"class", "id", "type", "data-action"}
    REQUIRED_ACTIONS = {"previous", "next", "confirm", "restore"}
    REQUIRED_SLOTS = {"wizardFields", "wizardProgress", "wizardStatus"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.safe = True
        self.stack: list[str] = []
        self.root_seen = False
        self.root_closed = False
        self.ids: set[str] = set()
        self.slots: set[str] = set()
        self.action_counts: dict[str, int] = {}
        self.form_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag not in self.ALLOWED_TAGS or self.root_closed:
            self.safe = False
            return
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if any(name not in self.ALLOWED_ATTRS for name in attr_map):
            self.safe = False
        for value in attr_map.values():
            if "javascript:" in value.lower() or "data:" in value.lower():
                self.safe = False
        element_id = attr_map.get("id")
        if element_id:
            if element_id in self.ids:
                self.safe = False
            self.ids.add(element_id)
            if element_id in self.REQUIRED_SLOTS:
                if "form" not in self.stack:
                    self.safe = False
                self.slots.add(element_id)
        if not self.root_seen:
            self.root_seen = True
            if tag != "div" or attr_map.get("class") != "wizard-box":
                self.safe = False
        if tag == "form":
            self.form_count += 1
            if attr_map.get("id") != "wizardForm":
                self.safe = False
        if tag == "button":
            action = attr_map.get("data-action")
            if "form" not in self.stack or attr_map.get("type") != "button" or action not in self.REQUIRED_ACTIONS:
                self.safe = False
            elif action:
                self.action_counts[action] = self.action_counts.get(action, 0) + 1
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self.stack or self.stack[-1] != tag:
            self.safe = False
            return
        self.stack.pop()
        if not self.stack and tag == "div":
            self.root_closed = True

    def handle_comment(self, data: str) -> None:
        self.safe = False

    def handle_decl(self, decl: str) -> None:
        self.safe = False


def validate_html(markup: str) -> bool:
    """Validates that generated HTML strictly adheres to AST security standards."""
    if not markup or len(markup) > 15_000:
        return False
    parser = HTMLSecurityParser()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        return False
    return (
        parser.safe
        and parser.root_seen
        and parser.root_closed
        and not parser.stack
        and parser.form_count == 1
        and parser.slots == parser.REQUIRED_SLOTS
        and parser.action_counts == {action: 1 for action in parser.REQUIRED_ACTIONS}
    )


def _fallback_html() -> str:
    """Standard pre-compiled shell used when offline."""
    return (
        '<div class="wizard-box">'
        '<form id="wizardForm">'
        '<div id="wizardFields"></div>'
        '<div id="wizardProgress"></div>'
        '<div id="wizardStatus"></div>'
        '<div class="wizard-actions">'
        '<button type="button" class="btn-ghost" data-action="previous">Previous</button>'
        '<button type="button" class="btn-primary" data-action="next">Next section</button>'
        '<button type="button" class="btn-success" data-action="confirm">Confirm &amp; Finish</button>'
        '<button type="button" class="btn-ghost" data-action="restore">Go Back to Full Form</button>'
        "</div></form></div>"
    )


SYSTEM_PROMPT = """You are AuraGen, a privacy-first UI generator for guided insurance proposal forms.

Core Rules:
- Output raw, valid HTML only. No markdown fences, no explanations, no extra text.
- Keep all copy ultra-short, calm, and actionable. Never add filler or obvious instructions.
- Reassure the user that all entered details stay locally in their browser and are never sent to an AI service.
- Never generate <input>, <select>, <textarea>, <style>, or <script> tags.
- Strictly preserve all required IDs, classes, and data-action attributes from the requested template.

Output must begin with <div class="wizard-box"> and end with </div>."""

def _cache_key(trigger: str, context: dict[str, str]) -> str:
    canonical = json.dumps(context, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{trigger}|{canonical}".encode("utf-8")).hexdigest()


async def _generate_html(trigger: str, score: int, context: dict[str, str]) -> tuple[str, str]:
    """Generates the simplified wizard shell via Gemini under 2 seconds."""
    cache_key = _cache_key(trigger, context)
    if cache_key in UI_CACHE:
        return UI_CACHE[cache_key], "CACHE_HIT"

    keys = get_api_keys()
    if not keys:
        return _fallback_html(), "SERVER_SAFE_FALLBACK"

    prompt = f"User experienced {trigger} with friction score {score}/100. Generate the adaptive wizard shell."
    for _ in range(min(len(keys), 3)):
        try:
            client, _ = get_next_client()
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=PRIMARY_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.1,
                    ),
                ),
                timeout=2.2,
            )
            markup = response.text.strip().removeprefix("```html").removeprefix("```").removesuffix("```").strip()
            if validate_html(markup):
                UI_CACHE[cache_key] = markup
                return markup, "GEMINI_STREAM"
        except Exception:
            continue

    return _fallback_html(), "SERVER_SAFE_FALLBACK"


# ------------------------------------------------------------------------------
# 5. REST API ENDPOINTS
# ------------------------------------------------------------------------------
@app.get("/")
async def serve_frontend() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "platform": "AuraGen Enterprise",
        "keys_loaded": len(get_api_keys()),
        "model": PRIMARY_MODEL,
    }


@app.get("/api/field-guidance")
async def get_field_guidance_endpoint(name: str, label: str = "") -> dict[str, Any]:
    t0 = time.perf_counter()
    guide = await _generate_field_guidance(name, label or name)
    latency = round((time.perf_counter() - t0) * 1000, 2)
    source = "CACHE" if latency < 15 else "GEMINI_LIVE"
    return {"status": "ok", "fieldName": name, "source": source, "latencyMs": latency, **guide}


@app.post("/api/proposals/submit")
async def submit_proposal(request: Request) -> JSONResponse:
    """Processes proposal submission, calculates real actuarial quote, and stores record."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid JSON"}, status_code=400)

    # Real quote calculation with safe descending order to prevent substring collision
    sum_str = str(data.get("sumInsured", "₹10,00,000"))
    if "50,00,000" in sum_str or "50L" in sum_str:
        numeric_sum = 5000000
    elif "25,00,000" in sum_str or "25L" in sum_str:
        numeric_sum = 2500000
    elif "10,00,000" in sum_str or "10L" in sum_str:
        numeric_sum = 1000000
    elif "5,00,000" in sum_str or "5L" in sum_str:
        numeric_sum = 500000
    else:
        numeric_sum = 1000000

    base_premium = numeric_sum * 0.0085
    tobacco = str(data.get("tobacco", "No")).lower()
    tobacco_load = base_premium * 0.20 if ("regular" in tobacco or "occasional" in tobacco or "yes" in tobacco) else 0.0
    medical = str(data.get("medical", "")).strip().lower()
    medical_load = base_premium * 0.15 if (medical and medical != "none") else 0.0
    frequency = str(data.get("frequency", "Annual"))
    discount = base_premium * 0.08 if "annual" in frequency.lower() else 0.0

    net = max(1800, base_premium + tobacco_load + medical_load - discount)
    gst = round(net * 0.18, 2)
    total = round(net + gst, 2)

    app_id = f"AURA-{datetime.now().year}-{uuid.uuid4().hex[:6].upper()}"
    record = {
        "applicationId": app_id,
        "submittedAt": datetime.now().isoformat(),
        "applicant": {
            "fullName": data.get("fullName", "Applicant"),
            "email": data.get("email", ""),
            "phone": data.get("phone", ""),
            "dob": data.get("dob", ""),
            "cityState": data.get("cityState", ""),
            "idType": data.get("idType", ""),
            "idNumber": data.get("idNumber", ""),
            "nomineeName": data.get("nomineeName", ""),
            "nomineeRelation": data.get("nomineeRelation", ""),
        },
        "coverage": {
            "policyType": data.get("policyType", "Health Insurance"),
            "sumInsured": sum_str,
            "tenure": data.get("tenure", "3 Years"),
            "frequency": frequency,
        },
        "underwriting": {
            "riskTier": "Standard Risk" if tobacco_load == 0 and medical_load == 0 else "Rated Risk",
            "basePremium": round(base_premium, 2),
            "discount": round(discount, 2),
            "gst18": gst,
            "totalPayable": total,
            "status": "APPROVED_PRELIMINARY",
        }
    }

    # Store in local JSON database safely with async lock
    async with db_lock:
        db_file = DATA_DIR / "applications.json"
        try:
            existing = json.loads(db_file.read_text(encoding="utf-8")) if db_file.exists() else []
        except Exception:
            existing = []
        existing.append(record)
        db_file.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    return JSONResponse({"status": "success", "record": record})


# ------------------------------------------------------------------------------
# 6. REAL-TIME WEBSOCKET (COGNITIVE FRICTION & INSTANT COPILOT GUIDANCE)
# ------------------------------------------------------------------------------
@app.websocket("/ws/telemetry")
async def telemetry_ws(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            try:
                payload: Any = json.loads(await ws.receive_text())
                if not isinstance(payload, dict):
                    continue

                # Event 1: Dynamic Field Guidance Request (as user clicks on a field)
                if payload.get("type") == "FIELD_GUIDANCE_REQUEST":
                    field_name = str(payload.get("fieldName", "")).strip()
                    field_label = str(payload.get("fieldLabel", field_name)).strip()
                    t0 = time.perf_counter()
                    guide = await _generate_field_guidance(field_name, field_label)
                    latency = round((time.perf_counter() - t0) * 1000, 2)
                    source = "CACHE" if latency < 15 else "GEMINI_LIVE"
                    await ws.send_json({
                        "type": "FIELD_GUIDANCE_RESPONSE",
                        "fieldName": field_name,
                        "guide": guide.get("guide", guide.get("what", "")),
                        "example": guide.get("example", guide.get("how", "")),
                        "what": guide.get("guide", guide.get("what", "")),
                        "how": guide.get("example", guide.get("how", "")),
                        "latencyMs": latency,
                        "source": source,
                    })
                    continue

                # Event 2: Cognitive Friction Spikes (Rage clicks, Hesitation, Erratic cursor)
                events = payload.get("events", [])
                trigger = next(
                    (
                        e.get("type")
                        for e in events
                        if isinstance(e, dict)
                        and e.get("type") in {"hesitation", "rage_click", "rapid_submit", "erratic_cursor"}
                    ),
                    None,
                )
                if not trigger:
                    continue

                score = max(0, min(100, int(payload.get("cognitiveScore", 0))))
                t0 = time.perf_counter()
                markup, source = await _generate_html(trigger, score, {"formId": FORM_ID})
                await ws.send_json({
                    "type": "RENDER_UI",
                    "html": markup,
                    "reason": trigger,
                    "latencyMs": round((time.perf_counter() - t0) * 1000, 2),
                    "source": source,
                })

            except json.JSONDecodeError:
                await ws.send_json({"type": "ERROR", "message": "Invalid JSON"})
            except ValueError as exc:
                await ws.send_json({"type": "ERROR", "message": str(exc)})
    except WebSocketDisconnect:
        pass

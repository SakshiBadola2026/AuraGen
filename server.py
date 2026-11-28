import asyncio
import hashlib
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from google import genai
from google.genai import types

# ==============================================================================
# 1. APP SETUP & CONFIG
# ==============================================================================
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

load_dotenv(BASE_DIR / "env.env")

PRIMARY_MODEL = "gemini-3.5-flash-lite"
FORM_ID = "insurance-proposal-v1"


class FormSchemaParser(HTMLParser):
    """Automatically reads form field names and labels from index.html."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_form = False
        self.current_label_for: str | None = None
        self.label_buf: list[str] = []
        self.labels: dict[str, str] = {}
        self.fields: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if tag == "form" and attr_map.get("id") == "proposalForm":
            self.in_form = True
        if not self.in_form:
            return
        if tag == "label" and attr_map.get("for"):
            self.current_label_for = attr_map["for"]
            self.label_buf = []
        elif tag in {"input", "select", "textarea"}:
            name = attr_map.get("name") or attr_map.get("data-field") or attr_map.get("id")
            if name:
                self.fields.append((name, attr_map.get("id", name)))

    def handle_data(self, data: str) -> None:
        if self.in_form and self.current_label_for is not None:
            self.label_buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "label" and self.current_label_for is not None:
            text = " ".join("".join(self.label_buf).replace("*", "").split())
            if text:
                self.labels[self.current_label_for] = text[:80]
            self.current_label_for = None
        elif tag == "form" and self.in_form:
            self.in_form = False


def load_form_fields() -> dict[str, str]:
    """Auto-fetch all form fields and labels directly from index.html."""
    html_path = BASE_DIR / "index.html"
    if not html_path.exists():
        return {}
    parser = FormSchemaParser()
    parser.feed(html_path.read_text(encoding="utf-8"))
    parser.close()
    return {name: parser.labels.get(el_id, name) for name, el_id in parser.fields}


# Auto-discovered from index.html at startup (no hardcoded field list)
FORM_FIELDS: dict[str, str] = load_form_fields()

UI_CACHE: dict[str, str] = {}
FIELD_GUIDE_CACHE: dict[str, dict[str, str]] = {}
_CLIENTS: dict[tuple[str, int], genai.Client] = {}
_current_key_index = 0
db_lock = asyncio.Lock()


# ==============================================================================
# 2. GEMINI CLIENT & ROTATION
# ==============================================================================
def get_api_keys() -> list[str]:
    """Read Gemini API keys from environment variables."""
    raw = os.getenv("GEMINI_API_KEYS", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        single = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if single:
            keys.append(single.strip())
    return keys


def get_next_client() -> tuple[genai.Client, str]:
    """Return the next cached Gemini client in rotation for the active event loop."""
    global _current_key_index
    keys = get_api_keys()
    if not keys:
        raise RuntimeError("No Gemini API keys configured.")

    key = keys[_current_key_index % len(keys)]
    _current_key_index = (_current_key_index + 1) % len(keys)
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = 0
    cache_id = (key, loop_id)
    if cache_id not in _CLIENTS:
        _CLIENTS[cache_id] = genai.Client(api_key=key)
    return _CLIENTS[cache_id], key


def _parse_json(raw: str) -> Any:
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(cleaned)


async def ask_gemini(
    prompt: str,
    *,
    system_instruction: str | None = None,
    json_mode: bool = False,
    timeout: float = 4.0,
) -> str | None:
    """Call Gemini with automatic key rotation and timeout."""
    keys = get_api_keys()
    if not keys:
        return None

    config_args: dict[str, Any] = {"temperature": 0.15}
    if system_instruction:
        config_args["system_instruction"] = system_instruction
    if json_mode:
        config_args["response_mime_type"] = "application/json"

    for _ in range(min(len(keys), 3)):
        try:
            client, _ = get_next_client()
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=PRIMARY_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(**config_args),
                ),
                timeout=timeout,
            )
            return response.text.strip()
        except Exception:
            continue
    return None


# ==============================================================================
# 3. AI-GENERATED FIELD GUIDANCE (BATCH WARMUP + LIVE ON-DEMAND)
# ==============================================================================
def _pack_guide(guide: str, example: str) -> dict[str, str]:
    return {"guide": guide, "example": example, "what": guide, "how": example}


async def warm_field_guides() -> None:
    """Ask Gemini once at startup to generate guides & examples for all form fields."""
    if not get_api_keys():
        return

    fields_json = json.dumps(FORM_FIELDS, ensure_ascii=False)
    prompt = (
        "Generate concise Indian insurance form help for every field key below.\n"
        "Return a JSON object mapping each key to {\"guide\": \"max 10 words\", \"example\": \"max 5 words\"}.\n"
        f"Fields: {fields_json}"
    )
    raw = await ask_gemini(prompt, json_mode=True, timeout=12.0)
    if not raw:
        return
    try:
        parsed = _parse_json(raw)
        if isinstance(parsed, dict):
            for key, item in parsed.items():
                if key in FORM_FIELDS and isinstance(item, dict):
                    g = str(item.get("guide", "")).strip()
                    e = str(item.get("example", "")).strip()
                    if g and e and key not in FIELD_GUIDE_CACHE:
                        FIELD_GUIDE_CACHE[key] = _pack_guide(g, e)
    except Exception:
        pass


async def _generate_field_guidance(field_name: str, field_label: str) -> tuple[dict[str, str], str]:
    """Return Gemini-generated field guidance (cached or live)."""
    if field_name in FIELD_GUIDE_CACHE:
        return FIELD_GUIDE_CACHE[field_name], "GEMINI_CACHE"

    prompt = (
        "Return pure JSON with:\n"
        '- "guide": 1 short helpful tip on what to choose/enter (max 10 words, no filler).\n'
        '- "example": 1 realistic example value (max 5 words).\n'
        f'Field: "{field_label}" (key: "{field_name}")'
    )
    raw = await ask_gemini(prompt, json_mode=True, timeout=4.5)
    if raw:
        try:
            data = _parse_json(raw)
            guide = str(data.get("guide", data.get("what", ""))).strip()
            example = str(data.get("example", data.get("how", ""))).strip()
            if guide and example:
                packed = _pack_guide(guide, example)
                FIELD_GUIDE_CACHE[field_name] = packed
                return packed, "GEMINI_LIVE"
        except Exception:
            pass

    # Minimal offline safety message only if Gemini is completely unreachable
    return _pack_guide(f"Provide accurate details for {field_label}.", "Valid entry"), "OFFLINE_FALLBACK"


# ==============================================================================
# 4. AI-GENERATED WIZARD HTML & AST SECURITY CHECK
# ==============================================================================
SYSTEM_PROMPT = """You are AuraGen, an adaptive UI architect for privacy-first insurance proposal forms.

Output compact, raw, valid HTML only (no markdown fences, no explanations, no extra whitespace).
Adapt the wizard shell to the user's trigger and incomplete sections:
- Root MUST be <div class="wizard-box">...</div>.
- Include a tailored <h2> and short <p> explaining how this guided view helps with their situation.
- Include a <div class="wizard-callout"> with a short <strong> heading and a <ul class="wizard-checklist"><li>...</li></ul> (2-3 concise items, no PII).
- Must contain exactly one <form id="wizardForm" data-start-section="SECTION">, where SECTION is the best starting section from: policy, personal, employment, contact, kyc.
- Inside <form>, include <div id="wizardFields"></div>, <div id="wizardProgress"></div>, <div id="wizardStatus"></div>, and exact action buttons:
<div class="wizard-actions"><button type="button" class="btn-ghost" data-action="previous">Previous</button><button type="button" class="btn-primary" data-action="next">Next section</button><button type="button" class="btn-success" data-action="confirm">Confirm &amp; Finish</button><button type="button" class="btn-ghost" data-action="restore">Back to Full Form</button></div>
- Never use <input>, <select>, <textarea>, <style>, <script>, comments, or onclick/javascript: URLs."""


class HTMLSecurityParser(HTMLParser):
    """Validates AI-generated HTML to block scripts, unsafe attributes, or missing slots."""

    ALLOWED_TAGS = {"div", "form", "p", "h2", "h3", "label", "button", "span", "strong", "ul", "li"}
    ALLOWED_ATTRS = {"class", "id", "type", "data-action", "data-start-section"}
    VALID_SECTIONS = {"policy", "personal", "employment", "contact", "kyc"}
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
        if any(k not in self.ALLOWED_ATTRS for k in attr_map):
            self.safe = False
        if any("javascript:" in v.lower() or "data:" in v.lower() for v in attr_map.values()):
            self.safe = False

        el_id = attr_map.get("id")
        if el_id:
            if el_id in self.ids:
                self.safe = False
            self.ids.add(el_id)
            if el_id in self.REQUIRED_SLOTS:
                if "form" not in self.stack:
                    self.safe = False
                self.slots.add(el_id)

        if not self.root_seen:
            self.root_seen = True
            if tag != "div" or attr_map.get("class") != "wizard-box":
                self.safe = False

        if tag == "form":
            self.form_count += 1
            if attr_map.get("id") != "wizardForm":
                self.safe = False
            start_sec = attr_map.get("data-start-section")
            if start_sec and start_sec not in self.VALID_SECTIONS:
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
        and parser.action_counts == {a: 1 for a in parser.REQUIRED_ACTIONS}
    )


def _fallback_html() -> str:
    return (
        '<div class="wizard-box"><form id="wizardForm">'
        '<div id="wizardFields"></div><div id="wizardProgress"></div><div id="wizardStatus"></div>'
        '<div class="wizard-actions">'
        '<button type="button" class="btn-ghost" data-action="previous">Previous</button>'
        '<button type="button" class="btn-primary" data-action="next">Next section</button>'
        '<button type="button" class="btn-success" data-action="confirm">Confirm &amp; Finish</button>'
        '<button type="button" class="btn-ghost" data-action="restore">Go Back to Full Form</button>'
        "</div></form></div>"
    )


async def _generate_html(trigger: str, score: int, context: dict[str, Any]) -> tuple[str, str]:
    raw_key = json.dumps(context, sort_keys=True, separators=(",", ":"))
    cache_key = hashlib.sha256(f"{trigger}|{raw_key}".encode()).hexdigest()
    if cache_key in UI_CACHE:
        return UI_CACHE[cache_key], "CACHE_HIT"

    prompt = (
        f"Trigger: {trigger} (effort score {score}/100).\n"
        f"Context (no PII): {json.dumps(context, ensure_ascii=False)}\n"
        "Generate an adapted guided wizard shell tailored to this trigger and context."
    )
    raw_html = await ask_gemini(prompt, system_instruction=SYSTEM_PROMPT, timeout=8.0)
    if raw_html:
        markup = raw_html.removeprefix("```html").removeprefix("```").removesuffix("```").strip()
        if validate_html(markup):
            UI_CACHE[cache_key] = markup
            return markup, "GEMINI_STREAM"

    return _fallback_html(), "SERVER_SAFE_FALLBACK"


# ==============================================================================
# 5. AI UNDERWRITING & QUOTE ASSESSMENT
# ==============================================================================
async def assess_proposal_with_llm(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Use Gemini for underwriting risk assessment & pricing (zero PII sent)."""
    sum_str = str(data.get("sumInsured", "₹10,00,000"))
    frequency = str(data.get("frequency", "Annual"))

    # Send only non-PII underwriting attributes to Gemini
    risk_profile = {
        "policyType": data.get("policyType", "Health Insurance"),
        "sumInsured": sum_str,
        "tenure": data.get("tenure", "3 Years"),
        "frequency": frequency,
        "dob": data.get("dob", ""),
        "tobacco": data.get("tobacco", "No"),
        "occupation": data.get("occupation", ""),
        "income": data.get("income", ""),
        "medical": data.get("medical", "None"),
    }

    prompt = (
        "You are an insurance underwriting engine. Assess this anonymized risk profile and calculate the quote in INR.\n"
        "Pricing formula:\n"
        "- basePremium = 0.0085 * numeric sumInsured (e.g. ₹10,00,000 -> 8500)\n"
        "- tobacco loading = +20% of basePremium if tobacco is Occasional/Regular/Yes\n"
        "- medical loading = +15% of basePremium if medical is non-empty and not 'None'\n"
        "- annual discount = -8% of basePremium if frequency contains 'Annual'\n"
        "- net = max(1800, basePremium + tobacco_loading + medical_loading - annual_discount)\n"
        "- gst18 = round(0.18 * net, 2); totalPayable = round(net + gst18, 2)\n\n"
        "Return pure JSON with exact keys:\n"
        '{"riskTier": "Standard Risk" or "Rated Risk", "basePremium": number, "discount": number, '
        '"gst18": number, "totalPayable": number, "status": "APPROVED_PRELIMINARY", '
        '"underwritingNote": "1 concise professional sentence explaining the decision"}\n\n'
        f"Profile: {json.dumps(risk_profile, ensure_ascii=False)}"
    )

    raw = await ask_gemini(prompt, json_mode=True, timeout=5.0)
    if raw:
        try:
            res = _parse_json(raw)
            return sum_str, {
                "riskTier": str(res["riskTier"]),
                "basePremium": round(float(res["basePremium"]), 2),
                "discount": round(float(res["discount"]), 2),
                "gst18": round(float(res["gst18"]), 2),
                "totalPayable": round(float(res["totalPayable"]), 2),
                "status": str(res.get("status", "APPROVED_PRELIMINARY")),
                "underwritingNote": str(res.get("underwritingNote", "Preliminary underwriting completed by AuraGen AI.")),
                "assessedBy": "GEMINI_LLM",
            }
        except Exception:
            pass

    # Deterministic fallback only if Gemini is offline
    sum_options = [(("50,00,000", "50L"), 5_000_000), (("25,00,000", "25L"), 2_500_000), (("10,00,000", "10L"), 1_000_000), (("5,00,000", "5L"), 500_000)]
    numeric_sum = next((amt for tokens, amt in sum_options if any(t in sum_str for t in tokens)), 1_000_000)
    base = numeric_sum * 0.0085
    tobacco_load = base * 0.20 if any(w in str(data.get("tobacco", "")).lower() for w in ("regular", "occasional", "yes")) else 0.0
    med = str(data.get("medical", "")).strip().lower()
    med_load = base * 0.15 if (med and med != "none") else 0.0
    discount = base * 0.08 if "annual" in frequency.lower() else 0.0
    net = max(1800, base + tobacco_load + med_load - discount)
    gst = round(net * 0.18, 2)
    return sum_str, {
        "riskTier": "Standard Risk" if tobacco_load == 0 and med_load == 0 else "Rated Risk",
        "basePremium": round(base, 2),
        "discount": round(discount, 2),
        "gst18": gst,
        "totalPayable": round(net + gst, 2),
        "status": "APPROVED_PRELIMINARY",
        "underwritingNote": "Preliminary quote calculated via local fallback rules.",
        "assessedBy": "OFFLINE_FALLBACK",
    }


# ==============================================================================
# 6. FASTAPI APP & ROUTES
# ==============================================================================
@asynccontextmanager
async def lifespan(_: FastAPI):
    asyncio.create_task(warm_field_guides())
    yield


app = FastAPI(title="AuraGen — Self-Healing UI & Underwriting Platform", lifespan=lifespan)


@app.get("/")
async def serve_frontend() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "platform": "AuraGen Enterprise",
        "keys_loaded": len(get_api_keys()),
        "fields_discovered": len(FORM_FIELDS),
        "guides_cached": len(FIELD_GUIDE_CACHE),
        "model": PRIMARY_MODEL,
    }


@app.get("/api/field-guidance")
async def get_field_guidance_endpoint(name: str, label: str = "") -> dict[str, Any]:
    t0 = time.perf_counter()
    guide, source = await _generate_field_guidance(name, label or FORM_FIELDS.get(name, name))
    latency = round((time.perf_counter() - t0) * 1000, 2)
    return {"status": "ok", "fieldName": name, "source": source, "latencyMs": latency, **guide}


@app.post("/api/proposals/submit")
async def submit_proposal(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid JSON"}, status_code=400)

    sum_str, underwriting = await assess_proposal_with_llm(data)
    record = {
        "applicationId": f"AURA-{datetime.now().year}-{uuid.uuid4().hex[:6].upper()}",
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
            "frequency": str(data.get("frequency", "Annual")),
        },
        "underwriting": underwriting,
    }

    async with db_lock:
        db_file = DATA_DIR / "applications.json"
        try:
            existing = json.loads(db_file.read_text(encoding="utf-8")) if db_file.exists() else []
        except Exception:
            existing = []
        existing.append(record)
        db_file.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    return JSONResponse({"status": "success", "record": record})


# ==============================================================================
# 7. WEBSOCKET (REAL-TIME FRICTION & AI FIELD GUIDANCE)
# ==============================================================================
FRICTION_TRIGGERS = {"hesitation", "rage_click", "rapid_submit", "erratic_cursor"}


@app.websocket("/ws/telemetry")
async def telemetry_ws(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            try:
                payload: Any = json.loads(await ws.receive_text())
                if not isinstance(payload, dict):
                    continue

                if payload.get("type") == "PING":
                    await ws.send_json({"type": "PONG"})
                    continue

                if payload.get("type") == "FIELD_GUIDANCE_REQUEST":
                    field_name = str(payload.get("fieldName", "")).strip()
                    field_label = str(payload.get("fieldLabel", FORM_FIELDS.get(field_name, field_name))).strip()
                    t0 = time.perf_counter()
                    guide, source = await _generate_field_guidance(field_name, field_label)
                    latency = round((time.perf_counter() - t0) * 1000, 2)
                    await ws.send_json({
                        "type": "FIELD_GUIDANCE_RESPONSE",
                        "fieldName": field_name,
                        "latencyMs": latency,
                        "source": source,
                        **guide,
                    })
                    continue

                events = payload.get("events", [])
                trigger = next(
                    (e.get("type") for e in events if isinstance(e, dict) and e.get("type") in FRICTION_TRIGGERS),
                    None,
                )
                if not trigger:
                    continue

                score = max(0, min(100, int(payload.get("cognitiveScore", 0))))
                raw_ctx = payload.get("domContext")
                safe_ctx: dict[str, Any] = {"formId": FORM_ID}
                if isinstance(raw_ctx, dict):
                    if isinstance(raw_ctx.get("activeSection"), str):
                        safe_ctx["activeSection"] = raw_ctx["activeSection"][:30]
                    if isinstance(raw_ctx.get("activeField"), str):
                        safe_ctx["activeField"] = raw_ctx["activeField"][:60]
                    if isinstance(raw_ctx.get("incompleteSections"), list):
                        safe_ctx["incompleteSections"] = [str(s)[:30] for s in raw_ctx["incompleteSections"][:5]]
                t0 = time.perf_counter()
                markup, source = await _generate_html(trigger, score, safe_ctx)
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

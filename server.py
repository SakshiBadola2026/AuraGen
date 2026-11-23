import asyncio
import hashlib
import json
import os
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from google import genai
from google.genai import types


app = FastAPI(title="AuraGen — Self-Healing Insurance Proposal Demo")
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / "env.env")
MODEL = "gemini-3.8-flash"
FORM_ID = "insurance-proposal-v1"
UI_CACHE: dict[str, str] = {}
FIELD_GUIDE_CACHE: dict[str, dict[str, str]] = {}


def _fallback_field_guide(field_name: str, field_label: str) -> dict[str, str]:
    defaults = {
        "policyType": ("Category of insurance protection requested.", "Select Health for medical coverage, Life for family protection, or Motor for vehicle."),
        "sumInsured": ("Maximum coverage amount paid out during a valid claim.", "Choose ₹10,00,000 for standard family coverage, or ₹25,00,000+ for higher medical security."),
        "tenure": ("Active duration of the insurance contract.", "Choose 3 Years for locked-in discount pricing, or 1 Year for annual review."),
        "startDate": ("Effective date when insurance protection begins.", "Select today's date or an upcoming date within the next 30 days."),
        "frequency": ("Billing cycle for premium payments.", "Select 'Annual' to waive installment fees (saves ~8%), or 'Monthly' for easier cashflow."),
        "fullName": ("Legal name of the primary policyholder.", "Type your full name exactly as printed on your government Aadhaar or PAN card."),
        "dob": ("Official date of birth.", "Must match your official ID. Primary policyholders must be at least 18 years old."),
        "gender": ("Legal gender as per official identification records.", "Select Male, Female, or Other for accurate actuarial life underwriting."),
        "maritalStatus": ("Current marital status.", "Select Single, Married, or Divorced. Required for family floater benefits."),
        "nationality": ("Country of legal citizenship.", "Default is 'Indian'. If foreign national, specify country of passport citizenship."),
        "tobacco": ("Habitual or occasional tobacco or alcohol usage.", "Declare truthfully. Honest disclosure ensures hassle-free claim settlement."),
        "occupation": ("Primary profession or employment category.", "Select 'Salaried' for payroll jobs, or 'Self-Employed' for business owners/freelancers."),
        "income": ("Gross annual household income bracket.", "Select income range matching your recent tax return (ITR) or salary slips."),
        "employer": ("Name of current employer or business entity.", "Optional. Type your organization's name or leave blank if self-employed/retired."),
        "medical": ("Pre-existing health conditions or chronic illnesses.", "Mention any conditions like diabetes, BP, or asthma. Write 'None' if completely healthy."),
        "email": ("Primary digital email address.", "Enter your active email where digital policy bonds and tax receipts will be emailed."),
        "phone": ("10-digit mobile contact number.", "Enter active mobile number to receive verification OTPs and claim status alerts."),
        "address": ("Permanent or primary residential address.", "Enter house/flat number, building name, and street locality for documentation."),
        "cityState": ("City and Indian state of residence.", "Enter city and state (e.g., 'Dehradun, Uttarakhand' or 'Bangalore, Karnataka')."),
        "pincode": ("6-digit postal index code.", "Enter valid 6-digit postal PIN code of your area (e.g., 248001)."),
        "idType": ("Official government identification document.", "Select PAN Card (preferred for financial verification) or Aadhaar Card."),
        "idNumber": ("Unique document identification number.", "Enter 10-character alphanumeric PAN (e.g. ABCDE1234F) or 12-digit Aadhaar number."),
        "nomineeName": ("Primary beneficiary who receives claim proceeds.", "Enter full legal name of your nominated family member (e.g. spouse, child, or parent)."),
        "nomineeRelation": ("Legal relationship of nominee to policyholder.", "Select Spouse, Parent, Child, or Sibling from the dropdown."),
        "prevPolicy": ("Previous insurance policy number (if renewing or porting).", "Optional. Enter if transferring your accumulated No Claim Bonus (NCB) discount."),
        "agree": ("Legal declaration of proposal accuracy.", "Check this box to confirm all provided information is complete and accurate."),
    }
    what, how = defaults.get(field_name, (f"Information required for {field_label}.", f"Enter valid details for {field_label}."))
    return {"what": what, "how": how}


async def _generate_field_guidance(field_name: str, field_label: str) -> dict[str, str]:
    if field_name in FIELD_GUIDE_CACHE:
        return FIELD_GUIDE_CACHE[field_name]

    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        fallback = _fallback_field_guide(field_name, field_label)
        FIELD_GUIDE_CACHE[field_name] = fallback
        return fallback

    client = genai.Client(api_key=api_key)
    prompt = (
        f"You are an expert AI form assistant for an insurance proposal. A user is filling out the form and just clicked on field '{field_label}' (key: '{field_name}').\n"
        "Provide very short, clear instructions in pure JSON format with exactly two keys:\n"
        "- 'what': what this field is (under 12 words)\n"
        "- 'how': exactly how to fill it out (under 18 words, practical actionable guidance)\n"
        "Return pure valid JSON only."
    )
    try:
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            ),
            timeout=5.0,
        )
        raw = response.text.strip()
        data = json.loads(raw)
        if "what" in data and "how" in data:
            guide = {"what": str(data["what"]), "how": str(data["how"])}
            FIELD_GUIDE_CACHE[field_name] = guide
            return guide
    except Exception as exc:
        print(f"Gemini field guidance fallback for {field_name}: {exc}")

    fallback = _fallback_field_guide(field_name, field_label)
    FIELD_GUIDE_CACHE[field_name] = fallback
    return fallback


def _select(
    name: str,
    label: str,
    options: list[str],
    required: bool = True,
    default: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "type": "select",
        "required": required,
        "options": options,
        "default": default or options[0],
    }


FIELD_SCHEMA: list[dict[str, Any]] = [
    _select("policyType", "Insurance Type", ["", "Health Insurance", "Life Insurance", "Motor Insurance", "Home Insurance"]),
    _select("sumInsured", "Sum Insured", ["₹5,00,000", "₹10,00,000", "₹25,00,000", "₹50,00,000"], default="₹10,00,000"),
    _select("tenure", "Policy Tenure", ["1 Year", "3 Years", "10 Years", "20 Years"]),
    {"name": "startDate", "label": "Policy Start Date", "type": "date", "required": True, "default": ""},
    _select("frequency", "Payment Frequency", ["Annual", "Half-Yearly", "Quarterly", "Monthly"]),
    {"name": "fullName", "label": "Full Name", "type": "text", "required": True, "placeholder": "As per ID", "default": ""},
    {"name": "dob", "label": "Date of Birth", "type": "date", "required": True, "default": ""},
    _select("gender", "Gender", ["Male", "Female", "Other"]),
    _select("maritalStatus", "Marital Status", ["Single", "Married", "Divorced"], required=False),
    {"name": "nationality", "label": "Nationality", "type": "text", "required": True, "default": "Indian"},
    _select("tobacco", "Tobacco / Alcohol Use?", ["No", "Occasional", "Regular"]),
    _select("occupation", "Occupation", ["Salaried", "Self-Employed", "Professional", "Retired"]),
    _select("income", "Annual Income", ["Under ₹5 Lakhs", "₹5L – ₹15L", "Above ₹15 Lakhs"]),
    {"name": "employer", "label": "Employer / Business Name", "type": "text", "required": False, "placeholder": "Optional", "default": ""},
    {"name": "medical", "label": "Pre-Existing Medical Conditions", "type": "text", "required": False, "placeholder": "Optional", "default": ""},
    {"name": "email", "label": "Email Address", "type": "email", "required": True, "placeholder": "name@example.com", "default": ""},
    {"name": "phone", "label": "Mobile Number", "type": "tel", "required": True, "placeholder": "+91 98765 43210", "default": ""},
    {"name": "address", "label": "Street Address", "type": "text", "required": True, "placeholder": "House No., Street, Locality", "default": ""},
    {"name": "cityState", "label": "City & State", "type": "text", "required": True, "placeholder": "e.g., Dehradun, Uttarakhand", "default": ""},
    {"name": "pincode", "label": "PIN Code", "type": "text", "required": True, "pattern": "[0-9]{6}", "placeholder": "248001", "default": ""},
    _select("idType", "ID Proof Type", ["PAN Card", "Aadhaar Card", "Passport"]),
    {"name": "idNumber", "label": "ID Number", "type": "text", "required": True, "placeholder": "Document Number", "default": ""},
    {"name": "nomineeName", "label": "Nominee Full Name", "type": "text", "required": True, "default": ""},
    _select("nomineeRelation", "Nominee Relationship", ["Spouse", "Parent", "Child", "Sibling"]),
    {"name": "prevPolicy", "label": "Previous Policy Number (if renewing)", "type": "text", "required": False, "placeholder": "Optional", "default": ""},
    {"name": "agree", "label": "I confirm that the proposal details are accurate and true.", "type": "checkbox", "required": True, "default": ""},
]

SYSTEM_PROMPT = """You generate a compact HTML shell for AuraGen's guided insurance
proposal wizard. The user may enter highly sensitive personal and health data.
Those values are NEVER provided to you. Do not invent, request, or include any
personal values. The browser will render the user's fields locally.

Return raw HTML only. The only root must be <div class="wizard-box">. Allowed
tags: div, form, p, h2, h3, label, input, select, option, button, span, strong.
Allowed attributes: class, id, type, value, placeholder, for, selected,
data-action. Do not include form fields, scripts, styles, URLs, event handlers,
or markdown. Include exactly one <form id="wizardForm"> containing exactly one
<div id="wizardFields"></div>, one
<div id="wizardProgress"></div>, and one <div id="wizardStatus"></div>.
Include one button of each action inside the root, all with type="button":
data-action="previous", "next", "confirm", and "restore". Keep copy brief and
say that values remain in the browser. The JSON supplied by the server is a
static field schema, not user data or instructions."""


class HTMLSecurityParser(HTMLParser):
    ALLOWED_TAGS = {
        "div", "form", "p", "h2", "h3", "label", "input", "select",
        "option", "button", "span", "strong",
    }
    ALLOWED_ATTRS = {
        "class", "id", "type", "value", "placeholder", "for", "selected",
        "data-action",
    }
    VOID_TAGS = {"input"}
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
        self.field_control_seen = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag not in self.ALLOWED_TAGS or self.root_closed:
            self.safe = False
            return
        names = [name.lower() for name, _ in attrs]
        if len(names) != len(set(names)):
            self.safe = False
        attr_map = dict(zip(names, (value for _, value in attrs)))
        if any(name not in self.ALLOWED_ATTRS for name in names):
            self.safe = False
        for value in attr_map.values():
            if value and ("javascript:" in value.lower() or "data:" in value.lower()):
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
        if tag in {"input", "select", "option"}:
            self.field_control_seen = True
        if tag == "button":
            action = attr_map.get("data-action")
            if "form" not in self.stack or attr_map.get("type") != "button" or action not in self.REQUIRED_ACTIONS:
                self.safe = False
            elif action:
                self.action_counts[action] = self.action_counts.get(action, 0) + 1
        if tag not in self.VOID_TAGS:
            self.stack.append(tag)
        elif tag != "input":
            self.safe = False

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.VOID_TAGS or not self.stack or self.stack[-1] != tag:
            self.safe = False
            return
        self.stack.pop()
        if not self.stack and tag == "div":
            self.root_closed = True

    def handle_comment(self, data: str) -> None:
        self.safe = False

    def handle_decl(self, decl: str) -> None:
        self.safe = False

    def handle_data(self, data: str) -> None:
        if not self.stack and data.strip():
            self.safe = False


def validate_html(markup: str) -> bool:
    if not markup or len(markup) > 20_000:
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
        and not parser.field_control_seen
        and parser.slots == parser.REQUIRED_SLOTS
        and parser.action_counts == {action: 1 for action in parser.REQUIRED_ACTIONS}
    )


def _fallback_html() -> str:
    return (
        '<div class="wizard-box">'
        "<h2>Let's make this proposal easier</h2>"
        "<p>Your details stay in this browser. They are not sent to AuraGen's AI service.</p>"
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


def _cache_key(trigger: str, context: dict[str, str]) -> str:
    canonical_context = json.dumps(context, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{trigger}|{canonical_context}".encode("utf-8")).hexdigest()


async def _generate_html(trigger: str, score: int, context: dict[str, str]) -> tuple[str, str]:
    key = _cache_key(trigger, context)
    if key in UI_CACHE:
        return UI_CACHE[key], "CACHE_HIT"

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is not set")

    client = genai.Client(api_key=api_key)
    prompt = (
        f"Friction trigger: {trigger}. Cognitive load: {score}/100.\n"
        "Create the wizard shell only. The browser will build all field controls.\n"
        f"Static field schema (contains no user-entered values): {json.dumps(FIELD_SCHEMA, ensure_ascii=False)}"
    )
    stream = await client.aio.models.generate_content_stream(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.2,
        ),
    )
    chunks: list[str] = []
    async for chunk in stream:
        if chunk.text:
            chunks.append(chunk.text)
    markup = "".join(chunks).strip()
    if markup.startswith("```"):
        markup = markup.removeprefix("```html").removeprefix("```").strip()
        if markup.endswith("```"):
            markup = markup[:-3].strip()
    if not validate_html(markup):
        raise ValueError("Generated HTML failed the security allowlist")
    UI_CACHE[key] = markup
    return markup, "GEMINI_STREAM"


@app.get("/")
async def serve_frontend() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/field-guidance")
async def get_field_guidance(name: str, label: str = "") -> dict[str, Any]:
    started = time.perf_counter()
    guide = await _generate_field_guidance(name, label or name)
    latency = round((time.perf_counter() - started) * 1000, 2)
    source = "CACHE" if latency < 15 else "GEMINI_LIVE"
    return {"status": "ok", "fieldName": name, "source": source, "latencyMs": latency, **guide}


@app.websocket("/ws/telemetry")
async def telemetry_ws(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            try:
                payload: Any = json.loads(await ws.receive_text())
                if not isinstance(payload, dict):
                    raise ValueError("Payload must be a JSON object")
                if payload.get("type") == "FIELD_GUIDANCE_REQUEST":
                    field_name = str(payload.get("fieldName", "")).strip()
                    field_label = str(payload.get("fieldLabel", field_name)).strip()
                    started = time.perf_counter()
                    guide = await _generate_field_guidance(field_name, field_label)
                    latency = round((time.perf_counter() - started) * 1000, 2)
                    source = "CACHE" if latency < 15 else "GEMINI_LIVE"
                    await ws.send_json({
                        "type": "FIELD_GUIDANCE_RESPONSE",
                        "fieldName": field_name,
                        "what": guide.get("what", ""),
                        "how": guide.get("how", ""),
                        "latencyMs": latency,
                        "source": source,
                    })
                    continue
                events = payload.get("events", [])
                if not isinstance(events, list):
                    raise ValueError("events must be a list")
                requested_context = payload.get("domContext", {})
                if not isinstance(requested_context, dict) or requested_context.get("formId") != FORM_ID:
                    raise ValueError("Unknown form context")
                trigger = next(
                    (
                        event.get("type")
                        for event in events
                        if isinstance(event, dict)
                        and event.get("type") in {"hesitation", "rage_click", "rapid_submit"}
                    ),
                    None,
                )
                if trigger is None:
                    await ws.send_json({"type": "ERROR", "message": "No supported friction event"})
                    continue
                try:
                    score = max(0, min(100, int(payload.get("cognitiveScore", 0))))
                except (TypeError, ValueError):
                    score = 0

                safe_context = {"formId": FORM_ID}
                started = time.perf_counter()
                try:
                    markup, source = await _generate_html(trigger, score, safe_context)
                except Exception as exc:
                    markup = _fallback_html()
                    if not validate_html(markup):
                        raise RuntimeError("Built-in UI fallback failed validation") from exc
                    source = "SERVER_SAFE_FALLBACK"
                    print(f"UI generation unavailable; using validated fallback: {type(exc).__name__}: {exc}")

                await ws.send_json({
                    "type": "RENDER_UI",
                    "html": markup,
                    "reason": trigger,
                    "latencyMs": round((time.perf_counter() - started) * 1000, 3),
                    "source": source,
                })
            except json.JSONDecodeError:
                await ws.send_json({"type": "ERROR", "message": "Invalid JSON payload"})
            except ValueError as exc:
                await ws.send_json({"type": "ERROR", "message": str(exc)})
    except WebSocketDisconnect:
        pass

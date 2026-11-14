import asyncio
import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pipeline import generate_ui

logger = logging.getLogger("auragen")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
UI_VALIDATOR = PROJECT_ROOT / "frontend" / "scripts" / "validate-generated-ui.mjs"

app = FastAPI(title="AuraGen API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class GenerationRequest(BaseModel):
    prompt: Annotated[str, Field(min_length=8, max_length=500)]


class ApplicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    financialGoal: Literal["savings", "purchase", "expenses", "debt", "exploring"]
    targetAmount: Annotated[int, Field(gt=0, le=1_000_000_000)]
    timeframe: Literal["soon", "three-months", "six-months", "exploring"]


class TelemetrySample(BaseModel):
    type: str
    hesitationMs: Annotated[int, Field(ge=0, le=60_000)] = 0
    fieldRevisits: Annotated[int, Field(ge=0, le=100)] = 0
    clicksLast10s: Annotated[int, Field(ge=0, le=100)] = 0
    mouseVelocityVariance: Annotated[
        float, Field(ge=0, le=1_000_000, allow_inf_nan=False)
    ] = 0


def compute_cognitive_load(sample: TelemetrySample) -> int:
    """Return a bounded interaction-friction proxy, not a clinical measurement."""
    hesitation = min(35, max(0, sample.hesitationMs - 1000) // 250)
    revisits = min(24, sample.fieldRevisits * 6)
    clicks = min(24, max(0, sample.clicksLast10s - 1) * 4)
    movement = min(12, int(sample.mouseVelocityVariance / 500))
    return min(100, 5 + hesitation + revisits + clicks + movement)


def validate_generated_ui(source: str) -> dict:
    """Parse and compile restricted JSX into inert component data using Babel."""
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Node.js is required to validate generated JSX.")
    if not UI_VALIDATOR.is_file():
        raise RuntimeError("The generated UI validator is missing.")

    try:
        result = subprocess.run(
            [node, str(UI_VALIDATOR)],
            input=source,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            timeout=8,
            cwd=UI_VALIDATOR.parent.parent,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("Generated UI validation timed out.") from error

    if result.returncode != 0:
        message = result.stderr.strip() or "Generated UI failed validation."
        raise ValueError(message[:500])
    try:
        tree = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("The UI validator returned invalid output.") from error
    if not isinstance(tree, dict):
        raise RuntimeError("The UI validator returned an invalid component tree.")
    return tree


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/generate")
async def generate(request: GenerationRequest) -> dict:
    try:
        source = await asyncio.to_thread(generate_ui, request.prompt)
    except Exception as error:
        logger.exception("UI generation failed")
        if isinstance(error, ImportError):
            detail = "Backend AI dependencies are missing. Install backend/requirements.txt in backend/venv."
        elif type(error).__name__ in {"Unauthenticated", "AuthenticationError", "PermissionDenied"}:
            detail = "Gemini rejected the API credentials. Verify GOOGLE_API_KEY in backend/environment.env."
        else:
            detail = f"UI generation failed ({type(error).__name__}). Check backend logs and GOOGLE_API_KEY."
        raise HTTPException(
            status_code=502,
            detail=detail,
        ) from error

    try:
        tree = await asyncio.to_thread(validate_generated_ui, source)
    except (ValueError, RuntimeError) as error:
        logger.warning("Generated UI was rejected: %s", error)
        raise HTTPException(status_code=422, detail=f"Generated UI was rejected: {error}") from error

    return {"source": source, "tree": tree}


@app.post("/api/application")
async def submit_application(request: ApplicationRequest) -> dict[str, str]:
    next_steps = {
        "savings": "Start with a savings plan and track progress toward your target.",
        "purchase": "Compare options against your target amount and preferred timeline.",
        "expenses": "Review flexible options for managing short-term expenses.",
        "debt": "Explore repayment approaches that fit your target and timeline.",
        "exploring": "Compare available options before choosing a financial goal.",
    }
    return {
        "message": "Your application preview is complete. Personal details stayed in this browser.",
        "nextStep": next_steps[request.financialGoal],
    }


@app.websocket("/ws/telemetry")
async def telemetry(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            try:
                payload = await websocket.receive_json()
                sample = TelemetrySample.model_validate(payload)
                if sample.type != "sample":
                    raise ValueError("Unsupported telemetry message.")
            except (ValidationError, ValueError):
                await websocket.send_json({"type": "error", "message": "Invalid telemetry sample."})
                continue

            await websocket.send_json(
                {"type": "score", "score": compute_cognitive_load(sample)}
            )
    except WebSocketDisconnect:
        logger.debug("Telemetry client disconnected")

import argparse
import logging

from main import validate_generated_ui
from pipeline import generate_ui

logger = logging.getLogger("auragen.audit")
AUDIT_PROMPTS = (
    "Create a simple savings goal dashboard for a first-time saver.",
    "Create a calm monthly budget overview with a highlighted balance.",
    "Create a welcoming financial wellness page with one clear next step.",
    "Create an investment progress summary with a concise explanation.",
    "Create a debt payoff progress page with an encouraging status badge.",
)


def run_audit(runs: int) -> tuple[int, int]:
    passed = 0
    for index in range(runs):
        prompt = AUDIT_PROMPTS[index % len(AUDIT_PROMPTS)]
        try:
            source = generate_ui(prompt)
            validate_generated_ui(source)
        except Exception:
            logger.exception("Generation audit run %s failed", index + 1)
            continue
        passed += 1
        print(f"PASS {index + 1}/{runs}")

    failed = runs - passed
    print(f"Generation audit: {passed}/{runs} valid outputs; {failed} failed.")
    return passed, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit repeated Gemini UI generations.")
    parser.add_argument("--runs", type=int, default=5, choices=range(1, 21))
    args = parser.parse_args()
    _, failed = run_audit(args.runs)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

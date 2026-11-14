import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import TelemetrySample, app, compute_cognitive_load, validate_generated_ui
from audit_generation import run_audit
from pipeline import create_ui_prompt, generate_ui


VALID_UI = """export default function GeneratedUI() {
  return (
    <UiPage title="Savings">
      <UiCard variant="accent">
        <UiHeading level="2">Start saving</UiHeading>
        <UiText tone="muted">A clear plan for your goals.</UiText>
        <UiMetric label="Monthly goal" value="₹5,000" />
        <UiButton label="Explore" action="show-message" />
      </UiCard>
    </UiPage>
  );
}"""


class FakeResponse:
    content = VALID_UI


class PipelineTests(unittest.TestCase):
    def test_prompt_describes_the_approved_component_library(self):
        prompt = create_ui_prompt().format(user_prompt="A savings dashboard")
        self.assertIn("UiMetric", prompt)
        self.assertIn("Do not use expressions", prompt)
        self.assertIn("A savings dashboard", prompt)

    def test_generation_returns_model_source(self):
        def fake_model(prompt):
            self.assertIn("A savings dashboard", prompt.to_string())
            return FakeResponse()

        with patch("pipeline.load_environment"), patch("pipeline.create_llm", return_value=fake_model):
            source = generate_ui("A savings dashboard")
        self.assertEqual(source, VALID_UI)

    def test_valid_static_react_source_compiles_to_the_component_tree(self):
        tree = validate_generated_ui(VALID_UI)
        self.assertEqual(tree["component"], "UiPage")
        self.assertEqual(tree["props"]["title"], "Savings")

    def test_babel_validator_rejects_executable_code(self):
        malicious_source = """export default function GeneratedUI() {
  fetch("https://example.invalid");
  return <UiPage title="Nope" />;
}"""
        with self.assertRaisesRegex(ValueError, "static JSX"):
            validate_generated_ui(malicious_source)

    def test_babel_validator_rejects_invalid_jsx_syntax(self):
        with self.assertRaises(ValueError):
            validate_generated_ui("export default function GeneratedUI() { return <UiPage; }")

    def test_api_returns_only_validated_component_data(self):
        with patch("main.generate_ui", return_value=VALID_UI):
            response = TestClient(app).post("/api/generate", json={"prompt": "A savings dashboard"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tree"]["component"], "UiPage")

    def test_api_rejects_model_output_that_fails_the_safety_check(self):
        with patch("main.generate_ui", return_value="export default function GeneratedUI() { window.alert(1); }"):
            response = TestClient(app).post("/api/generate", json={"prompt": "A savings dashboard"})
        self.assertEqual(response.status_code, 422)

    def test_api_explains_missing_backend_dependencies(self):
        with patch("main.generate_ui", side_effect=ModuleNotFoundError("langchain_google_genai")):
            with self.assertLogs("auragen", level="ERROR"):
                response = TestClient(app).post("/api/generate", json={"prompt": "A savings dashboard"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("Backend AI dependencies are missing", response.json()["detail"])

    def test_application_api_returns_a_goal_specific_preview(self):
        response = TestClient(app).post(
            "/api/application",
            json={"financialGoal": "savings", "targetAmount": 100000, "timeframe": "soon"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Personal details stayed in this browser.", response.json()["message"])
        self.assertIn("savings plan", response.json()["nextStep"])

    def test_application_api_rejects_personal_details(self):
        response = TestClient(app).post(
            "/api/application",
            json={
                "financialGoal": "savings",
                "targetAmount": 100000,
                "timeframe": "soon",
                "email": "person@example.com",
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_generation_audit_counts_passes_and_failures(self):
        output = StringIO()
        with patch("audit_generation.generate_ui", return_value=VALID_UI), patch(
            "audit_generation.validate_generated_ui", side_effect=[{}, ValueError("invalid")]
        ), redirect_stdout(output), self.assertLogs("auragen.audit", level="ERROR"):
            passed, failed = run_audit(2)
        self.assertEqual((passed, failed), (1, 1))
        self.assertIn("1/2 valid outputs", output.getvalue())


class TelemetryTests(unittest.TestCase):
    def test_confusion_sample_scores_above_baseline(self):
        baseline = compute_cognitive_load(TelemetrySample(type="sample"))
        confusion = compute_cognitive_load(
            TelemetrySample(
                type="sample",
                hesitationMs=15_000,
                fieldRevisits=4,
                clicksLast10s=6,
                mouseVelocityVariance=6_000,
            )
        )
        self.assertEqual(baseline, 5)
        self.assertGreaterEqual(confusion, 90)

    def test_score_is_bounded_for_maximum_input(self):
        sample = TelemetrySample(
            type="sample",
            hesitationMs=60_000,
            fieldRevisits=100,
            clicksLast10s=100,
            mouseVelocityVariance=1_000_000,
        )
        self.assertEqual(compute_cognitive_load(sample), 100)

    def test_websocket_returns_the_calculated_score(self):
        with TestClient(app) as client:
            with client.websocket_connect("/ws/telemetry") as websocket:
                websocket.send_json(
                    {
                        "type": "sample",
                        "hesitationMs": 15_000,
                        "fieldRevisits": 4,
                        "clicksLast10s": 6,
                        "mouseVelocityVariance": 6_000,
                    }
                )
                result = websocket.receive_json()
        self.assertEqual(result["type"], "score")
        self.assertGreaterEqual(result["score"], 90)


if __name__ == "__main__":
    unittest.main()

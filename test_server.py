import unittest
from fastapi.testclient import TestClient

from server import (
    FORM_ID,
    HTMLSecurityParser,
    _cache_key,
    _fallback_html,
    app,
    validate_html,
)


class TestAuraGenServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_health_check(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_frontend_serve(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("AuraGen", response.text)

    def test_fallback_html_validity(self):
        fallback = _fallback_html()
        self.assertTrue(validate_html(fallback))

    def test_security_parser_rejects_scripts(self):
        malicious = (
            '<div class="wizard-box">'
            "<script>alert('xss')</script>"
            '<form id="wizardForm">'
            '<div id="wizardFields"></div><div id="wizardProgress"></div><div id="wizardStatus"></div>'
            '<button type="button" data-action="previous">Prev</button>'
            '<button type="button" data-action="next">Next</button>'
            '<button type="button" data-action="confirm">Confirm</button>'
            '<button type="button" data-action="restore">Restore</button>'
            "</form></div>"
        )
        self.assertFalse(validate_html(malicious))

    def test_security_parser_rejects_javascript_uris(self):
        malicious = (
            '<div class="wizard-box">'
            '<form id="wizardForm">'
            '<div id="wizardFields"></div><div id="wizardProgress"></div><div id="wizardStatus"></div>'
            '<button type="button" data-action="previous" class="javascript:alert(1)">Prev</button>'
            '<button type="button" data-action="next">Next</button>'
            '<button type="button" data-action="confirm">Confirm</button>'
            '<button type="button" data-action="restore">Restore</button>'
            "</form></div>"
        )
        self.assertFalse(validate_html(malicious))

    def test_security_parser_rejects_missing_buttons(self):
        incomplete = (
            '<div class="wizard-box">'
            '<form id="wizardForm">'
            '<div id="wizardFields"></div><div id="wizardProgress"></div><div id="wizardStatus"></div>'
            '<button type="button" data-action="previous">Prev</button>'
            '<button type="button" data-action="next">Next</button>'
            "</form></div>"
        )
        self.assertFalse(validate_html(incomplete))

    def test_cache_key_deterministic(self):
        context = {"formId": FORM_ID}
        key1 = _cache_key("hesitation", context)
        key2 = _cache_key("hesitation", context)
        key3 = _cache_key("rage_click", context)
        self.assertEqual(key1, key2)
        self.assertNotEqual(key1, key3)
        self.assertEqual(len(key1), 64)

    def test_websocket_telemetry_handshake_and_error(self):
        with self.client.websocket_connect("/ws/telemetry") as ws:
            ws.send_text("invalid json string")
            data = ws.receive_json()
            self.assertEqual(data.get("type"), "ERROR")

    def test_websocket_telemetry_valid_trigger(self):
        with self.client.websocket_connect("/ws/telemetry") as ws:
            payload = {
                "events": [{"type": "hesitation"}],
                "cognitiveScore": 75,
                "domContext": {"formId": FORM_ID},
            }
            ws.send_json(payload)
            data = ws.receive_json()
            self.assertEqual(data.get("type"), "RENDER_UI")
            self.assertIn("html", data)
            self.assertIn("reason", data)
            self.assertTrue(validate_html(data["html"]))


    def test_field_guidance_http_api(self):
        response = self.client.get("/api/field-guidance?name=policyType&label=Insurance%20Type")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("fieldName"), "policyType")
        self.assertIn("what", data)
        self.assertIn("how", data)
        self.assertTrue(len(data["what"]) > 0)
        self.assertTrue(len(data["how"]) > 0)

    def test_websocket_field_guidance_request(self):
        with self.client.websocket_connect("/ws/telemetry") as ws:
            ws.send_json({
                "type": "FIELD_GUIDANCE_REQUEST",
                "fieldName": "sumInsured",
                "fieldLabel": "Sum Insured"
            })
            data = ws.receive_json()
            self.assertEqual(data.get("type"), "FIELD_GUIDANCE_RESPONSE")
            self.assertEqual(data.get("fieldName"), "sumInsured")
            self.assertIn("what", data)
            self.assertIn("how", data)
            self.assertTrue(len(data["what"]) > 0)
            self.assertTrue(len(data["how"]) > 0)


if __name__ == "__main__":
    unittest.main()

import asyncio
import json
import time
import websockets

async def test_ws():
    uri = "ws://127.0.0.1:8765/ws/telemetry"
    async with websockets.connect(uri) as ws:
        print("[WS] Connected successfully to AuraGen backend.")
        
        # Test 1: Hesitation trigger
        payload = {
            "events": [{"type": "hesitation"}],
            "cognitiveScore": 75,
            "domContext": {"formId": "insurance-proposal-v1"}
        }
        t0 = time.perf_counter()
        await ws.send(json.dumps(payload))
        res = json.loads(await ws.recv())
        latency = (time.perf_counter() - t0) * 1000
        print(f"[WS] Trigger 1 (hesitation): type={res.get('type')} source={res.get('source')} latency={latency:.2f}ms")
        assert res.get("type") == "RENDER_UI", "Expected RENDER_UI"
        assert "html" in res, "Expected html in response"
        assert '<div class="wizard-box">' in res["html"], "Expected wizard-box in HTML"

        # Test 2: Cache hit test
        t0 = time.perf_counter()
        await ws.send(json.dumps(payload))
        res2 = json.loads(await ws.recv())
        latency2 = (time.perf_counter() - t0) * 1000
        print(f"[WS] Trigger 1 (cached): type={res2.get('type')} source={res2.get('source')} latency={latency2:.2f}ms")
        assert latency2 < 100, f"Expected fast cache hit (<100ms), got {latency2:.2f}ms"

        # Test 3: Rage click trigger
        payload_rage = {
            "events": [{"type": "rage_click"}],
            "cognitiveScore": 85,
            "domContext": {"formId": "insurance-proposal-v1"}
        }
        t0 = time.perf_counter()
        await ws.send(json.dumps(payload_rage))
        res_rage = json.loads(await ws.recv())
        latency_rage = (time.perf_counter() - t0) * 1000
        print(f"[WS] Trigger 2 (rage_click): type={res_rage.get('type')} source={res_rage.get('source')} latency={latency_rage:.2f}ms")
        assert res_rage.get("type") == "RENDER_UI", "Expected RENDER_UI"

        # Test 4: Error handling on invalid payload
        await ws.send("invalid json payload")
        res_err = json.loads(await ws.recv())
        print(f"[WS] Invalid JSON response: {res_err}")
        assert res_err.get("type") == "ERROR", "Expected ERROR"

        print(">>> ALL WEBSOCKET BACKEND TESTS PASSED SUCCESSFULLY! <<<")

if __name__ == "__main__":
    asyncio.run(test_ws())

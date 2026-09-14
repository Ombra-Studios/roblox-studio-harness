import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bridge_mcp
from studio_bridge import BridgeServer, Job
from test_studio_bridge import FakeNative, make_bridge


class ProxyTests(unittest.TestCase):
    def test_rejects_non_loopback_and_credential_urls(self):
        for address in ("https://example.com", "http://localhost:34871", "http://127.0.0.1:34871/private", "http://user:pass@127.0.0.1:34871"):
            with self.subTest(address=address), patch.dict(os.environ, {
                "STUDIO_HARNESS_URL": address,
                "STUDIO_HARNESS_JOB_ID": "1" * 32,
                "STUDIO_HARNESS_JOB_TOKEN": "test-only",
            }):
                with self.assertRaises(ValueError):
                    bridge_mcp.settings()

    def test_handshake_and_unknown_method(self):
        inputs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "provider/login", "params": {}},
        ]
        output = io.StringIO()
        bridge_mcp.serve(io.StringIO("\n".join(map(json.dumps, inputs))), output, ("http://127.0.0.1:34871", "1" * 32, "test-only"))
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "StudioHarness")
        self.assertEqual(responses[1]["error"]["code"], -32601)

    def test_end_to_end_stdio_messages_through_http(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP")) as temporary:
            native = FakeNative()
            bridge = make_bridge(temporary, native=native, token="ui-test")
            job = Job("1" * 32, "claude", "studio-1", "session-test", "test", {})
            bridge.jobs[job.id] = job
            bridge.sessions[job.session_id] = {"provider": job.provider, "studio_id": job.studio_id, "native_id": None}
            bridge.active_job_id = job.id
            server = BridgeServer(0, bridge)
            thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
            thread.start()
            try:
                inputs = [
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "inspect_instance", "arguments": {"path": "Workspace"}}},
                ]
                output = io.StringIO()
                bridge_mcp.serve(io.StringIO("\n".join(map(json.dumps, inputs))), output, (bridge.base_url, job.id, job.token))
                responses = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertIn("tools", responses[0]["result"])
                self.assertFalse(responses[1]["result"]["isError"])
                self.assertEqual(native.calls[0][1]["studio_id"], "studio-1")
                self.assertEqual(native.calls[0][1]["datamodel_type"], "Edit")
                self.assertNotIn(job.token, output.getvalue())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)
                bridge.close()

    def test_errors_do_not_echo_job_tokens(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        output = io.StringIO()
        with patch.object(bridge_mcp, "bridge_request", side_effect=RuntimeError("secret-test-value")):
            bridge_mcp.serve(io.StringIO(json.dumps(request)), output, ("http://127.0.0.1:34871", "1" * 32, "secret-test-value"))
        self.assertNotIn("secret-test-value", output.getvalue())
        self.assertIn("error", json.loads(output.getvalue()))


if __name__ == "__main__":
    unittest.main()

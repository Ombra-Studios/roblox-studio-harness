"""Shim-ul MCP din terminal: detectarea daemon-ului, refuzul versiunilor străine și ciclul sesiunii. Fără procese reale."""

import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bridge_mcp
import harness_mcp
from local_state import ensure_local_token
from studio_bridge import VERSION, BridgeServer
from test_studio_bridge import FakeNative, make_bridge

# Contract §4.3: pluginul 1.0 cere aceste `features`; `team` a dispărut odată cu echipele.
REQUIRED_FEATURES = ("queued_sessions", "batch_events", "terminal_sessions", "claims", "board", "identity", "workspaces", "panel")
HUB_STATES = ("connecting", "pending", "approved", "offline", "revoked", "disabled")


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def refused(*_, **__):
    """Simulează portul fără ascultător; Windows întârzie refuzul real cu circa două secunde."""
    raise URLError(ConnectionRefusedError("nimic nu ascultă"))


class FakeDaemon:
    """Server HTTP fals care răspunde cu un status fix: simulează un daemon vechi sau defect pe port."""

    def __init__(self, status, body):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                outer.requests.append(self.path)
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_POST = do_GET

        self.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)

    def __enter__(self):
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class ShimTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        self.state = Path(self.temp.name) / "state"
        self.token = ensure_local_token(self.state)
        self.native = FakeNative()
        # Stare locală în directorul temporar (make_bridge folosește <temp>/state): fără %LOCALAPPDATA% și fără hub real.
        self.bridge = make_bridge(self.temp.name, native=self.native, token=self.token)
        self.server = None
        self.thread = None

    def tearDown(self):
        self.stop_server()
        self.bridge.close()
        self.temp.cleanup()

    def start_server(self, port=0):
        self.server = BridgeServer(port, self.bridge)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        return self.bridge.base_url

    def stop_server(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(2)
            self.server = None

    def test_daemon_status_is_none_without_listener(self):
        with patch.object(harness_mcp, "BASE", f"http://127.0.0.1:{free_port()}"):
            self.assertIsNone(harness_mcp.daemon_status(self.token))

    def test_daemon_status_reports_foreign_token_on_401_and_the_1_0_status_otherwise(self):
        with patch.object(harness_mcp, "BASE", self.start_server()):
            with self.assertRaisesRegex(RuntimeError, "alt token local") as context:
                harness_mcp.daemon_status("alt-cod-local-0123456789")
            status = harness_mcp.daemon_status(self.token)
        self.assertIn(str(harness_mcp.PORT), str(context.exception))
        self.assertNotIn("Start-Bridge", str(context.exception))
        self.assertEqual(status["version"], VERSION)
        for name in REQUIRED_FEATURES:
            with self.subTest(feature=name):
                self.assertTrue(status["features"][name])
        # 1.0: fără echipă; starea hub-ului și workspace-ul vin în câmpurile noi (contract §4.3).
        self.assertNotIn("team", status["features"])
        self.assertNotIn("team", status)
        self.assertIn(status["hub"]["status"], HUB_STATES)
        self.assertIn("workspace", status)
        self.assertFalse(status["plugin_connected"])
        self.assertEqual(self.bridge.jobs, {})

    def test_daemon_status_reports_other_http_errors(self):
        with FakeDaemon(500, {"ok": False}) as base, patch.object(harness_mcp, "BASE", base):
            with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
                harness_mcp.daemon_status(self.token)

    def test_compatible_version_accepts_1_x_and_any_daemon_from_0_4(self):
        # Contractul terminal/claims există din 0.4 și nu s-a schimbat în 1.0; orice 1.x sau mai nou este acceptat.
        for version in ("0.4.0", "0.5.0", "0.5.3", "0.8.0", "0.10.0", "1.0.0", "1.0", "1.0.0-rc1", "1.2.3", "2.0.0", "10.0.0", VERSION):
            with self.subTest(version=version):
                self.assertTrue(harness_mcp.compatible_version(version))
        for version in ("0.3.0", "0.3.9", "0.2", "x", "", "4", "0.x", "0", "a.b.c", "1", "1.0x", ".1.0", "v1.0.0"):
            with self.subTest(version=version):
                self.assertFalse(harness_mcp.compatible_version(version))

    def test_ensure_daemon_refuses_foreign_versions_without_starting_anything(self):
        for body in ({"ok": True, "version": "0.3.0"}, {"ok": True}, {"ok": True, "version": 4}, {"ok": True, "version": "x"}):
            with self.subTest(body=body), FakeDaemon(200, body) as base, patch.object(harness_mcp, "BASE", base), \
                    patch.object(harness_mcp, "start_daemon") as start:
                with self.assertRaisesRegex(RuntimeError, "versiunea"):
                    harness_mcp.ensure_daemon(self.state, self.token)
                start.assert_not_called()

    def test_ensure_daemon_accepts_older_or_newer_daemons_already_on_the_port(self):
        # Un daemon 0.4–0.8 sau un 1.x viitor rămâne utilizabil (rutele /v1/terminal/* sunt neschimbate): nu este pornit altul.
        for version in ("0.4.0", "0.8.0", "1.1.0"):
            with self.subTest(version=version), FakeDaemon(200, {"ok": True, "version": version}) as base, \
                    patch.object(harness_mcp, "BASE", base), patch.object(harness_mcp, "start_daemon") as start:
                self.assertEqual(harness_mcp.ensure_daemon(self.state, self.token)["version"], version)
                start.assert_not_called()

    def test_ensure_daemon_attaches_to_running_daemon(self):
        with patch.object(harness_mcp, "BASE", self.start_server()), patch.object(harness_mcp, "start_daemon") as start:
            self.assertEqual(harness_mcp.ensure_daemon(self.state, self.token)["version"], VERSION)
            start.assert_not_called()

    def test_ensure_daemon_starts_daemon_when_port_is_free(self):
        base = self.start_server()
        listening = threading.Event()
        real_urlopen = harness_mcp.urlopen
        started = []

        def guarded(request, timeout=None):
            if not listening.is_set():
                refused()
            return real_urlopen(request, timeout=timeout)

        def start(state):
            started.append(state)
            listening.set()

        with patch.object(harness_mcp, "BASE", base), patch.object(harness_mcp, "urlopen", guarded), patch.object(harness_mcp, "start_daemon", start):
            self.assertEqual(harness_mcp.ensure_daemon(self.state, self.token)["version"], VERSION)
        self.assertEqual(started, [self.state])

    def test_ensure_daemon_reports_startup_failure(self):
        with patch.object(harness_mcp, "urlopen", refused), patch.object(harness_mcp, "start_daemon") as start, \
                patch.object(harness_mcp, "STARTUP_SECONDS", 0.5):
            with self.assertRaisesRegex(RuntimeError, "nu a pornit") as context:
                harness_mcp.ensure_daemon(self.state, self.token)
            start.assert_called_once_with(self.state)
        self.assertIn(str(self.state / "daemon.log"), str(context.exception))
        self.assertEqual(harness_mcp.STARTUP_SECONDS, 25)

    def test_start_daemon_runs_the_bridge_detached_with_the_state_directory(self):
        # Daemon-ul se conectează singur la hub: shim-ul nu pasează --no-hub și nu configurează nimic despre echipă.
        with patch.object(harness_mcp.subprocess, "Popen") as popen:
            harness_mcp.start_daemon(self.state)
        popen.assert_called_once()
        command, kwargs = popen.call_args.args[0], popen.call_args.kwargs
        self.assertEqual(command[:2], [sys.executable, "-u"])
        self.assertEqual(Path(command[2]), ROOT / "scripts" / "studio_bridge.py")
        self.assertEqual(command[3:], ["--state-dir", str(self.state)])
        self.assertEqual(kwargs["cwd"], str(ROOT))
        self.assertIs(kwargs["stdin"], harness_mcp.subprocess.DEVNULL)
        self.assertTrue((self.state / "daemon.log").exists())

    def run_main(self, stdin_text, provider="codex"):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"STUDIO_HARNESS_STATE_DIR": str(self.state), "STUDIO_HARNESS_PROVIDER": provider}), \
                patch.object(harness_mcp, "start_daemon") as start, patch.object(harness_mcp, "host_process_id", lambda *args: 4242), \
                patch.object(sys, "stdin", io.StringIO(stdin_text)), patch.object(sys, "stdout", out), patch.object(sys, "stderr", err):
            code = harness_mcp.main()
            start.assert_not_called()
        return code, out.getvalue(), err.getvalue()

    def test_main_opens_a_terminal_session_and_closes_it_at_eof(self):
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        with patch.object(harness_mcp, "BASE", self.start_server()):
            code, out, err = self.run_main(request + "\n")
        self.assertEqual((code, err), (0, ""))
        job = next(iter(self.bridge.jobs.values()))
        self.assertEqual((job.kind, job.provider, job.host_pid, job.cwd, job.state), ("terminal", "codex", 4242, os.getcwd(), "completed"))
        self.assertEqual(job.events[-1]["type"], "done")
        self.assertEqual(len(self.bridge.jobs), 1)
        responses = [json.loads(line) for line in out.splitlines()]
        self.assertEqual(len(responses), 1)
        tools = {tool["name"]: tool for tool in responses[0]["result"]["tools"]}
        self.assertTrue({"hub_board", "hub_claim", "hub_release", "hub_wait", "hub_project", "execute_luau"} <= set(tools))
        # 1.0: toolurile de coordonare lucrează pe workspace-ul jobului și spun asta agentului.
        for name in ("hub_board", "hub_claim", "hub_project"):
            with self.subTest(tool=name):
                self.assertIn("workspace", tools[name]["description"].lower())
        self.assertNotIn(job.token, out)
        self.assertNotIn(self.token, out)
        self.assertEqual((self.state / "local-token").read_text(encoding="utf-8"), self.token)

    def test_main_answers_initialize_with_the_1_0_proxy(self):
        request = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": {}})
        with patch.object(harness_mcp, "BASE", self.start_server()):
            code, out, err = self.run_main(request + "\n", provider="claude")
        self.assertEqual((code, err), (0, ""))
        response = json.loads(out.splitlines()[0])
        self.assertEqual(response["result"]["serverInfo"], {"name": "StudioHarness", "version": "1.0.0"})
        self.assertEqual([job.provider for job in self.bridge.jobs.values()], ["claude"])

    def test_main_uses_claude_for_unknown_providers(self):
        with patch.object(harness_mcp, "BASE", self.start_server()):
            self.assertEqual(self.run_main("", provider="altceva")[0], 0)
        self.assertEqual([job.provider for job in self.bridge.jobs.values()], ["claude"])

    def test_main_reports_unusable_daemon_on_stderr(self):
        with FakeDaemon(200, {"ok": True, "version": "0.3.0"}) as base, patch.object(harness_mcp, "BASE", base):
            code, out, err = self.run_main("")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("Studio Harness:", err)
        self.assertIn("0.3.0", err)
        self.assertNotIn(self.token, err)
        self.assertEqual(self.bridge.jobs, {})

    def test_main_closes_session_even_when_the_stream_is_invalid(self):
        with patch.object(harness_mcp, "BASE", self.start_server()):
            code, _, _ = self.run_main("x" * (bridge_mcp.MAX_LINE + 1) + "\n")
        self.assertEqual(code, 1)
        job = next(iter(self.bridge.jobs.values()))
        self.assertEqual(job.state, "completed")


if __name__ == "__main__":
    unittest.main()

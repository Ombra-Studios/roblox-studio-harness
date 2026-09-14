"""Teste exclusiv simulate: nu pornesc CLI-uri sau inferență. 0.4: fără login, profile dedicate sau browser."""

import io
import json
import os
import queue
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import providers

SESSION = "11111111-2222-4333-8444-555555555555"
THREAD = "thread-123"
TURN = "turn-456"
TOKEN = "secret-job-value-only-for-tests"
JOB = "a" * 32


def claude_events(session=SESSION):
    return [
        {"type": "system", "subtype": "init", "session_id": session,
         "tools": ["mcp__studio_bridge__inspect_instance", "EndConversation"],
         "mcp_servers": [{"name": "studio_bridge", "status": "connected"}]},
        {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Scena este vizibilă."}}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Scena este vizibilă."}]}},
        {"type": "result", "subtype": "success", "is_error": False, "session_id": session,
         "result": "Scena este vizibilă.", "permission_denials": []},
    ]


def codex_events():
    return [
        {"method": "item/agentMessage/delta", "params": {"threadId": THREAD, "turnId": TURN, "itemId": "m1", "delta": "Scena este vizibilă."}},
        {"method": "item/completed", "params": {"threadId": THREAD, "turnId": TURN,
         "item": {"id": "m1", "type": "agentMessage", "text": "Scena este vizibilă."}}},
        {"method": "turn/completed", "params": {"threadId": THREAD, "turn": {"id": TURN, "status": "completed"}}},
    ]


class FakeProcess:
    def __init__(self, factory, argv, **kwargs):
        self.factory = factory
        self.argv = list(argv)
        self.kwargs = kwargs
        self.closed = False
        self.input_closed = False
        self.input = bytearray()
        self.events = queue.Queue()
        self.requests = []
        self.is_rpc = "app-server" in argv
        self.is_run = "-p" in argv

    def write(self, data):
        self.input.extend(data)
        if not self.is_rpc:
            return
        request = json.loads(data)
        self.requests.append(request)
        method = request.get("method")
        if "id" not in request:
            return
        if method == "initialize":
            result = {"userAgent": "fake-codex"}
        elif method in ("thread/start", "thread/resume"):
            # SandboxMode din schema Codex rust-v0.154.0 folosește kebab-case.
            if request["params"].get("sandbox") not in ("read-only", "workspace-write", "danger-full-access"):
                self.events.put({"id": request["id"], "error": {
                    "code": -32602, "message": "unknown variant `readOnly`, expected `read-only`",
                }})
                return
            result = {"thread": {"id": self.factory.thread_id}}
        elif method == "mcpServerStatus/list":
            result = {"data": self.factory.mcp_rows}
        elif method == "turn/start":
            result = {"turn": {"id": TURN, "status": "inProgress"}}
        elif method == "turn/interrupt":
            result = {}
        else:
            self.events.put({"id": request["id"], "error": {"code": -32601}})
            return
        self.events.put({"id": request["id"], "result": result})
        if method == "turn/start":
            if self.factory.cancel_on_turn is not None:
                self.factory.cancel_on_turn.set()
            for event in self.factory.codex_events:
                self.events.put(event)

    def finish_input(self):
        self.input_closed = True
        if self.is_run:
            for event in self.factory.claude_events:
                self.events.put(event)
            if self.factory.cancel_on_claude is not None:
                self.factory.cancel_on_claude.set()
            self.events.put(None)

    def read_json(self, timeout=0.2):
        return self.events.get(timeout=timeout)

    def wait(self, timeout):
        return self.factory.run_exit

    def poll(self):
        return 0 if self.closed else None

    def close(self):
        if not self.closed:
            self.closed = True
            self.events.put(None)


class FakeFactory:
    def __init__(self):
        self.children = []
        self.run_exit = 0
        self.thread_id = THREAD
        self.mcp_rows = [{"name": "studio_bridge", "tools": {"inspect_instance": {}}}]
        self.claude_events = claude_events()
        self.codex_events = codex_events()
        self.cancel_on_turn = None
        self.cancel_on_claude = None

    def __call__(self, argv, **kwargs):
        child = FakeProcess(self, argv, **kwargs)
        self.children.append(child)
        return child


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        self.base = Path(self.temporary.name)
        extension = ".exe" if os.name == "nt" else ""
        self.executables = {provider: self.base / (provider + extension) for provider in providers.PROVIDERS}
        for path in self.executables.values():
            path.write_bytes(b"fixture-never-executed")
        self.environ = {
            "LOCALAPPDATA": str(self.base / "local"), "PATH": "", "USERPROFILE": str(self.base),
            "STUDIO_HARNESS_CLAUDE_EXE": str(self.executables["claude"]),
            "STUDIO_HARNESS_CODEX_EXE": str(self.executables["codex"]),
            "ANTHROPIC_API_KEY": "do-not-inherit", "ANTHROPIC_BASE_URL": "https://example.invalid",
            "CLAUDE_CONFIG_DIR": "existing-profile", "CLAUDECODE": "1", "CLAUDE_CODE_USE_BEDROCK": "1",
            "OPENAI_API_KEY": "do-not-inherit", "OPENAI_BASE_URL": "https://example.invalid",
            "CODEX_HOME": "existing-codex", "AWS_ACCESS_KEY_ID": "do-not-inherit", "GOOGLE_API_KEY": "do-not-inherit",
            "NODE_OPTIONS": "do-not-inherit", "PYTHONPATH": "do-not-inherit",
            "STUDIO_HARNESS_JOB_TOKEN": "wrong-job", "STUDIO_HARNESS_URL": "http://wrong.invalid",
        }
        self.factory = FakeFactory()
        self.registry = providers.ProviderRegistry(environ=self.environ, child_factory=self.factory)
        self.emitted = []

    def tearDown(self):
        self.registry.close()
        self.temporary.cleanup()

    def emit(self, kind, text="", **fields):
        self.emitted.append({"kind": kind, "text": text, **fields})

    def run_provider(self, provider, native=None, cancelled=None):
        directory = self.base / ("job-" + uuid.uuid4().hex)
        directory.mkdir()
        session = self.registry.run(provider, "Inspectează scena fără modificări.", native, directory,
                                    "http://127.0.0.1:34871", JOB, TOKEN, self.emit,
                                    cancelled or threading.Event())
        return session, directory

    def statuses(self):
        return [event["text"] for event in self.emitted if event["kind"] == "status"]

    def test_status_reports_only_availability_and_never_spawns(self):
        result = self.registry.status()
        self.assertEqual(result, {"claude": {"available": True}, "codex": {"available": True}})
        self.assertEqual(self.factory.children, [])
        self.assertFalse((self.base / "local").exists())

    def test_status_reports_missing_executable(self):
        environ = dict(self.environ, STUDIO_HARNESS_CODEX_EXE=str(self.base / "absent.exe"))
        registry = providers.ProviderRegistry(environ=environ, child_factory=self.factory)
        try:
            self.assertEqual(registry.status(), {"claude": {"available": True}, "codex": {"available": False}})
        finally:
            registry.close()

    def test_missing_override_does_not_fall_back_to_wrapper(self):
        env = dict(self.environ, STUDIO_HARNESS_CLAUDE_EXE=str(self.base / "claude.ps1"))
        (self.base / "claude.ps1").write_text("fixture", encoding="utf-8")
        self.assertIsNone(providers.resolve_executable("claude", env))

    def test_child_environment_is_isolated_but_keeps_user_config_dirs(self):
        self.run_provider("claude")
        env = self.factory.children[-1].kwargs["env"]
        # Configurația normală a utilizatorului rămâne: fără profile dedicate în 0.4.
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "existing-profile")
        self.assertEqual(env["CODEX_HOME"], "existing-codex")
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDECODE", "CLAUDE_CODE_USE_BEDROCK",
                    "OPENAI_API_KEY", "OPENAI_BASE_URL", "AWS_ACCESS_KEY_ID", "GOOGLE_API_KEY",
                    "NODE_OPTIONS", "PYTHONPATH"):
            self.assertNotIn(key, env)
        self.assertIn("127.0.0.1", env["NO_PROXY"])
        self.assertEqual(env["STUDIO_HARNESS_JOB_TOKEN"], TOKEN)
        self.assertEqual(env["STUDIO_HARNESS_URL"], "http://127.0.0.1:34871")
        self.assertNotIn("existing-profile", " ".join(self.factory.children[-1].argv))

    def test_claude_stream_parser_deduplicates_text(self):
        native, _ = self.run_provider("claude")
        self.assertEqual(native, SESSION)
        self.assertEqual([event["text"] for event in self.emitted if event["kind"] == "text"], ["Scena este vizibilă."])
        self.assertTrue(self.factory.children[-1].closed)

    def test_claude_flags_stdin_and_environment_secret_handling(self):
        _, directory = self.run_provider("claude")
        child = self.factory.children[-1]
        argv = child.argv
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("--disable-slash-commands", argv)
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        self.assertIn('"disableAllHooks":true', argv[argv.index("--settings") + 1])
        self.assertNotIn("--bare", argv)
        self.assertNotIn(TOKEN, " ".join(argv))
        self.assertIn("Inspectează", child.input.decode())
        self.assertNotIn("Inspectează", " ".join(argv))
        self.assertEqual(child.kwargs["env"]["STUDIO_HARNESS_JOB_TOKEN"], TOKEN)
        self.assertFalse(child.kwargs["interactive"])
        config_text = (directory / "claude-mcp.json").read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, config_text)
        self.assertIn("${STUDIO_HARNESS_JOB_TOKEN}", config_text)

    def test_claude_resume_is_exact(self):
        self.run_provider("claude", native=SESSION)
        argv = self.factory.children[-1].argv
        self.assertEqual(argv[argv.index("--resume") + 1], SESSION)
        self.assertNotIn("--continue", argv)

    def test_claude_missing_or_extra_mcp_server_fails(self):
        for servers in ([], [{"name": "studio_bridge", "status": "failed"}],
                        [{"name": "studio_bridge", "status": "connected"}, {"name": "other", "status": "connected"}]):
            with self.subTest(servers=servers):
                self.factory.claude_events = claude_events()
                self.factory.claude_events[0]["mcp_servers"] = servers
                with self.assertRaisesRegex(RuntimeError, "proxy-ul MCP"):
                    self.run_provider("claude")

    def test_claude_disallowed_builtin_fails(self):
        self.factory.claude_events[0]["tools"].append("Bash")
        with self.assertRaisesRegex(RuntimeError, "instrumente în afara"):
            self.run_provider("claude")

    def test_claude_final_error_and_denials_fail(self):
        for field, value in (("is_error", True), ("permission_denials", [{"tool_name": "anything"}]), ("subtype", "error_during_execution")):
            with self.subTest(field=field):
                self.factory.claude_events = claude_events()
                self.factory.claude_events[-1][field] = value
                with self.assertRaises(RuntimeError):
                    self.run_provider("claude")

    def test_claude_tool_error_result_does_not_end_the_turn(self):
        # O eroare de tool (claim refuzat, Luau eșuat) face parte din bucla agentului.
        self.factory.claude_events.insert(2, {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "mcp__studio_bridge__multi_edit", "input": {}}]}})
        self.factory.claude_events.insert(3, {"type": "user", "message": {"content": [
            {"type": "tool_result", "is_error": True, "content": "Fără claim pe ServerScriptService.Main."}]}})
        native, _ = self.run_provider("claude")
        self.assertEqual(native, SESSION)
        self.assertTrue(any("agentul continuă" in text for text in self.statuses()))
        self.assertEqual([event["tool"] for event in self.emitted if event["kind"] == "tool"], ["mcp__studio_bridge__multi_edit"])
        self.assertEqual([event["text"] for event in self.emitted if event["kind"] == "text"], ["Scena este vizibilă."])
        self.assertFalse(any(event["kind"] == "error" for event in self.emitted))

    def test_claude_failed_turn_keeps_native_id_for_resume(self):
        self.factory.claude_events[-1]["is_error"] = True
        with self.assertRaises(providers.ProviderError) as context:
            self.run_provider("claude")
        self.assertEqual(context.exception.native_id, SESSION)
        self.assertIsInstance(context.exception, RuntimeError)

    def test_claude_failure_before_init_has_no_native_id(self):
        self.factory.claude_events[0]["mcp_servers"] = []
        with self.assertRaises(providers.ProviderError) as context:
            self.run_provider("claude")
        self.assertIsNone(context.exception.native_id)

    def test_claude_resumed_session_failure_keeps_requested_id(self):
        self.factory.claude_events = claude_events()[:-1]
        with self.assertRaises(providers.ProviderError) as context:
            self.run_provider("claude", native=SESSION)
        self.assertEqual(context.exception.native_id, SESSION)

    def test_claude_missing_result_fails(self):
        self.factory.claude_events = claude_events()[:-1]
        with self.assertRaisesRegex(RuntimeError, "rezultat final"):
            self.run_provider("claude")

    def test_claude_nonzero_exit_fails_after_success_event(self):
        self.factory.run_exit = 1
        with self.assertRaises(RuntimeError):
            self.run_provider("claude")

    def test_claude_end_conversation_is_not_a_shell_tool(self):
        self.factory.claude_events.insert(1, {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "EndConversation"}]}})
        self.assertEqual(self.run_provider("claude")[0], SESSION)

    def test_reflected_job_token_is_redacted(self):
        self.factory.claude_events[1]["event"]["delta"]["text"] = TOKEN
        self.run_provider("claude")
        self.assertNotIn(TOKEN, json.dumps(self.emitted))

    def test_cancel_before_run_starts_no_cli(self):
        cancelled = threading.Event()
        cancelled.set()
        self.assertIsNone(self.run_provider("claude", cancelled=cancelled)[0])
        self.assertEqual(self.factory.children, [])

    def test_claude_cancellation_closes_owned_child(self):
        cancelled = threading.Event()
        self.factory.cancel_on_claude = cancelled
        self.run_provider("claude", cancelled=cancelled)
        self.assertTrue(self.factory.children[-1].closed)
        self.assertFalse(any(event["kind"] == "text" for event in self.emitted))

    def test_rpc_error_names_method_without_exposing_raw_message(self):
        message = providers._rpc_error_message("thread/start", {"code": -32602, "message": "private=" + TOKEN})
        self.assertIn("thread/start", message)
        self.assertIn("-32602", message)
        self.assertNotIn(TOKEN, message)
        self.assertNotIn("private=", message)

    def test_rpc_error_reports_missing_schema_field(self):
        message = providers._rpc_error_message("turn/start", {"code": -32602, "message": "missing field `text_elements`"})
        self.assertIn("text_elements", message)
        self.assertIn("turn/start", message)

    def test_codex_streaming_and_exact_resume(self):
        native, _ = self.run_provider("codex", native=THREAD)
        self.assertEqual(native, THREAD)
        child = self.factory.children[-1]
        requests = [request for request in child.requests if request.get("method") == "thread/resume"]
        self.assertEqual(requests[0]["params"]["threadId"], THREAD)
        self.assertEqual(requests[0]["params"]["sandbox"], "read-only")
        self.assertEqual([event["text"] for event in self.emitted if event["kind"] == "text"], ["Scena este vizibilă."])
        # Un singur proces per cerere, închis la final; nu mai există un client de autentificare persistent.
        self.assertEqual(len(self.factory.children), 1)
        self.assertTrue(child.closed)

    def test_codex_job_configuration_is_per_process_and_no_token_in_argv(self):
        _, directory = self.run_provider("codex")
        child = self.factory.children[-1]
        argv = " ".join(child.argv)
        for setting in ("features.shell_tool=false", "features.unified_exec=false", "features.apps=false",
                        "features.multi_agent=false", 'sandbox_mode="read-only"', 'approval_policy="never"',
                        "mcp_servers.studio_bridge.required=true", "mcp_servers.studio_bridge.tool_timeout_sec=1200"):
            self.assertIn(setting, argv)
        self.assertNotIn(TOKEN, argv)
        self.assertIn("mcp_servers.studio_bridge.env_vars=", argv)
        self.assertEqual(child.kwargs["env"]["CODEX_HOME"], "existing-codex")
        self.assertEqual(child.kwargs["env"]["STUDIO_HARNESS_JOB_TOKEN"], TOKEN)
        request = next(request for request in child.requests if request.get("method") == "turn/start")
        self.assertEqual(request["params"]["sandboxPolicy"], {"type": "readOnly"})
        self.assertNotIn("access", request["params"]["sandboxPolicy"])
        self.assertNotIn("jsonrpc", request)
        self.assertFalse(any(request.get("method", "").startswith("account/") for request in child.requests))

    def test_codex_unexpected_mcp_server_fails_before_turn(self):
        self.factory.mcp_rows.append({"name": "other", "tools": {"bad": {}}})
        with self.assertRaisesRegex(RuntimeError, "în afara bridge-ului"):
            self.run_provider("codex")
        methods = [request.get("method") for child in self.factory.children for request in child.requests]
        self.assertNotIn("turn/start", methods)

    def test_codex_builtin_execution_event_is_rejected(self):
        self.factory.codex_events.insert(0, {"method": "item/started", "params": {"item": {"type": "commandExecution", "id": "bad"}}})
        with self.assertRaisesRegex(RuntimeError, "în afara proxy-ului"):
            self.run_provider("codex")

    def test_codex_failed_turn_is_not_success_but_keeps_thread(self):
        self.factory.codex_events[-1]["params"]["turn"]["status"] = "failed"
        with self.assertRaisesRegex(providers.ProviderError, "cu succes") as context:
            self.run_provider("codex")
        self.assertEqual(context.exception.native_id, THREAD)

    def test_codex_failed_mcp_tool_call_does_not_end_the_turn(self):
        self.factory.codex_events.insert(0, {"method": "item/started", "params": {"threadId": THREAD, "turnId": TURN,
            "item": {"id": "t1", "type": "mcpToolCall", "server": "studio_bridge", "tool": "multi_edit"}}})
        self.factory.codex_events.insert(1, {"method": "item/completed", "params": {"threadId": THREAD, "turnId": TURN,
            "item": {"id": "t1", "type": "mcpToolCall", "server": "studio_bridge", "tool": "multi_edit", "status": "failed",
                     "result": {"isError": True, "content": [{"type": "text", "text": "Fără claim."}]}}}})
        native, _ = self.run_provider("codex")
        self.assertEqual(native, THREAD)
        self.assertTrue(any("agentul continuă" in text for text in self.statuses()))
        self.assertEqual([event["tool"] for event in self.emitted if event["kind"] == "tool"], ["multi_edit"])
        self.assertEqual([event["text"] for event in self.emitted if event["kind"] == "text"], ["Scena este vizibilă."])

    def test_codex_tool_call_to_other_server_fails(self):
        self.factory.codex_events.insert(0, {"method": "item/started", "params": {"threadId": THREAD, "turnId": TURN,
            "item": {"id": "t1", "type": "mcpToolCall", "server": "other", "tool": "shell"}}})
        with self.assertRaisesRegex(RuntimeError, "alt server MCP"):
            self.run_provider("codex")

    def test_codex_native_cancel_sends_interrupt(self):
        cancelled = threading.Event()
        self.factory.cancel_on_turn = cancelled
        self.run_provider("codex", cancelled=cancelled)
        child = self.factory.children[-1]
        request = next(request for request in child.requests if request.get("method") == "turn/interrupt")
        self.assertEqual(request["params"], {"threadId": THREAD, "turnId": TURN})
        self.assertTrue(child.closed)

    def test_unknown_provider_and_invalid_session_fail(self):
        with self.assertRaises(RuntimeError):
            self.run_provider("unsupported")
        with self.assertRaises(RuntimeError):
            self.run_provider("claude", native="../../other-session")
        self.assertEqual(self.factory.children, [])

    def test_codex_missing_mcp_fails_without_inference(self):
        self.factory.mcp_rows = []
        with patch.object(providers.time, "monotonic", side_effect=[0.0, 31.0]):
            with self.assertRaisesRegex(RuntimeError, "nu a conectat proxy-ul"):
                self.run_provider("codex")
        self.assertFalse(any(request.get("method") == "turn/start" for child in self.factory.children for request in child.requests))

    def test_claude_permission_denied_event_fails(self):
        self.factory.claude_events.insert(1, {"type": "system", "subtype": "permission_denied"})
        with self.assertRaisesRegex(RuntimeError, "refuzată"):
            self.run_provider("claude")

    def test_claude_wrong_resumed_session_fails(self):
        self.factory.claude_events = claude_events("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
        with self.assertRaisesRegex(RuntimeError, "reluat conversația"):
            self.run_provider("claude", native=SESSION)

    def test_codex_wrong_resumed_thread_fails(self):
        self.factory.thread_id = "another-thread"
        with self.assertRaisesRegex(RuntimeError, "reluat conversația"):
            self.run_provider("codex", native=THREAD)

    def test_invalid_bridge_url_has_no_sensitive_error(self):
        directory = self.base / "invalid-job"
        directory.mkdir()
        with self.assertRaises(RuntimeError) as context:
            self.registry.run("claude", "test", None, directory, "http://127.0.0.1:secret-value", JOB, TOKEN,
                              self.emit, threading.Event())
        self.assertNotIn("secret-value", str(context.exception))
        self.assertEqual(self.factory.children, [])

    def test_process_drains_non_utf8_stderr_without_real_subprocess(self):
        fake = Mock()
        fake.pid = 12345
        fake.stdin = io.BytesIO()
        fake.stdout = io.BytesIO(b'{"type":"result"}\n')
        fake.stderr = io.BytesIO(b'\xff' + b'x' * 131072)
        fake.poll.return_value = 0
        fake.wait.return_value = 0
        with patch.object(providers.subprocess, "Popen", return_value=fake) as popen, patch.object(providers, "WindowsProcessTree"):
            child = providers._Process(["fake-official-executable"], env={}, cwd=self.base)
            message = child.read_json(timeout=2)
            self.assertIsNone(child.read_json(timeout=2))
            child.close()
        self.assertEqual(message, {"type": "result"})
        self.assertTrue(fake.stderr.closed)
        self.assertTrue(fake.stdout.closed)
        self.assertFalse(popen.call_args.kwargs["shell"])

    def test_registry_close_closes_children_and_refuses_new_runs(self):
        child = FakeProcess(self.factory, ["held"])
        self.registry.children.add(child)
        self.registry.close()
        self.assertTrue(child.closed)
        with self.assertRaisesRegex(RuntimeError, "închis"):
            self.run_provider("claude")
        self.assertEqual(self.factory.children, [])


if __name__ == "__main__":
    unittest.main()

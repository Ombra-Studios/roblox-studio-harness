"""Hook-urile CLI (Claude Code și notify Codex) împotriva unui daemon local pe port efemer. Fără procese reale."""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import harness_hook
from studio_bridge import BridgeServer
from test_studio_bridge import make_bridge

TOKEN = "hook-local-code-0123456789"


def refused(*_, **__):
    """Simulează daemon-ul oprit; Windows întârzie refuzul real pe loopback cu circa două secunde."""
    raise URLError(ConnectionRefusedError("nimic nu ascultă"))


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        self.path = Path(self.temp.name) / "transcript.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def test_assistant_texts_reads_from_offset_and_skips_incomplete_lines(self):
        lines = [
            json.dumps({"type": "user", "message": {"content": "salut"}}),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Primul"}, {"type": "tool_use", "name": "x"}, {"type": "text", "text": "Al doilea"}]}}),
            json.dumps({"type": "assistant", "message": {"content": "Text simplu"}}),
            "nu este json",
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "   "}]}}),
            json.dumps({"type": "assistant", "message": "invalid"}),
            json.dumps(["listă"]),
        ]
        partial = '{"type": "assistant", "message": {"content": [{"type": "text", "text": "incomplet"'
        # Octeți exacți (fără traducerea liniilor noi de pe Windows): offsetul este poziție în fișier.
        complete = ("\n".join(lines) + "\n").encode("utf-8")
        self.path.write_bytes(complete + partial.encode("utf-8"))
        record = {"job_id": "j", "offset": 0}
        self.assertEqual(harness_hook.assistant_texts(str(self.path), record), ["Primul\nAl doilea", "Text simplu"])
        self.assertEqual(record["offset"], len(complete))
        # Linia incompletă rămâne pentru următorul Stop; fără linie nouă nu se avansează.
        self.assertEqual(harness_hook.assistant_texts(str(self.path), record), [])
        self.assertEqual(record["offset"], len(complete))
        with self.path.open("ab") as stream:
            stream.write(b"}]}}\n")
        self.assertEqual(harness_hook.assistant_texts(str(self.path), record), ["incomplet"])
        self.assertEqual(record["offset"], self.path.stat().st_size)

    def test_assistant_texts_handles_missing_paths_and_bad_offsets(self):
        self.assertEqual(harness_hook.assistant_texts(None, {"offset": 0}), [])
        self.assertEqual(harness_hook.assistant_texts(str(self.path / "absent"), {"offset": 0}), [])
        self.path.write_text(json.dumps({"type": "assistant", "message": {"content": "Șir cu diacritice"}}) + "\n", encoding="utf-8")
        for record in ({"offset": "3"}, {"offset": None}, {}, {"offset": 10_000}):
            with self.subTest(record=record):
                self.assertEqual(harness_hook.assistant_texts(str(self.path), record), ["Șir cu diacritice"])
                self.assertEqual(record["offset"], self.path.stat().st_size)


class HookServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        self.state = Path(self.temp.name) / "state"
        self.state.mkdir()
        (self.state / "local-token").write_text(TOKEN, encoding="utf-8")
        self.bridge = make_bridge(self.temp.name, token=TOKEN)
        self.server = BridgeServer(0, self.bridge)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.patches = [patch.object(harness_hook, "BASE", self.bridge.base_url),
                        patch.object(harness_hook, "host_process_id", lambda names: 4242),
                        patch.dict(os.environ, {"STUDIO_HARNESS_STATE_DIR": str(self.state)})]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.bridge.close()
        self.temp.cleanup()

    def jobs(self):
        return list(self.bridge.jobs.values())

    def test_register_creates_one_session_and_reuses_the_record(self):
        record = harness_hook.register(self.state, TOKEN, "claude", "dir", "sess/1", ("claude.exe",))
        job = self.jobs()[0]
        self.assertEqual(record, {"job_id": job.id, "job_token": job.token, "offset": 0})
        self.assertEqual((job.kind, job.provider, job.cwd, job.cli_session_id, job.host_pid), ("terminal", "claude", "dir", "sess/1", 4242))
        path = harness_hook.session_file(self.state, "sess/1")
        self.assertEqual(path.name, "sess_1.json")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), record)
        self.assertEqual(harness_hook.register(self.state, TOKEN, "claude", "dir", "sess/1", ("claude.exe",)), record)
        self.assertEqual(len(self.jobs()), 1)

    def test_register_fails_quietly_with_wrong_code_or_no_daemon(self):
        self.assertIsNone(harness_hook.register(self.state, "wrong-code-0123456789", "claude", "dir", "k", ()))
        with patch.object(harness_hook, "urlopen", refused):
            self.assertIsNone(harness_hook.register(self.state, TOKEN, "claude", "dir", "k", ()))
        self.assertEqual(self.jobs(), [])
        self.assertFalse(harness_hook.session_file(self.state, "k").exists())

    def test_send_event_posts_and_drops_stale_records(self):
        record = harness_hook.register(self.state, TOKEN, "codex", "dir", "k", ())
        job = self.jobs()[0]
        self.assertTrue(harness_hook.send_event(self.state, "k", record, "prompt", "  Salut  "))
        self.assertEqual((job.events[-1]["type"], job.events[-1]["text"]), ("prompt", "Salut"))
        self.assertTrue(harness_hook.send_event(self.state, "k", record, "text", "   "))
        self.assertEqual(len(job.events), 2)
        path = harness_hook.session_file(self.state, "k")
        with patch.object(harness_hook, "urlopen", refused):
            self.assertFalse(harness_hook.send_event(self.state, "k", record, "text", "fără daemon"))
        self.assertTrue(path.exists())
        self.assertFalse(harness_hook.send_event(self.state, "k", {**record, "job_token": "wrong"}, "text", "x"))
        self.assertFalse(path.exists())
        harness_hook.save_record(path, record)
        self.assertFalse(harness_hook.send_event(self.state, "k", {**record, "job_id": "absent"}, "text", "x"))
        self.assertFalse(path.exists())
        harness_hook.save_record(path, record)
        self.bridge.close_terminal(job)
        self.assertFalse(harness_hook.send_event(self.state, "k", record, "text", "după închidere"))
        self.assertFalse(path.exists())
        self.assertNotIn("după închidere", json.dumps(job.events, ensure_ascii=False))

    def test_handle_claude_prompt_stop_and_session_end(self):
        transcript = Path(self.temp.name) / "transcript.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Răspuns"}]}}) + "\n", encoding="utf-8")
        base = {"session_id": "abc", "cwd": "proiect", "transcript_path": str(transcript)}
        harness_hook.handle_claude({**base, "hook_event_name": "SessionStart"})
        job = self.jobs()[0]
        self.assertEqual((job.provider, job.cwd, job.cli_session_id), ("claude", "proiect", "abc"))
        harness_hook.handle_claude({**base, "hook_event_name": "UserPromptSubmit", "prompt": "Fă ceva"})
        self.assertEqual((job.events[-1]["type"], job.events[-1]["text"]), ("prompt", "Fă ceva"))
        harness_hook.handle_claude({**base, "hook_event_name": "Stop"})
        self.assertEqual((job.events[-1]["type"], job.events[-1]["text"]), ("text", "Răspuns"))
        record = harness_hook.load_record(harness_hook.session_file(self.state, "abc"))
        self.assertEqual(record["offset"], transcript.stat().st_size)
        harness_hook.handle_claude({**base, "hook_event_name": "Stop"})
        self.assertEqual(len([event for event in job.events if event["type"] == "text"]), 1)
        harness_hook.handle_claude({**base, "hook_event_name": "SessionEnd"})
        self.assertEqual(job.state, "completed")
        self.assertFalse(harness_hook.session_file(self.state, "abc").exists())
        self.assertEqual(len(self.jobs()), 1)
        harness_hook.handle_claude({**base, "hook_event_name": "UserPromptSubmit", "prompt": "din nou"})
        self.assertEqual(len(self.jobs()), 2)

    def test_handle_claude_ignores_missing_session_or_local_code(self):
        harness_hook.handle_claude({"hook_event_name": "UserPromptSubmit", "prompt": "x"})
        harness_hook.handle_claude({"hook_event_name": "UserPromptSubmit", "prompt": "x", "session_id": ""})
        (self.state / "local-token").unlink()
        harness_hook.handle_claude({"hook_event_name": "UserPromptSubmit", "prompt": "x", "session_id": "abc"})
        self.assertEqual(self.jobs(), [])

    def test_handle_codex_notify_payload(self):
        harness_hook.handle_codex(json.dumps({"thread-id": "t1", "cwd": "dir", "input-messages": ["prima", 5, "a doua"],
                                              "last-assistant-message": "gata"}))
        job = self.jobs()[0]
        self.assertEqual((job.provider, job.cwd, job.cli_session_id), ("codex", "dir", "t1"))
        self.assertEqual([(event["type"], event["text"]) for event in job.events[1:]],
                         [("prompt", "prima"), ("prompt", "a doua"), ("text", "gata")])
        harness_hook.handle_codex(json.dumps({"thread-id": "t1", "last-assistant-message": "încă una"}))
        self.assertEqual(len(self.jobs()), 1)
        self.assertEqual(job.events[-1]["text"], "încă una")
        harness_hook.handle_codex("{nu e json")
        harness_hook.handle_codex(json.dumps(["listă"]))
        self.assertEqual(len(self.jobs()), 1)
        # Fără thread-id, cheia este procesul gazdă; același host_pid se unește cu sesiunea deschisă.
        harness_hook.handle_codex(json.dumps({"last-assistant-message": "fără thread"}))
        self.assertEqual(len(self.jobs()), 1)
        self.assertEqual(job.events[-1]["text"], "fără thread")
        self.assertEqual(harness_hook.load_record(harness_hook.session_file(self.state, "codex-4242"))["job_id"], job.id)
        with patch.object(harness_hook, "host_process_id", lambda names: None):
            harness_hook.handle_codex(json.dumps({"last-assistant-message": "alt proces"}))
        self.assertEqual(len(self.jobs()), 2)
        self.assertEqual(self.jobs()[1].cli_session_id, "codex-" + str(os.getppid()))
        self.assertIsNone(self.jobs()[1].host_pid)

    def test_main_never_blocks_the_cli_and_writes_nothing_to_stdout(self):
        out = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO("nu este json")), patch.object(sys, "stdout", out):
            self.assertEqual(harness_hook.main(["hook"]), 0)
            self.assertEqual(harness_hook.main(["hook", "--codex", "{stricat"]), 0)
            self.assertEqual(harness_hook.main(["hook", "--codex"]), 0)
        with patch.object(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "m", "prompt": "Salut"}))), patch.object(sys, "stdout", out):
            self.assertEqual(harness_hook.main(["hook"]), 0)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(self.jobs()[0].events[-1]["text"], "Salut")
        with patch.object(harness_hook, "handle_claude", side_effect=RuntimeError("defect")), patch.object(sys, "stdin", io.StringIO("{}")):
            self.assertEqual(harness_hook.main(["hook"]), 0)


if __name__ == "__main__":
    unittest.main()

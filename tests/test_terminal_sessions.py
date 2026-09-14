"""Mod T (0.4 → 1.0): sesiuni din terminal, tablă, claims impuse în proxy, politica de aprobare, Studio-ul țintă și rutele HTTP.

Fără procese reale și fără hub (`hub_url=None`): claims-urile sunt locale, toolurile `hub_*` spun explicit că hub-ul este dezactivat."""

import json
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import studio_bridge
from providers import ProviderError
from studio_bridge import BridgeError, BridgeServer, Job
from test_studio_bridge import IDENTITY, UI_TOKEN, FakeNative, FakeProviders, make_bridge, temp_dir

EDITS = [{"old_string": "a", "new_string": "b"}, {"old_string": "c", "new_string": "d"}]
LOCAL_NOTICE = "Hub-ul nu este disponibil (stare: disabled): claims-urile sunt locale, colegii nu le văd."


class TerminalBase(unittest.TestCase):
    def setUp(self):
        self.temp = temp_dir()
        self.native = FakeNative()
        self.providers = FakeProviders()
        self.bridge = make_bridge(self.temp.name, providers=self.providers, native=self.native)

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def terminal(self, provider="claude", **extra):
        return self.bridge.create_terminal_session({"provider": provider, **extra})

    def other_job(self, developer="dan"):
        """Sesiune din terminal a altui developer, adăugată direct ca în daemon."""
        job = Job(uuid.uuid4().hex, "codex", None, uuid.uuid4().hex, "", {}, kind="terminal",
                  developer=developer, name="Codex · altcineva", state="running")
        self.bridge.jobs[job.id] = job
        return job

    def claim(self, job, *paths):
        result = self.bridge.agent_call(job, "hub_claim", {"paths": list(paths)})
        self.assertFalse(result["isError"], result)
        return result

    def text(self, result):
        return result["content"][0]["text"]

    def events(self, job, kind):
        return [event for event in job.events if event["type"] == kind]


class ReapTests(TerminalBase):
    """1.0: o sesiune din terminal al cărei CLI a murit brutal (kill, pană) nu rămâne „în lucru” în pluginul din Studio."""

    def test_a_dead_terminal_is_closed_and_its_claims_are_freed(self):
        alive = self.terminal(host_pid=4242)
        dead = self.terminal(host_pid=5353, cli_session_id="al-doilea")
        self.claim(dead, "Workspace.Map")
        # Sesiunile proaspete sunt lăsate în pace; abia după perioada de grație intră în verificare.
        self.bridge.alive_pids = lambda pids: set()
        self.assertEqual(self.bridge.reap_terminals(force=True), [])
        for job in (alive, dead):
            job.last_activity -= studio_bridge.REAP_GRACE + 1
        self.assertEqual([claim["path"] for claim in self.bridge.claims.snapshot()], ["Workspace.Map"])
        # Numai 4242 mai există: sesiunea lui 5353 se închide, claims-urile ei se eliberează, cealaltă rămâne neatinsă.
        self.bridge.alive_pids = lambda pids: {pid for pid in pids if pid == 4242}
        self.assertEqual(self.bridge.reap_terminals(force=True), [dead.id])
        self.assertEqual(dead.state, "cancelled")
        self.assertIn("Terminalul s-a închis", dead.events[-1]["text"])
        self.assertEqual(alive.state, "running")
        self.assertEqual(self.bridge.claims.snapshot(), [])
        # A doua trecere nu mai are ce închide, iar `board()` nu ridică nimic.
        self.assertEqual(self.bridge.reap_terminals(force=True), [])
        self.assertEqual(len(self.bridge.board()["sessions"]), 2)

    def test_reaping_is_throttled_and_survives_a_failing_probe(self):
        first = self.terminal(host_pid=4242)
        first.last_activity -= studio_bridge.REAP_GRACE + 1
        calls = []
        self.bridge.alive_pids = lambda pids: calls.append(set(pids)) or set()
        self.bridge.last_reap = 0.0
        self.bridge.board()
        self.assertEqual(len(calls), 1, "prima chemare verifică procesele")
        self.bridge.board()
        self.assertEqual(len(calls), 1, "a doua chemare, imediat după, este temperată")
        # O eroare la citirea proceselor nu trebuie să rupă tabla.
        second = self.terminal(host_pid=6464, cli_session_id="alta")
        second.last_activity -= studio_bridge.REAP_GRACE + 1
        def explode(_):
            raise OSError("snapshot indisponibil")
        self.bridge.alive_pids = explode
        self.assertEqual(self.bridge.reap_terminals(force=True), [])
        self.assertEqual(second.state, "running")

    def test_sessions_without_a_host_pid_are_left_alone(self):
        session = self.terminal()
        session.last_activity -= studio_bridge.REAP_GRACE + 1
        self.assertIsNone(session.host_pid)
        self.bridge.alive_pids = lambda pids: set()
        self.assertEqual(self.bridge.reap_terminals(force=True), [])
        self.assertEqual(session.state, "running")


class SessionTests(TerminalBase):
    def test_create_terminal_session_validations(self):
        invalid = [
            {}, {"provider": "other"}, {"provider": "claude", "cwd": 5}, {"provider": "claude", "cwd": "x" * 1025},
            {"provider": "claude", "host_pid": 0}, {"provider": "claude", "host_pid": -3}, {"provider": "claude", "host_pid": True},
            {"provider": "claude", "host_pid": "12"}, {"provider": "claude", "cli_session_id": ""},
            {"provider": "claude", "cli_session_id": "x" * 129}, {"provider": "claude", "name": "n" * 81},
        ]
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(BridgeError) as error:
                self.bridge.create_terminal_session(body)
            self.assertEqual(error.exception.status, 400)
        self.assertEqual(self.bridge.jobs, {})

    def test_create_terminal_session_defaults(self):
        cwd = str(Path(self.temp.name) / "game")
        job = self.terminal(cwd=cwd)
        self.assertEqual((job.kind, job.state, job.provider, job.studio_id, job.developer, job.workspace),
                         ("terminal", "running", "claude", None, "tester", None))
        self.assertEqual(job.name, "Claude Code · game")
        self.assertEqual(job.cwd, cwd)
        self.assertEqual(self.terminal("codex").name, "Codex · terminal")
        self.assertEqual(self.terminal(name="Al meu").name, "Al meu")
        self.assertEqual(job.events[0]["type"], "status")
        self.assertIn("Director: " + cwd, job.events[0]["text"])
        self.assertEqual(self.bridge.sessions[job.session_id], {"provider": "claude", "studio_id": None, "native_id": None})
        self.assertIs(self.bridge.authenticate_job(job.id, job.token), job)
        self.assertIsNone(self.bridge.active_job_id)

    def test_sessions_merge_by_host_pid_while_running(self):
        first = self.terminal(host_pid=4242)
        merged = self.terminal(host_pid=4242, cwd="dir", cli_session_id="abc")
        self.assertIs(merged, first)
        self.assertEqual((first.cwd, first.cli_session_id), ("dir", "abc"))
        self.assertIs(self.terminal(host_pid=4242, cli_session_id="other", cwd="alt"), first)
        self.assertEqual((first.cwd, first.cli_session_id), ("dir", "abc"))
        self.assertIsNot(self.terminal(host_pid=4243), first)
        self.assertIsNot(self.terminal(), self.terminal())
        self.bridge.close_terminal(first)
        self.assertIsNot(self.terminal(host_pid=4242), first)

    def test_terminal_session_limit(self):
        with patch.object(studio_bridge, "MAX_TERMINAL_SESSIONS", 2):
            first = self.terminal()
            self.terminal()
            with self.assertRaises(BridgeError) as error:
                self.terminal()
            self.assertEqual(error.exception.status, 429)
            self.bridge.close_terminal(first)
            self.assertEqual(self.terminal().kind, "terminal")
        self.assertEqual(studio_bridge.MAX_TERMINAL_SESSIONS, 64)

    def test_terminal_event_types_and_state(self):
        job = self.terminal()
        before = job.last_activity
        for kind in ("prompt", "text", "status"):
            self.bridge.terminal_event(job, {"type": kind, "text": "mesaj " + kind})
        self.assertEqual([(event["type"], event["text"]) for event in job.events[1:]],
                         [("prompt", "mesaj prompt"), ("text", "mesaj text"), ("status", "mesaj status")])
        self.assertGreaterEqual(job.last_activity, before)
        for body in ({"type": "tool", "text": "x"}, {"type": "text"}, {"type": "text", "text": "   "}, {"type": "text", "text": 3}, {"text": "x"}):
            with self.subTest(body=body), self.assertRaises(BridgeError) as error:
                self.bridge.terminal_event(job, body)
            self.assertEqual(error.exception.status, 400)
        with self.assertRaises(BridgeError) as error:
            self.bridge.terminal_event(job, {"type": "text", "text": "x" * (4 * 64000 + 1)})
        self.assertEqual(error.exception.status, 413)
        self.bridge.terminal_event(job, {"type": "text", "text": "ș" * 70000})
        self.assertEqual("".join(event["text"] for event in job.events[-2:]), "ș" * 70000)
        self.bridge.close_terminal(job)
        with self.assertRaises(BridgeError) as error:
            self.bridge.terminal_event(job, {"type": "text", "text": "după închidere"})
        self.assertEqual(error.exception.status, 409)

    def test_close_terminal_releases_claims_and_emits_claim_before_done(self):
        job = self.terminal()
        self.claim(job, "Workspace.Map.Zone3", "Lighting")
        self.bridge.close_terminal(job)
        self.assertEqual(job.state, "completed")
        self.assertIsNotNone(job.closed_at)
        self.assertEqual(self.bridge.claims.held_by(job.id), [])
        kinds = [event["type"] for event in job.events]
        released = next(event for event in job.events if event["type"] == "claim" and event["action"] == "released")
        self.assertEqual(sorted(released["paths"]), ["Lighting", "Workspace.Map.Zone3"])
        self.assertLess(kinds.index("claim", kinds.index("claim") + 1), kinds.index("done"))
        self.assertEqual(kinds[-1], "done")
        self.assertEqual(job.events[-1]["state"], "completed")
        count = len(job.events)
        self.bridge.close_terminal(job)
        self.assertEqual(len(job.events), count)
        with self.assertRaises(BridgeError):
            self.bridge.authenticate_job(job.id, job.token)

    def test_board_lists_sessions_claims_and_journal(self):
        terminal = self.terminal(cwd="dir", host_pid=7)
        queued = Job(uuid.uuid4().hex, "codex", "studio-1", "s-q", "p", {}, developer="tester", name="Sesiune Studio", state="queued")
        finished = Job(uuid.uuid4().hex, "claude", "studio-1", "s-f", "p", {}, developer="tester", state="completed")
        recent = Job(uuid.uuid4().hex, "codex", None, "s-r", "", {}, kind="terminal", developer="tester", state="completed", closed_at=time.time() - 30)
        stale = Job(uuid.uuid4().hex, "codex", None, "s-s", "", {}, kind="terminal", developer="tester", state="completed", closed_at=time.time() - 601)
        for job in (queued, finished, recent, stale):
            self.bridge.jobs[job.id] = job
        self.claim(terminal, "Workspace.Map.Zone3")
        self.bridge._record(terminal, "multi_edit", ["ServerScriptService.Main"], "multi_edit · ServerScriptService.Main")
        board = self.bridge.board()
        self.assertTrue(board["ok"])
        self.assertEqual(board["bridge_id"], self.bridge.bridge_id)
        self.assertEqual(board["studios"], [{"id": "studio-1", "name": "Scenă de test"}])
        self.assertTrue(board["connected"])
        self.assertIsNone(board["default_studio_id"])
        self.assertEqual(board["developer"], "tester")
        # 1.0: tabla poartă identitatea, workspace-ul, hub-ul și sumarul workspace-urilor; fără hub sunt goale/dezactivate.
        self.assertEqual((board["identity"], board["workspace"], board["hub"]["status"], board["members"], board["workspaces"]),
                         (None, None, "disabled", [], []))
        rows = {row["job_id"]: row for row in board["sessions"]}
        self.assertEqual(set(rows), {terminal.id, queued.id, recent.id})
        row = rows[terminal.id]
        self.assertEqual({key: row[key] for key in ("kind", "provider", "developer", "name", "state", "studio_id", "cwd", "claims", "pending_approval",
                                                    "workspace", "device_id", "roblox_user_id", "machine", "remote", "mine")},
                         {"kind": "terminal", "provider": "claude", "developer": "tester", "name": "Claude Code · dir", "state": "running",
                          "studio_id": None, "cwd": "dir", "claims": ["Workspace.Map.Zone3"], "pending_approval": False, "workspace": None,
                          "device_id": self.bridge.status()["hub"]["device_id"], "roblox_user_id": 0, "machine": self.bridge.machine,
                          "remote": False, "mine": True})
        self.assertIsInstance(row["started"], float)
        self.assertIsInstance(row["last_activity"], float)
        self.assertEqual(rows[queued.id]["state"], "queued")
        self.assertEqual(rows[recent.id]["state"], "completed")
        self.assertEqual([(claim["path"], claim["mine"], claim["workspace"]) for claim in board["claims"]], [("Workspace.Map.Zone3", True, None)])
        self.assertEqual(board["claims"][0]["job_id"], terminal.id)
        self.assertEqual(len(board["journal"]), 1)
        entry = board["journal"][0]
        self.assertEqual({key: entry[key] for key in ("seq", "job_id", "developer", "provider", "tool", "paths", "summary", "workspace")},
                         {"seq": 1, "job_id": terminal.id, "developer": "tester", "provider": "claude", "tool": "multi_edit",
                          "paths": ["ServerScriptService.Main"], "summary": "multi_edit · ServerScriptService.Main", "workspace": None})
        self.assertIsInstance(entry["time"], float)
        self.assertNotIn(terminal.token, json.dumps(board))
        # După identitate, tabla arată workspace-ul, iar claims-urile și jurnalul nou poartă cheia.
        self.bridge.set_identity(IDENTITY)
        self.bridge._record(terminal, "multi_edit", ["Lighting"], "multi_edit · Lighting")
        board = self.bridge.board()
        self.assertEqual((board["workspace"]["key"], board["identity"]["name"], board["developer"]), ("game:987654", "ellob", "ellob"))
        self.assertEqual((board["claims"][0]["workspace"], board["journal"][-1]["workspace"]), ("game:987654", "game:987654"))

    def test_board_limits_journal_and_uses_cached_studios_when_native_is_busy(self):
        job = self.terminal()
        for index in range(studio_bridge.BOARD_JOURNAL + 5):
            self.bridge._record(job, "multi_edit", ["X"], str(index))
        board = self.bridge.board()
        self.assertEqual(len(board["journal"]), studio_bridge.BOARD_JOURNAL)
        self.assertEqual(board["journal"][-1]["summary"], str(studio_bridge.BOARD_JOURNAL + 4))
        self.assertEqual(len(self.bridge.journal), studio_bridge.BOARD_JOURNAL + 5)
        self.assertEqual(self.bridge.journal.maxlen, 500)
        # Un apel nativ lung nu blochează tabla: se folosește lista din cache.
        self.native.try_list_studios = lambda timeout: None
        board = self.bridge.board()
        self.assertEqual(board["studios"], [{"id": "studio-1", "name": "Scenă de test"}])
        self.native.try_list_studios = lambda timeout: []
        self.assertFalse(self.bridge.board()["connected"])

    def test_status_reports_plugin_connection(self):
        self.assertFalse(self.bridge.status()["plugin_connected"])
        self.bridge.touch_ui()
        self.assertTrue(self.bridge.status()["plugin_connected"])
        with patch.object(studio_bridge, "PLUGIN_TIMEOUT", 0):
            self.assertFalse(self.bridge.plugin_connected())
        self.assertEqual(studio_bridge.PLUGIN_TIMEOUT, 10)

    def test_set_default_studio(self):
        for body in ({}, {"studio_id": ""}, {"studio_id": 3}):
            with self.subTest(body=body), self.assertRaises(BridgeError) as error:
                self.bridge.set_default_studio(body)
            self.assertEqual(error.exception.status, 400)
        with self.assertRaises(BridgeError) as error:
            self.bridge.set_default_studio({"studio_id": "studio-2"})
        self.assertEqual(error.exception.status, 409)
        self.assertIsNone(self.bridge.default_studio_id)
        self.bridge.set_default_studio({"studio_id": "studio-1"})
        self.assertEqual(self.bridge.status()["default_studio_id"], "studio-1")
        self.assertEqual(self.bridge.board()["default_studio_id"], "studio-1")

    def test_terminal_job_resolves_studio_on_every_call(self):
        job = self.terminal()
        self.bridge.agent_call(job, "inspect_instance", {"path": "Workspace"})
        self.assertEqual(self.native.calls[-1][2], "studio-1")
        self.assertEqual(self.native.calls[-1][1]["studio_id"], "studio-1")
        self.native.studios.append({"id": "studio-2", "name": "A doua"})
        with self.assertRaisesRegex(BridgeError, "Mai multe instanțe Studio deschise; alege Studio-ul țintă în Avansat.") as error:
            self.bridge.agent_call(job, "inspect_instance", {"path": "Workspace"})
        self.assertEqual(error.exception.status, 409)
        # 1.0: instanța al cărei nume coincide cu jocul deschis (place_name din identitate) este aleasă automat.
        self.bridge.set_identity({**IDENTITY, "place_name": "A doua"})
        self.bridge.agent_call(job, "inspect_instance", {"path": "Workspace"})
        self.assertEqual(self.native.calls[-1][2], "studio-2")
        self.bridge.set_identity({**IDENTITY, "place_name": "Alt joc"})
        with self.assertRaisesRegex(BridgeError, "Mai multe instanțe"):
            self.bridge.agent_call(job, "inspect_instance", {"path": "Workspace"})
        # Suprascrierea manuală („Avansat”) câștigă în fața numelui.
        self.bridge.set_identity({**IDENTITY, "place_name": "A doua"})
        self.bridge.set_default_studio({"studio_id": "studio-1"})
        self.bridge.agent_call(job, "inspect_instance", {"path": "Workspace"})
        self.assertEqual(self.native.calls[-1][2], "studio-1")
        self.bridge.set_default_studio({"studio_id": "studio-2"})
        self.bridge.agent_call(job, "inspect_instance", {"path": "Workspace"})
        self.assertEqual(self.native.calls[-1][2], "studio-2")
        # Agentul nu poate alege instanța prin argumente.
        with self.assertRaises(BridgeError):
            self.bridge.agent_call(job, "inspect_instance", {"path": "Workspace", "studio_id": "studio-1"})
        # Instanța implicită deconectată: rămâne singura conectată.
        self.native.studios.pop()
        self.bridge.agent_call(job, "inspect_instance", {"path": "Workspace"})
        self.assertEqual(self.native.calls[-1][2], "studio-1")
        self.native.studios.clear()
        with self.assertRaisesRegex(BridgeError, "Nicio instanță Studio conectată; activează MCP-ul în Studio.") as error:
            self.bridge.agent_call(job, "inspect_instance", {"path": "Workspace"})
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(len(self.native.calls), 5)
        self.assertIsNone(job.studio_id)


class EnforcementTests(TerminalBase):
    def test_multi_edit_without_claim_is_refused_and_names_the_path(self):
        job = self.terminal()
        result = self.bridge.agent_call(job, "multi_edit", {"file_path": "ServerScriptService.Main", "edits": EDITS})
        self.assertTrue(result["isError"])
        self.assertIn("ServerScriptService.Main", self.text(result))
        self.assertIn("hub_claim", self.text(result))
        self.assertEqual(self.native.calls, [])
        self.assertIsNone(job.pending)
        denied = self.events(job, "claim")[-1]
        self.assertEqual((denied["action"], denied["paths"], denied["holder"]), ("denied", ["ServerScriptService.Main"], None))
        self.assertEqual(list(self.bridge.journal), [])

    def test_claim_held_by_other_job_names_the_holder(self):
        job, other = self.terminal(), self.other_job("dan")
        self.claim(other, "ServerScriptService")
        result = self.bridge.agent_call(job, "multi_edit", {"file_path": "ServerScriptService.Main", "edits": EDITS})
        self.assertTrue(result["isError"])
        self.assertIn("dan", self.text(result))
        self.assertIn("ServerScriptService", self.text(result))
        self.assertEqual(self.events(job, "claim")[-1]["holder"], "dan")
        self.assertEqual(self.native.calls, [])
        conflict = self.bridge.agent_call(job, "hub_claim", {"paths": ["ServerScriptService.Main"], "reason": "test"})
        self.assertTrue(conflict["isError"])
        payload = json.loads(self.text(conflict))
        self.assertFalse(payload["ok"])
        self.assertEqual((payload["conflicts"][0]["holder"], payload["conflicts"][0]["developer"], payload["conflicts"][0]["held_path"]),
                         (other.id, "dan", "ServerScriptService"))
        self.assertEqual(self.events(job, "claim")[-1]["action"], "denied")
        self.assertEqual(self.bridge.claims.held_by(job.id), [])

    def test_execute_luau_requires_scope_inside_claims(self):
        job = self.terminal()
        self.claim(job, "Workspace.Map.Zone3")
        for arguments in ({"code": "return 1"}, {"code": "return 1", "scope": ""}, {"code": "return 1", "scope": []},
                          {"code": "return 1", "scope": [1]}, {"code": "return 1", "scope": {"a": 1}}):
            with self.subTest(arguments=arguments):
                result = self.bridge.agent_call(job, "execute_luau", arguments)
                self.assertTrue(result["isError"])
                self.assertIn("scope", self.text(result))
        outside = self.bridge.agent_call(job, "execute_luau", {"code": "return 1", "scope": ["Workspace.Map.Zone3", "Lighting"]})
        self.assertTrue(outside["isError"])
        self.assertIn("Lighting", self.text(outside))
        self.assertEqual(self.native.calls, [])
        ok = self.bridge.agent_call(job, "execute_luau", {"code": "return 1", "scope": "workspace.Map.Zone3.Part"})
        self.assertFalse(ok["isError"])
        self.assertEqual(self.native.calls[-1][0], "execute_luau")
        self.assertNotIn("scope", self.native.calls[-1][1])
        self.assertEqual(self.native.calls[-1][1]["code"], "return 1")
        self.assertIsNone(job.pending)

    def test_luau_literal_outside_claim_is_denied(self):
        job = self.terminal()
        self.claim(job, "Workspace.Map.Zone3")
        result = self.bridge.agent_call(job, "execute_luau", {"code": "local zone = workspace.Map.Zone4\nzone:Destroy()", "scope": ["Workspace.Map.Zone3"]})
        self.assertTrue(result["isError"])
        self.assertIn("Workspace.Map.Zone4", self.text(result))
        denied = self.events(job, "claim")[-1]
        self.assertEqual((denied["action"], denied["paths"]), ("denied", ["Workspace.Map.Zone4"]))
        self.assertEqual(self.native.calls, [])
        service = self.bridge.agent_call(job, "execute_luau", {"code": 'game:GetService("Lighting").ClockTime = 12', "scope": ["Workspace.Map.Zone3"]})
        self.assertTrue(service["isError"])
        self.assertIn("Lighting.ClockTime", self.text(service))
        # Strămoșii claim-ului și serviciile neverificate sunt permise.
        allowed = self.bridge.agent_call(job, "execute_luau", {
            "code": 'local map = workspace.Map\nlocal run = game:GetService("RunService")\nreturn map.Zone3', "scope": ["Workspace.Map.Zone3"]})
        self.assertFalse(allowed["isError"])
        self.assertEqual(len(self.native.calls), 1)
        self.claim(job, "Lighting")
        self.assertFalse(self.bridge.agent_call(job, "execute_luau", {"code": 'game:GetService("Lighting").ClockTime = 12', "scope": ["Lighting"]})["isError"])

    def test_play_tools_require_play_claim_per_instance(self):
        job = self.terminal()
        result = self.bridge.agent_call(job, "start_stop_play", {"action": "start"})
        self.assertTrue(result["isError"])
        self.assertIn("@play:studio-1", self.text(result))
        claimed = self.claim(job, "@play")
        # Fără hub, claim-ul este local și rezultatul spune explicit de ce.
        self.assertEqual(json.loads(self.text(claimed)), {"ok": True, "claimed": ["@play:studio-1"], "scope": "local", "notice": LOCAL_NOTICE})
        self.assertEqual(self.events(job, "claim")[-1], {"seq": self.events(job, "claim")[-1]["seq"], "type": "claim", "action": "claimed",
                                                          "paths": ["@play:studio-1"], "text": "Claims acordate: @play:studio-1"})
        self.assertFalse(self.bridge.agent_call(job, "start_stop_play", {"action": "start"})["isError"])
        self.assertEqual(self.native.calls[-1][0], "start_stop_play")
        self.assertIsNone(job.pending)
        released = self.bridge.agent_call(job, "hub_release", {"paths": ["@play"]})
        self.assertEqual(json.loads(self.text(released)), {"ok": True, "released": ["@play:studio-1"]})
        self.assertTrue(self.bridge.agent_call(job, "start_stop_play", {"action": "stop"})["isError"])

    def test_other_mutations_use_path_arguments_or_all(self):
        job = self.terminal()
        result = self.bridge.agent_call(job, "insert_asset", {"asset_id": 1, "parent_path": "Workspace.Generated"})
        self.assertIn("Workspace.Generated", self.text(result))
        result = self.bridge.agent_call(job, "insert_asset", {"asset_id": 1})
        self.assertTrue(result["isError"])
        self.assertIn("@all", self.text(result))
        self.claim(job, "Workspace.Generated")
        self.assertFalse(self.bridge.agent_call(job, "insert_asset", {"asset_id": 1, "parent_path": "Workspace.Generated.Tree"})["isError"])
        self.assertTrue(self.bridge.agent_call(job, "insert_asset", {"asset_id": 1})["isError"])
        self.claim(job, "@all")
        self.assertFalse(self.bridge.agent_call(job, "insert_asset", {"asset_id": 1})["isError"])
        self.assertEqual(len(self.native.calls), 2)

    def test_terminal_mutation_with_claim_runs_without_approval_and_is_journaled(self):
        job = self.terminal()
        self.claim(job, "ServerScriptService.Main")
        result = self.bridge.agent_call(job, "multi_edit", {"file_path": "ServerScriptService.Main", "edits": EDITS})
        self.assertFalse(result["isError"])
        self.assertIsNone(job.pending)
        self.assertFalse(any(event["type"] == "approval" for event in job.events))
        self.assertEqual(self.native.calls[-1][0], "multi_edit")
        self.assertEqual(self.native.calls[-1][1]["studio_id"], "studio-1")
        self.assertEqual([event["type"] for event in job.events[-2:]], ["tool", "status"])
        entry = list(self.bridge.journal)[-1]
        self.assertEqual({key: entry[key] for key in ("seq", "job_id", "developer", "provider", "tool", "paths", "summary")},
                         {"seq": 1, "job_id": job.id, "developer": "tester", "provider": "claude", "tool": "multi_edit",
                          "paths": ["ServerScriptService.Main"], "summary": "multi_edit · ServerScriptService.Main (2 editări)"})
        self.bridge.agent_call(job, "inspect_instance", {"path": "Workspace"})
        self.bridge.agent_call(job, "hub_board", {})
        self.assertEqual(len(self.bridge.journal), 1)
        # O eroare raportată de instrument nu intră în jurnal.
        self.native.call = lambda name, arguments, studio_id: {"content": [], "isError": True}
        self.assertTrue(self.bridge.agent_call(job, "multi_edit", {"file_path": "ServerScriptService.Main", "edits": EDITS})["isError"])
        self.assertEqual(len(self.bridge.journal), 1)
        self.assertEqual(job.events[-1]["text"], "Instrumentul a raportat o eroare.")

    def test_generation_requires_connected_plugin_and_approval(self):
        job = self.terminal()
        self.claim(job, "Workspace.Generated")
        arguments = {"parent_path": "Workspace.Generated", "prompt": "un copac"}
        result = self.bridge.agent_call(job, "generate_mesh", arguments)
        self.assertTrue(result["isError"])
        self.assertIn("pluginul Studio Harness nu este conectat", self.text(result))
        self.assertIsNone(job.pending)
        self.assertEqual(self.native.calls, [])
        self.bridge.touch_ui()
        results = []
        thread = threading.Thread(target=lambda: results.append(self.bridge.agent_call(job, "generate_mesh", arguments)))
        thread.start()
        with job.condition:
            self.assertTrue(job.condition.wait_for(lambda: job.pending is not None, timeout=2))
            approval_id = job.pending["id"]
        self.assertEqual(job.state, "waiting_approval")
        approval = self.events(job, "approval")[-1]
        self.assertEqual((approval["tool"], approval["approval_id"]), ("generate_mesh", approval_id))
        self.assertIn("credite", approval["text"])
        self.assertTrue(self.bridge.board()["sessions"][0]["pending_approval"])
        job.approve(approval_id, True)
        thread.join(2)
        self.assertFalse(results[0]["isError"])
        self.assertEqual(self.native.calls[-1][0], "generate_mesh")
        self.assertEqual(list(self.bridge.journal)[-1]["paths"], ["Workspace.Generated"])
        results.clear()
        thread = threading.Thread(target=lambda: results.append(self.bridge.agent_call(job, "generate_mesh", arguments)))
        thread.start()
        with job.condition:
            self.assertTrue(job.condition.wait_for(lambda: job.pending is not None, timeout=2))
            job.approve(job.pending["id"], False)
        thread.join(2)
        self.assertTrue(results[0]["isError"])
        self.assertIn("nu a aprobat", self.text(results[0]))
        self.assertEqual(len(self.native.calls), 1)
        self.assertEqual(job.state, "running")

    def test_studio_job_still_needs_approval_even_with_claim(self):
        job = Job(uuid.uuid4().hex, "claude", "studio-1", "s-1", "p", {}, developer="tester")
        self.bridge.jobs[job.id] = job
        self.bridge.active_job_id = job.id
        self.bridge.claims.claim(job.id, "tester", ["ServerScriptService.Main"])
        self.bridge.touch_ui()
        results = []
        thread = threading.Thread(target=lambda: results.append(self.bridge.agent_call(job, "multi_edit", {"file_path": "ServerScriptService.Main", "edits": EDITS})))
        thread.start()
        with job.condition:
            self.assertTrue(job.condition.wait_for(lambda: job.pending is not None, timeout=2))
            self.assertEqual(self.native.calls, [])
            job.approve(job.pending["id"], True)
        thread.join(2)
        self.assertFalse(results[0]["isError"])
        self.assertEqual(len(self.native.calls), 1)

    def test_hub_board_release_and_wait_via_agent_call(self):
        job, other = self.terminal(), self.other_job("dan")
        self.claim(job, "Workspace.Map.Zone3", "Lighting")
        self.claim(other, "ServerStorage")
        board = json.loads(self.text(self.bridge.agent_call(job, "hub_board", {})))
        self.assertEqual(board["developer"], "tester")
        self.assertEqual({row["job_id"] for row in board["sessions"]}, {job.id, other.id})
        self.assertEqual({claim["path"] for claim in board["claims"]}, {"Workspace.Map.Zone3", "Lighting", "ServerStorage"})
        self.assertEqual(board["journal"], [])
        # 1.0: agentul află starea hub-ului (aici dezactivat) și workspace-ul jobului (necunoscut până la identitate).
        self.assertEqual((board["hub"], board["workspace"], board["members"]), ({"status": "disabled", "url": None, "notice": LOCAL_NOTICE}, None, []))
        for arguments in ({}, {"paths": []}, {"paths": "Workspace"}, {"paths": [1]}, {"paths": ["W"] * 33}, {"paths": ["Workspace"], "reason": 3}):
            with self.subTest(arguments=arguments):
                self.assertTrue(self.bridge.agent_call(job, "hub_claim", arguments)["isError"])
        self.assertTrue(self.bridge.agent_call(job, "hub_claim", {"paths": ["@bogus"]})["isError"])
        self.assertTrue(self.bridge.agent_call(job, "hub_release", {"paths": "Lighting"})["isError"])
        partial = self.bridge.agent_call(job, "hub_release", {"paths": ["Lighting", "Absent"]})
        self.assertEqual(json.loads(self.text(partial)), {"ok": True, "released": ["Lighting"]})
        self.assertEqual(self.events(job, "claim")[-1]["paths"], ["Lighting"])
        everything = self.bridge.agent_call(job, "hub_release", {})
        self.assertEqual(json.loads(self.text(everything)), {"ok": True, "released": ["Workspace.Map.Zone3"]})
        self.assertEqual(json.loads(self.text(self.bridge.agent_call(job, "hub_release", {}))), {"ok": True, "released": []})
        self.assertEqual([display for display in (claim["path"] for claim in self.bridge.claims.snapshot())], ["ServerStorage"])
        # hub_wait nu revendică: verifică doar disponibilitatea.
        free = self.bridge.agent_call(job, "hub_wait", {"path": "Workspace.Map"})
        self.assertFalse(free["isError"])
        self.assertIn("liberă", self.text(free))
        held = self.bridge.agent_call(job, "hub_wait", {"path": "game.ServerStorage.Models", "timeout_seconds": 0})
        self.assertTrue(held["isError"])
        self.assertIn("ținută", self.text(held))
        for arguments in ({}, {"path": 3}, {"path": "Workspace", "timeout_seconds": "5"}, {"path": "Workspace", "timeout_seconds": True}, {"path": "@bogus"}):
            with self.subTest(arguments=arguments):
                self.assertTrue(self.bridge.agent_call(job, "hub_wait", arguments)["isError"])
        results = []
        thread = threading.Thread(target=lambda: results.append(self.bridge.agent_call(job, "hub_wait", {"path": "ServerStorage", "timeout_seconds": 5})))
        thread.start()
        time.sleep(0.05)
        self.assertEqual(results, [])
        self.bridge.agent_call(other, "hub_release", {})
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(results[0]["isError"])
        self.assertEqual(self.bridge.claims.held_by(job.id), [])

    def test_cancel_from_studio_releases_claims_and_blocks_the_agent(self):
        job = self.terminal()
        self.claim(job, "Workspace.Map.Zone3")
        self.bridge.cancel_job(job.id)
        self.assertEqual(job.state, "cancelled")
        self.assertEqual(self.bridge.claims.held_by(job.id), [])
        kinds = [event["type"] for event in job.events]
        self.assertLess(kinds.index("claim", 1), kinds.index("done"))
        with self.assertRaises(BridgeError) as error:
            self.bridge.agent_call(job, "hub_board", {})
        self.assertEqual(error.exception.status, 409)
        with self.assertRaises(BridgeError):
            self.bridge.authenticate_job(job.id, job.token)
        self.assertEqual(self.native.calls, [])

    def test_failed_provider_turn_keeps_partial_native_id(self):
        def fail(**arguments):
            raise ProviderError("Claude Code nu a finalizat cererea.", "native-partial")
        self.providers.run = fail
        job = self.bridge.start_chat({"provider": "claude", "prompt": "Salut", "studio_id": "studio-1"})
        with job.condition:
            self.assertTrue(job.condition.wait_for(lambda: job.state == "failed", timeout=3))
        self.assertEqual(self.bridge.sessions[job.session_id]["native_id"], "native-partial")
        self.assertIn("nu a finalizat", next(event["text"] for event in job.events if event["type"] == "error"))
        resumed = self.bridge.start_chat({"provider": "claude", "prompt": "Din nou", "studio_id": "studio-1", "session_id": job.session_id})
        self.assertEqual(resumed.session_id, job.session_id)

    def test_failed_provider_turn_without_native_id_leaves_session_new(self):
        def fail(**arguments):
            raise RuntimeError("Executabilul lipsește.")
        self.providers.run = fail
        job = self.bridge.start_chat({"provider": "claude", "prompt": "Salut", "studio_id": "studio-1"})
        with job.condition:
            self.assertTrue(job.condition.wait_for(lambda: job.state == "failed", timeout=3))
        self.assertIsNone(self.bridge.sessions[job.session_id]["native_id"])


class TerminalHttpTests(TerminalBase):
    def setUp(self):
        super().setUp()
        self.server = BridgeServer(0, self.bridge)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.base = self.bridge.base_url

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        super().tearDown()

    def request(self, path, body=None, headers=None, token=UI_TOKEN):
        all_headers = {"Content-Type": "application/json"}
        if token is not None:
            all_headers["X-Studio-Harness-Token"] = token
        all_headers.update(headers or {})
        request = Request(self.base + path, data=None if body is None else json.dumps(body).encode(), headers=all_headers)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def job_headers(self, token):
        return {"X-Studio-Harness-Job": token}

    def test_board_and_default_studio_routes_need_the_local_code(self):
        job = self.terminal()
        status, body = self.request("/v1/board")
        self.assertEqual(status, 200)
        # 1.0: tabla are identitate, workspace, hub, membri și workspace-uri în loc de `team`; fără hub sunt goale.
        self.assertEqual(set(body), {"ok", "bridge_id", "studios", "connected", "default_studio_id", "developer", "identity", "workspace", "hub",
                                     "members", "workspaces", "sessions", "claims", "journal"})
        self.assertEqual((body["members"], body["workspaces"], body["hub"]["status"]), ([], [], "disabled"))
        self.assertNotIn("team", body)
        self.assertEqual(body["sessions"][0]["job_id"], job.id)
        self.assertEqual((body["sessions"][0]["remote"], body["sessions"][0]["mine"]), (False, True))
        self.assertEqual(self.request("/v1/board", token=None)[0], 401)
        self.assertEqual(self.request("/v1/board", headers=self.job_headers(job.token), token=None)[0], 401)
        self.assertEqual(self.request("/v1/default-studio", {"studio_id": "studio-2"})[0], 409)
        self.assertEqual(self.request("/v1/default-studio", {})[0], 400)
        self.assertEqual(self.request("/v1/default-studio", {"studio_id": "studio-1"}, token="wrong")[0], 401)
        status, body = self.request("/v1/default-studio", {"studio_id": "studio-1"})
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(self.request("/v1/status")[1]["default_studio_id"], "studio-1")
        self.assertTrue(self.request("/v1/status")[1]["plugin_connected"])

    def test_terminal_session_routes(self):
        self.assertEqual(self.request("/v1/terminal/sessions", {"provider": "claude"}, token="wrong")[0], 401)
        self.assertEqual(self.request("/v1/terminal/sessions", {"provider": "claude", "host_pid": 0})[0], 400)
        status, body = self.request("/v1/terminal/sessions", {"provider": "codex", "cwd": "dir", "host_pid": 77, "cli_session_id": "thread-1"})
        self.assertEqual(status, 200)
        job = self.bridge.get_job(body["job_id"])
        self.assertEqual(body, {"ok": True, "job_id": job.id, "job_token": job.token, "bridge_id": self.bridge.bridge_id,
                                "studio_id": None, "developer": "tester", "workspace": None})
        self.assertEqual((job.kind, job.provider, job.host_pid, job.cli_session_id), ("terminal", "codex", 77, "thread-1"))
        self.assertEqual(self.request("/v1/terminal/sessions", {"provider": "codex", "host_pid": 77})[1]["job_id"], job.id)
        # Pluginul nu apelează ruta: nu marchează conexiunea UI.
        self.assertFalse(self.request("/v1/status")[1]["plugin_connected"])
        events = f"/v1/terminal/sessions/{job.id}/events"
        self.assertEqual(self.request(events, {"type": "prompt", "text": "Salut"}, self.job_headers(job.token), token=None), (200, {"ok": True}))
        self.assertEqual(job.events[-1]["type"], "prompt")
        self.assertEqual(job.events[-1]["text"], "Salut")
        self.assertEqual(self.request(events, {"type": "text", "text": "x"}, self.job_headers("wrong"), token=None)[0], 401)
        self.assertEqual(self.request(events, {"type": "text", "text": "x"}, self.job_headers(UI_TOKEN), token=None)[0], 401)
        self.assertEqual(self.request(events, {"type": "text", "text": "x"})[0], 401)
        self.assertEqual(self.request(events, {"type": "tool", "text": "x"}, self.job_headers(job.token), token=None)[0], 400)
        self.assertEqual(self.request(events, {"type": "text", "text": "x" * (4 * 64000 + 1)}, self.job_headers(job.token), token=None)[0], 413)
        self.assertEqual(self.request("/v1/terminal/sessions/absent/events", {"type": "text", "text": "x"}, self.job_headers(job.token), token=None)[0], 404)
        self.assertEqual(self.request(f"/v1/terminal/sessions/{job.id}/other", {}, self.job_headers(job.token), token=None)[0], 404)
        studio = Job(uuid.uuid4().hex, "claude", "studio-1", "s-1", "p", {})
        self.bridge.jobs[studio.id] = studio
        self.assertEqual(self.request(f"/v1/terminal/sessions/{studio.id}/events", {"type": "text", "text": "x"}, self.job_headers(studio.token), token=None)[0], 409)
        close = f"/v1/terminal/sessions/{job.id}/close"
        self.assertEqual(self.request(close, {}, self.job_headers("wrong"), token=None)[0], 401)
        self.assertEqual(self.request(close, [], self.job_headers(job.token), token=None), (200, {"ok": True}))
        self.assertEqual(job.state, "completed")
        self.assertEqual(self.request(events, {"type": "text", "text": "x"}, self.job_headers(job.token), token=None)[0], 409)
        count = len(job.events)
        self.assertEqual(self.request(close, {}, self.job_headers(job.token), token=None)[0], 200)
        self.assertEqual(len(job.events), count)

    def test_release_and_cancel_routes(self):
        job = self.terminal()
        self.claim(job, "Workspace.Map.Zone3", "Lighting", "@play")
        self.assertEqual(self.request(f"/v1/jobs/{job.id}/release", {"paths": "Lighting"})[0], 400)
        self.assertEqual(self.request(f"/v1/jobs/{job.id}/release", {"paths": ["@bogus"]})[0], 400)
        self.assertEqual(self.request("/v1/jobs/absent/release", {})[0], 404)
        self.assertEqual(self.request(f"/v1/jobs/{job.id}/release", {}, self.job_headers(job.token), token=None)[0], 401)
        status, body = self.request(f"/v1/jobs/{job.id}/release", {"paths": ["Lighting", "@play:studio-1"]})
        self.assertEqual((status, body["ok"], sorted(body["released"])), (200, True, ["@play:studio-1", "Lighting"]))
        self.assertEqual(self.events(job, "claim")[-1]["action"], "released")
        status, body = self.request(f"/v1/jobs/{job.id}/release", [])
        self.assertEqual((status, body["released"]), (200, ["Workspace.Map.Zone3"]))
        self.assertEqual(self.bridge.claims.snapshot(), [])
        self.claim(job, "Lighting")
        self.assertEqual(self.request(f"/v1/jobs/{job.id}/cancel", {})[0], 200)
        self.assertEqual(job.state, "cancelled")
        self.assertEqual(self.bridge.claims.snapshot(), [])
        self.assertEqual(self.request(f"/agent/{job.id}/tools", headers=self.job_headers(job.token), token=None)[0], 409)
        rows = self.request("/v1/board")[1]["sessions"]
        self.assertEqual([row["state"] for row in rows if row["job_id"] == job.id], ["cancelled"])

    def test_agent_routes_for_terminal_job(self):
        job = self.terminal()
        status, body = self.request(f"/agent/{job.id}/tools", headers=self.job_headers(job.token), token=None)
        self.assertEqual(status, 200)
        names = {tool["name"] for tool in body["tools"]}
        self.assertTrue({"hub_board", "hub_claim", "hub_release", "hub_wait", "multi_edit"} <= names)
        self.assertNotIn("subagent", names)
        execute = next(tool for tool in body["tools"] if tool["name"] == "execute_luau")
        self.assertIn("claim", execute["description"])
        self.assertIn("scope", execute["inputSchema"]["required"])
        call = f"/agent/{job.id}/call"
        status, body = self.request(call, {"name": "multi_edit", "arguments": {"file_path": "ServerScriptService.Main", "edits": EDITS}},
                                    self.job_headers(job.token), token=None)
        self.assertEqual(status, 200)
        self.assertTrue(body["isError"])
        status, body = self.request(call, {"name": "hub_claim", "arguments": {"paths": ["ServerScriptService.Main"]}}, self.job_headers(job.token), token=None)
        self.assertEqual((status, body["isError"]), (200, False))
        status, body = self.request(call, {"name": "multi_edit", "arguments": {"file_path": "ServerScriptService.Main", "edits": EDITS}},
                                    self.job_headers(job.token), token=None)
        self.assertEqual((status, body["isError"]), (200, False))
        self.assertEqual(self.native.calls[-1][0], "multi_edit")
        self.assertEqual(self.request("/v1/board")[1]["journal"][-1]["tool"], "multi_edit")
        self.assertEqual(self.request(call, {"name": "subagent", "arguments": {}}, self.job_headers(job.token), token=None)[0], 400)
        self.assertEqual(self.request(call, {"name": "hub_board", "arguments": {}}, self.job_headers("wrong"), token=None)[0], 401)

    def test_provider_routes_return_404(self):
        for path in ("/v1/provider/check", "/v1/provider/login"):
            with self.subTest(path=path):
                status, body = self.request(path, {"provider": "claude"})
                self.assertEqual(status, 404)
                self.assertFalse(body["ok"])


if __name__ == "__main__":
    unittest.main()

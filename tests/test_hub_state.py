"""Hub 1.0 — stare persistentă (hub-state.json versiunea 2) și relansare fără gap pe POSIX.

Hub.save/load cu ceas injectat (dispozitive cu token_hash, workspace-uri cu sumarul proiectului, sesiuni, claims per workspace, jurnal),
starea 0.8 (`format: 1`) ignorată cu hub_id păstrat, ClaimTable.export/restore, un dispozitiv care rămâne aprobat peste o repornire,
HubServer cu socket moștenit (socket real pe port efemer), main() cu --inherit-socket și calea de update POSIX cu os.name/os.execv înlocuite.
Fără rețea în afara loopback-ului; starea se scrie doar în %TEMP%\\studio-harness-1.0\\hub."""

import contextlib
import hashlib
import io
import json
import os
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import team_hub  # noqa: E402
from claims import ClaimTable  # noqa: E402
from team_hub import Hub, HubServer  # noqa: E402
from test_team_hub import BALL, KART, TOKEN, RecordingThread, device_id, device_token, event, free_port, journal_item, project, session_row, temp_dir  # noqa: E402
from test_updater import LocalChannel, make_zip  # noqa: E402

REAL_THREAD = threading.Thread


class ClaimTableStateTests(unittest.TestCase):
    def test_export_and_restore_round_trip_drop_expired_and_invalid_rows(self):
        now = [1000.0]
        table = ClaimTable(clock=lambda: now[0])
        table.claim("job-a", "ana", ["Workspace.Map", "@play:s1"], "harta")
        now[0] = 1200.0
        table.claim("job-b", "dan", ["ServerScriptService.Main"], "x" * 300)
        rows = table.export()
        self.assertEqual([(row["path"], row["job_id"], row["developer"], row["since"], row["last_touch"]) for row in rows],
                         [("Workspace.Map", "job-a", "ana", 1000.0, 1000.0), ("@play:s1", "job-a", "ana", 1000.0, 1000.0),
                          ("ServerScriptService.Main", "job-b", "dan", 1200.0, 1200.0)])
        self.assertEqual(len(rows[2]["reason"]), 200)
        self.assertNotIn("expires", rows[0])
        restored = ClaimTable(clock=lambda: now[0])
        self.assertEqual(restored.restore(rows), 3)
        self.assertEqual(restored.snapshot(), table.snapshot())
        # După 10 minute fără touch claims-urile lui job-a sunt expirate la restaurare; cele invalide se ignoră.
        now[0] = 1601.0
        later = ClaimTable(clock=lambda: now[0])
        bad = [{"path": "Workspace.X"}, {"path": 5, "job_id": "j", "since": 1.0}, {"path": "bad path\x00", "job_id": "j", "since": 1.0},
               {"path": "Workspace.Y", "job_id": "j", "since": "1"}, "nu", None]
        self.assertEqual(later.restore(rows + bad), 1)
        self.assertEqual([(row["path"], row["job_id"]) for row in later.snapshot()], [("ServerScriptService.Main", "job-b")])
        # `last_touch` lipsă cade pe `since`; duplicatele păstrează primul rând; restaurarea înlocuiește tabela; câmpurile în plus (device_id) sunt ignorate.
        again = ClaimTable(clock=lambda: 10.0)
        self.assertEqual(again.restore([{"path": "Workspace.A", "job_id": "j1", "since": 5.0, "device_id": "d"}, {"path": "workspace.A", "job_id": "j2", "since": 6.0}]), 1)
        self.assertEqual([(row["path"], row["job_id"], row["expires"]) for row in again.snapshot()], [("Workspace.A", "j1", 605.0)])
        self.assertEqual(again.restore(None), 0)
        self.assertEqual(again.snapshot(), [])


class HubStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = temp_dir()
        self.path = Path(self.temp.name) / "state" / "hub-state.json"
        self.now = [1000.0]
        self.lines = []
        self.hub = self.make_hub()

    def tearDown(self):
        self.temp.cleanup()

    def make_hub(self, now=None):
        return Hub(TOKEN, clock=lambda: self.now[0] if now is None else now, logger=self.lines.append, avatar_fetcher=lambda user, size: None)

    def actor(self, hub, name, admin=False):
        return hub.actor(device_token(name), TOKEN if admin else None, allow_unknown=True)

    def join(self, hub, name, workspace=BALL):
        hub.register(self.actor(hub, name), {"roblox": {"user_id": len(name), "name": name}, "machine": "pc-" + name, "bridge_id": "bridge-" + name,
                                             "version": "1.0.0", "workspace": workspace})
        hub.approve_device(hub.actor(None, TOKEN), {"device_id": device_id(name)})
        return device_id(name)

    def populate(self):
        ana = self.join(self.hub, "ana")
        dan = self.join(self.hub, "dan")
        self.hub.register(self.actor(self.hub, "nou"), {"roblox": None, "machine": "pc-nou", "bridge_id": "b", "workspace": KART})
        events = [event(seq, "mesaj " + str(seq)) for seq in range(1, 301)]
        self.hub.sync(self.actor(self.hub, "ana"), {"workspace": BALL, "sessions": [session_row("job-a", events=events, claims=["Workspace.Map"])],
                                                    "journal": [journal_item()]})
        self.hub.sync(self.actor(self.hub, "dan"), {"workspace": BALL, "sessions": [session_row("job-d", state="completed")],
                                                    "project_digest": "snap-1", "project": project()})
        self.hub.claim(self.actor(self.hub, "ana"), {"job_id": "job-a", "paths": ["Workspace.Map"], "reason": "harta"})
        self.hub.claim(self.actor(self.hub, "ana"), {"job_id": "job-k", "paths": ["Lighting"], "workspace": "game:222"})
        for _ in range(2):
            self.hub.sync(self.actor(self.hub, "ana"), {"workspace": BALL, "journal": [journal_item("execute_luau", time=1001.0, paths=[])]})
        self.hub.set_enrollment(self.hub.actor(None, TOKEN), {"mode": "open"})
        return ana, dan

    def load_into_new_hub(self, now=None):
        loaded = self.make_hub(now)
        return loaded, loaded.load(self.path)

    def test_save_and_load_round_trip_keeps_devices_workspaces_sessions_claims_journal_and_project_summaries(self):
        ana, dan = self.populate()
        self.assertEqual(self.hub.save(self.path), self.path)
        raw = self.path.read_bytes()
        for secret in (TOKEN, device_token("ana"), device_token("dan"), device_token("nou")):
            self.assertNotIn(secret.encode(), raw)
        self.assertEqual(sorted(path.name for path in self.path.parent.iterdir()), ["hub-state.json"])
        state = json.loads(raw.decode("utf-8"))
        self.assertEqual((state["version"], state["hub_version"], state["hub_id"], state["saved"], state["enrollment"], state["journal_seq"]),
                         (2, team_hub.VERSION, self.hub.hub_id, 1000.0, "open", 3))
        self.assertEqual(sorted(state["devices"]), sorted([ana, dan, device_id("nou")]))
        self.assertEqual(state["devices"][ana]["token_hash"], hashlib.sha256(device_token("ana").encode()).hexdigest())
        self.assertEqual((state["devices"][ana]["status"], state["devices"][ana]["approved_by"], state["devices"][ana]["workspace"]), ("approved", "admin", "game:987654"))
        self.assertEqual((state["devices"][device_id("nou")]["status"], state["devices"][device_id("nou")]["roblox_name"]), ("pending", ""))
        self.assertEqual(sorted(state["workspaces"]), ["game:222", "game:987654"])
        self.assertEqual(state["workspaces"]["game:987654"]["project"], {"digest": "snap-1", "snapshot_id": "snap-1", "count": 2, "truncated": False,
                                                                         "groups": {"assets": 1, "settings": 1}, "reported_by": dan, "at": 1000.0})
        self.assertEqual((state["workspaces"]["game:222"]["name"], state["workspaces"]["game:222"]["project"]), ("Kart", None))
        self.assertEqual(len(state["sessions"]["job-a"]["events"]), 256)
        self.assertEqual((state["sessions"]["job-a"]["events"][0]["seq"], state["sessions"]["job-a"]["workspace"], state["sessions"]["job-a"]["device_id"]), (45, "game:987654", ana))
        self.assertEqual({key: [(row["path"], row["job_id"], row["device_id"]) for row in rows] for key, rows in state["claims"].items()},
                         {"game:987654": [("Workspace.Map", "job-a", ana)], "game:222": [("Lighting", "job-k", ana)]})
        self.assertEqual([(row["seq"], row["workspace"], row["developer"]) for row in state["journal"]], [(1, "game:987654", "ana"), (2, "game:987654", "ana"), (3, "game:987654", "ana")])
        self.assertIn("time", state["journal"][0])
        self.assertNotIn("at", state["journal"][0])
        self.now[0] = 1010.0
        loaded, ok = self.load_into_new_hub()
        self.assertTrue(ok)
        self.assertEqual((loaded.hub_id, loaded.enrollment), (self.hub.hub_id, "open"))
        self.assertEqual(loaded.devices, self.hub.devices)
        self.assertEqual(loaded.admin_devices(loaded.actor(None, TOKEN))["devices"], self.hub.admin_devices(self.hub.actor(None, TOKEN))["devices"])
        self.assertEqual(loaded.journal_seq, 3)
        self.assertEqual(list(loaded.journal), list(self.hub.journal))
        for key in ("game:987654", "game:222"):
            self.assertEqual(loaded.claims[key].snapshot(), self.hub.claims[key].snapshot())
        self.assertEqual(loaded.job_devices, {"job-a": ana, "job-d": dan, "job-k": ana})
        self.assertEqual(loaded.sessions["job-a"]["meta"], self.hub.sessions["job-a"]["meta"])
        self.assertEqual(len(loaded.sessions["job-a"]["events"]), 256)
        self.assertEqual(loaded.sessions["job-d"]["closed_at"], 1000.0)
        self.assertTrue(any(line.startswith("stare încărcată din hub-state.json: 3 dispozitive, 2 workspace-uri, 2 sesiuni, 2 claims, 3 intrări de jurnal") for line in self.lines), self.lines)
        # Dispozitivul rămâne aprobat: sync-ul următor merge (fără reînregistrare) și hub-ul cere din nou proiectul complet.
        response = loaded.sync(self.actor(loaded, "dan"), {"workspace": BALL, "project_digest": "snap-1", "want": {"job-a": 250}})
        self.assertEqual((response["hub_id"], response["device"], response["want_project"], response["journal_seq"]), (self.hub.hub_id, {"status": "approved"}, True, 3))
        self.assertEqual([item["seq"] for item in response["events"]["job-a"]], list(range(251, 301)))
        self.assertIsNone(loaded.project(self.actor(loaded, "dan"), "game:987654")["project"])
        summary = loaded.workspace(self.actor(loaded, "dan"), "game:987654")["project"]
        self.assertEqual((summary["digest"], summary["count"], summary["groups"], summary["reported_by"]), ("snap-1", 2, {"assets": 1, "settings": 1}, dan))
        self.assertEqual(response["workspaces"][0]["project"], {"count": 2, "digest": "snap-1", "groups": {"assets": 1, "settings": 1}})
        # Proiectul complet retrimis înlocuiește sumarul.
        self.assertFalse(loaded.sync(self.actor(loaded, "dan"), {"workspace": BALL, "project_digest": "snap-1", "project": project()})["want_project"])
        self.assertEqual(loaded.project(self.actor(loaded, "dan"), "game:987654")["project"]["count"], 2)
        # Jurnalul continuă numerotarea globală; salvarea peste fișierul vechi este atomică.
        loaded.sync(self.actor(loaded, "ana"), {"workspace": BALL, "journal": [journal_item("insert_asset")]})
        self.assertEqual(loaded.journal_seq, 4)
        loaded.save(self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["journal_seq"], 4)
        self.assertEqual(sorted(path.name for path in self.path.parent.iterdir()), ["hub-state.json"])

    def test_state_is_written_with_restricted_permissions(self):
        """`hub-state.json` are token_hash-uri, identități și conținutul sesiunilor: 0600 și pe fișierul temporar, ca să nu existe
        o fereastră în care alt utilizator local să îl poată citi (un hub pornit manual are umask 022)."""
        restricted = []
        with patch.object(team_hub, "restrict_permissions", restricted.append):
            target = self.hub.save(self.path)
        self.assertEqual(restricted, [target.with_name(target.name + ".part"), target])
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_older_state_is_ignored_but_its_hub_id_is_kept(self):
        original = self.hub.hub_id
        self.path.parent.mkdir(parents=True)
        old = {"format": 1, "version": "0.8.0", "hub_id": "b" * 32, "members": {"m": {"member_id": "m", "developer": "ana", "machine": "pc", "last_seen": 1.0}},
               "sessions": {}, "claims": [{"path": "Workspace.Map", "job_id": "j", "developer": "ana", "since": 999.0}], "journal": [{"seq": 5}], "journal_seq": 5}
        self.path.write_text(json.dumps(old), encoding="utf-8")
        self.assertFalse(self.hub.load(self.path))
        self.assertEqual((self.hub.hub_id, self.hub.devices, self.hub.workspaces, self.hub.sessions, self.hub.claims, list(self.hub.journal), self.hub.journal_seq),
                         ("b" * 32, {}, {}, {}, {}, [], 0))
        self.assertEqual(len(self.lines), 1)
        self.assertIn("formatul 1 (hub 0.8.0), nu versiunea 2", self.lines[0])
        self.assertIn("hub-ul pornește curat; hub_id păstrat", self.lines[0])
        # Orice altă versiune se ignoră la fel; hub_id-ul se preia doar dacă este valid (32 hex), altfel rămâne cel curent.
        for content, label, hub_id in ((json.dumps({"version": 3, "hub_id": "c" * 32}), "versiunea 3, nu versiunea 2", "c" * 32),
                                       (json.dumps({"format": 1, "hub_id": "scurt"}), "formatul 1 (hub None), nu versiunea 2", "c" * 32),
                                       (json.dumps({"version": "2", "hub_id": "d" * 32}), "versiunea '2', nu versiunea 2", "d" * 32)):
            with self.subTest(content=content):
                self.lines.clear()
                self.path.write_text(content, encoding="utf-8")
                self.assertFalse(self.hub.load(self.path))
                self.assertEqual(self.hub.hub_id, hub_id)
                self.assertIn(label, self.lines[0])
                self.assertIn("se ignoră, hub-ul pornește curat", self.lines[0])
                self.assertEqual("hub_id păstrat" in self.lines[0], hub_id in content)
        self.assertNotEqual(original, self.hub.hub_id)

    def test_missing_or_invalid_files_leave_the_hub_empty_and_log_the_reason(self):
        original = self.hub.hub_id
        self.assertFalse(self.hub.load(self.path))
        self.assertEqual(self.lines, [])
        self.path.parent.mkdir(parents=True)
        cases = [("{", "invalidă"), ("[1]", "format necunoscut"), (json.dumps({"version": 2, "hub_id": "scurt"}), "hub_id"),
                 (json.dumps({"version": 2, "hub_id": "a" * 32, "devices": []}), "dispozitive invalide"),
                 (json.dumps({"version": 2, "hub_id": "a" * 32, "devices": {}, "workspaces": []}), "workspace-uri invalide"),
                 (json.dumps({"version": 2, "hub_id": "a" * 32, "devices": {}, "sessions": [], "claims": {}}), "sesiuni invalide"),
                 (json.dumps({"version": 2, "hub_id": "a" * 32, "devices": {}, "claims": []}), "claims invalide")]
        for content, expected in cases:
            with self.subTest(content=content):
                self.lines.clear()
                self.path.write_text(content, encoding="utf-8")
                self.assertFalse(self.hub.load(self.path))
                self.assertEqual(len(self.lines), 1)
                self.assertIn("hub-ul pornește curat", self.lines[0])
                self.assertIn(expected, self.lines[0])
                self.assertEqual((self.hub.hub_id, self.hub.devices, self.hub.sessions), (original, {}, {}))
        # Fișier prea mare: refuzat fără parsare.
        self.path.write_bytes(b"[" + b" " * (team_hub.MAX_STATE_BYTES + 1))
        self.lines.clear()
        self.assertFalse(self.hub.load(self.path))
        self.assertIn("limita de dimensiune", self.lines[0])

    def test_load_tolerates_partial_rows_and_keeps_the_valid_ones(self):
        ana = self.join(self.hub, "ana")
        token_hash = self.hub.devices[ana]["token_hash"]
        state = {"version": 2, "hub_id": self.hub.hub_id, "enrollment": "nimic", "journal_seq": "x",
                 "devices": {ana: {"device_id": ana, "token_hash": token_hash, "status": "approved", "machine": "pc-ana", "roblox_user_id": "x", "roblox_name": 5,
                                   "last_seen": 900.0, "reaped": True, "project_rejected": "snap-0", "workspace": "game:rau"},
                             "altul": {"device_id": "altul", "token_hash": "0" * 64, "status": "approved", "machine": "pc"},
                             "c" * 16: {"device_id": "c" * 16, "token_hash": "c" * 16 + "0" * 48, "status": "ciudat", "machine": "pc"},
                             "d" * 16: {"device_id": "d" * 16, "token_hash": "d" * 16 + "0" * 48, "status": "revoked", "machine": "pc-d", "roblox_user_id": 7, "roblox_name": "dana"}},
                 "workspaces": {"game:987654": {"game_id": 987654, "place_id": 1, "name": "", "creator_type": "group", "first_seen": "x", "project": {"snapshot_id": "s", "groups": {"ui": 3, "bad": "x"}, "count": "x"}},
                                "game:1": {"game_id": 2}, "rau": {"game_id": 1}, "place:5": "nu", "game:3": {"game_id": True}},
                 "sessions": {"job-a": {"device_id": ana, "meta": {"job_id": "job-a", "state": "running"}, "events": [{"seq": 1}, {"seq": "2"}, "nu"], "closed_at": "nu"},
                              "job-x": {"device_id": "necunoscut", "meta": {"job_id": "job-x"}}, "job-y": {"device_id": ana, "meta": {"job_id": "altul"}}, "job-z": 5,
                              "job-w": {"device_id": ana, "workspace": "game:777", "meta": {"job_id": "job-w", "state": "completed"}, "closed_at": 950.0}},
                 "journal": [{"seq": 7, "tool": "x", "workspace": "game:987654"}, {"seq": True}, {"nu": 1}],
                 "claims": {"game:987654": [{"path": "Workspace.Map", "job_id": "job-a", "developer": "ana", "since": 990.0, "last_touch": 995.0, "device_id": ana}],
                            "rau": [{"path": "Lighting", "job_id": "j", "since": 990.0}], "game:9": "nu"}}
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(state), encoding="utf-8")
        loaded, ok = self.load_into_new_hub()
        self.assertTrue(ok)
        self.assertEqual(loaded.enrollment, "approve")
        self.assertEqual(sorted(loaded.devices), sorted([ana, "d" * 16]))
        self.assertEqual(loaded.devices[ana], {"device_id": ana, "token_hash": token_hash, "roblox_user_id": 0, "roblox_name": "", "machine": "pc-ana", "bridge_id": "",
                                               "version": "", "status": "approved", "first_seen": 0.0, "last_seen": 900.0, "approved_at": 0.0, "approved_by": "",
                                               "workspace": None, "reaped": True, "project_rejected": "snap-0"})
        self.assertEqual((loaded.devices["d" * 16]["roblox_name"], loaded.devices["d" * 16]["status"]), ("dana", "revoked"))
        # Workspace-uri: cheia se recalculează din id-uri (game:1 cu game_id 2 se ignoră), numele gol devine cheia, stub-uri pentru sesiuni
        # (`local` pentru job-a, al cărui dispozitiv nu are workspace) și pentru claims.
        self.assertEqual(sorted(loaded.workspaces), ["game:777", "game:987654", "local"])
        ball = loaded.workspaces["game:987654"]
        self.assertEqual((ball["name"], ball["place_id"], ball["creator_type"], ball["first_seen"], ball["last_seen"]), ("game:987654", 1, "Group", 0.0, 0.0))
        self.assertEqual((ball["project"]["snapshot_id"], ball["project"]["count"], ball["project"]["groups"], ball["project"]["summary_only"]), ("s", 0, {"ui": {"count": 3}}, True))
        self.assertEqual(loaded.workspaces["game:777"]["name"], "game:777")
        self.assertEqual(sorted(loaded.sessions), ["job-a", "job-w"])
        self.assertEqual((loaded.sessions["job-a"]["events"], loaded.sessions["job-a"]["closed_at"], loaded.sessions["job-a"]["workspace"]), ([{"seq": 1}], None, "local"))
        self.assertEqual((loaded.sessions["job-a"]["meta"]["workspace"], loaded.sessions["job-a"]["meta"]["device_id"], loaded.sessions["job-a"]["meta"]["remote"]), ("local", ana, True))
        self.assertEqual((loaded.journal_seq, [row["seq"] for row in loaded.journal]), (7, [7]))
        self.assertEqual([(row["path"], row["expires"]) for row in loaded.claims["game:987654"].snapshot()], [("Workspace.Map", 1595.0)])
        self.assertEqual(sorted(loaded.claims), ["game:987654"])
        self.assertEqual(loaded.job_devices, {"job-a": ana, "job-w": ana})
        self.assertEqual(loaded.workspace(loaded.actor(None, TOKEN), "game:987654")["project"]["groups"], {"ui": 3})
        # Dispozitivul deja marcat offline la salvare rămâne așa până la sync, fără un nou mesaj „dispozitiv offline”; cel revocat nu este „reaped”.
        self.assertEqual([(row["status"], row["online"]) for row in loaded.admin_devices(loaded.actor(None, TOKEN))["devices"]], [("approved", False), ("revoked", False)])
        self.assertFalse(any("dispozitiv offline" in line for line in self.lines), self.lines)
        self.assertNotIn("reaped", loaded.devices["d" * 16])

    def test_expired_claims_and_offline_devices_are_cleaned_at_load(self):
        ana, dan = self.populate()
        self.hub.save(self.path)
        loaded, ok = self.load_into_new_hub(now=1010.0)
        self.assertTrue(ok)
        self.assertEqual([(row["path"], row["job_id"]) for row in loaded.claims["game:987654"].snapshot()], [("Workspace.Map", "job-a")])
        self.assertEqual([row["online"] for row in loaded.admin_devices(loaded.actor(None, TOKEN))["devices"] if row["status"] == "approved"], [True, True])
        self.assertEqual(loaded.sessions["job-a"]["meta"]["state"], "running")
        # La 50 s: nu mai sunt „online” (15 s), dar nici offline (60 s): claims-urile și sesiunile rămân.
        between, ok = self.load_into_new_hub(now=1050.0)
        self.assertTrue(ok)
        self.assertEqual([row["online"] for row in between.admin_devices(between.actor(None, TOKEN))["devices"]], [False, False, False])
        self.assertEqual((between.sessions["job-a"]["meta"]["state"], len(between.claims["game:987654"].snapshot())), ("running", 1))
        # Mult mai târziu: claims-urile au expirat, dispozitivele sunt offline și sesiunile lor devin `lost`.
        self.lines.clear()
        later, ok = self.load_into_new_hub(now=1700.0)
        self.assertTrue(ok)
        self.assertEqual([table.snapshot() for table in later.claims.values()], [[], []])
        self.assertEqual([row["online"] for row in later.admin_devices(later.actor(None, TOKEN))["devices"]], [False, False, False])
        self.assertEqual(later.sessions["job-a"]["meta"]["state"], "lost")
        self.assertTrue(any(line.startswith("dispozitiv offline: ana @ pc-ana") for line in self.lines), self.lines)
        self.assertEqual(later.hub_id, self.hub.hub_id)


class HubRestartTests(unittest.TestCase):
    """Un dispozitiv rămâne aprobat peste o repornire a hub-ului cu stare salvată; fără stare este necunoscut și revine ca pending."""

    def setUp(self):
        self.temp = temp_dir()
        self.path = Path(self.temp.name) / "hub-state.json"
        self.lines = []
        self.server = None
        self.thread = None

    def tearDown(self):
        self.stop()
        self.temp.cleanup()

    def start(self, hub):
        self.server = HubServer("127.0.0.1", 0, hub)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        return hub

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(2)
            self.server = None

    def request(self, path, body=None, admin=False):
        headers = {team_hub.DEVICE_HEADER: device_token("ana"), "Content-Type": "application/json"}
        if admin:
            headers[team_hub.ADMIN_HEADER] = TOKEN
        request = Request(self.base + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def register(self):
        return self.request("/hub/register", {"roblox": {"user_id": 101, "name": "ana"}, "machine": "pc-ana", "bridge_id": "b", "version": "1.0.0", "workspace": BALL})

    def test_saved_state_keeps_the_device_approved_with_its_claims_across_a_restart(self):
        first = self.start(Hub(TOKEN, logger=self.lines.append))
        status, body = self.register()
        self.assertEqual((status, body["status"]), (202, "pending"))
        self.assertEqual(self.request("/hub/admin/devices/approve", {"device_id": device_id("ana")}, admin=True)[0], 200)
        self.assertEqual(self.register()[0], 200)
        self.assertEqual(self.request("/hub/claims/claim", {"job_id": "job-a", "paths": ["Workspace.Map"], "reason": "harta"})[0], 200)
        hub_id = first.hub_id
        first.save(self.path)
        self.stop()
        second = Hub(TOKEN, logger=self.lines.append)
        self.assertTrue(second.load(self.path))
        self.start(second)
        status, body = self.request("/hub/sync", {"workspace": BALL, "touch": ["job-a"]})
        self.assertEqual((status, body["hub_id"], body["device"], [(row["path"], row["job_id"]) for row in body["claims"]]),
                         (200, hub_id, {"status": "approved"}, [("Workspace.Map", "job-a")]))
        self.assertEqual(self.register()[0], 200)
        self.assertFalse(any("dispozitiv nou" in line for line in self.lines[1:]), self.lines)
        # Fără stare salvată hub-ul nou nu cunoaște dispozitivul: 401 → reînregistrare → pending, cu alt hub_id.
        self.stop()
        self.start(Hub(TOKEN, logger=self.lines.append))
        status, body = self.request("/hub/sync", {"workspace": BALL})
        self.assertEqual((status, body["status"]), (401, "unknown"))
        status, body = self.register()
        self.assertEqual((status, body["status"]), (202, "pending"))
        self.assertNotEqual(body["hub_id"], hub_id)
        self.assertEqual(self.request("/hub/status")[1]["device"]["status"], "pending")


class InheritedSocketTests(unittest.TestCase):
    def setUp(self):
        self.temp = temp_dir()
        self.state = Path(self.temp.name) / "state"
        self.lines = []

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def listening_socket():
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        port = listener.getsockname()[1]
        return listener.detach(), port

    def test_server_serves_on_an_inherited_socket_without_binding(self):
        fd, port = self.listening_socket()
        server = HubServer("127.0.0.1", 0, Hub(TOKEN, logger=self.lines.append), inherited_fd=fd)
        try:
            self.assertEqual((server.server_address[1], server.server_port, server.inherited), (port, port, True))
            thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
            thread.start()
            with urlopen(Request(f"http://127.0.0.1:{port}/healthz"), timeout=5) as response:
                self.assertEqual(json.loads(response.read()), {"ok": True, "version": team_hub.VERSION})
            self.assertTrue(server.wait_idle(1.0))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
        self.assertFalse(HubServer("127.0.0.1", 0, Hub(TOKEN)).inherited)

    def test_reexec_arguments_replace_any_previous_inherit_socket(self):
        argv = ["--listen", "0.0.0.0", "--port", "34880", "--inherit-socket", "7", "--state-dir", "/var/lib/x", "--inherit-socket=9", "--open-enrollment"]
        self.assertEqual(team_hub.reexec_arguments(argv, 12), ["--listen", "0.0.0.0", "--port", "34880", "--state-dir", "/var/lib/x", "--open-enrollment", "--inherit-socket", "12"])
        self.assertEqual(team_hub.reexec_arguments([], 3), ["--inherit-socket", "3"])

    @contextlib.contextmanager
    def running(self, *flags, serve=None):
        """main() cu serve_forever înlocuit (implicit: nu face nimic) și cu firele înregistrate, nu pornite; codul de admin din env."""
        servers = []

        def serve_forever(server, poll_interval=0.5):
            servers.append(server)
            if serve:
                serve(server)

        argv = ["team_hub.py", "--listen", "127.0.0.1", "--port", str(free_port()), "--state-dir", str(self.state), "--no-auto-update", *flags]
        RecordingThread.instances = []
        with patch.object(sys, "argv", argv), patch.dict(os.environ, {"STUDIO_HARNESS_ADMIN_TOKEN": TOKEN, "STUDIO_HARNESS_OPEN_ENROLLMENT": ""}), \
                patch.object(team_hub, "log", self.lines.append), patch.object(team_hub.signal, "signal", lambda *_: None), \
                patch.object(HubServer, "serve_forever", serve_forever), patch.object(HubServer, "shutdown", lambda server: None), \
                patch.object(team_hub.threading, "Thread", RecordingThread):
            code = team_hub.main()
        yield code, servers

    def test_main_with_inherit_socket_listens_on_the_inherited_port_and_saves_state_at_exit(self):
        fd, port = self.listening_socket()
        with self.running("--inherit-socket", str(fd)) as (code, servers):
            pass
        self.assertEqual(code, 0)
        self.assertEqual(len(servers), 1)
        self.assertEqual((servers[0].inherited, servers[0].server_address[1]), (True, port))
        self.assertTrue(any(line.endswith("[socket moștenit, fără întrerupere]") and ":" + str(port) in line for line in self.lines), self.lines)
        state = json.loads((self.state / "hub-state.json").read_text(encoding="utf-8"))
        self.assertEqual((state["version"], state["devices"], state["workspaces"], state["sessions"], state["claims"], state["journal"]), (2, {}, {}, {}, {}, []))
        self.assertNotIn(TOKEN, json.dumps(state))
        self.assertEqual([thread.name for thread in RecordingThread.instances], ["studio-harness-hub-state"])

    def test_main_loads_saved_state_and_falls_back_to_bind_when_the_inherited_socket_is_unusable(self):
        saved = Hub(TOKEN, logger=self.lines.append)
        saved.register(saved.actor(device_token("ana"), None, allow_unknown=True), {"machine": "pc", "bridge_id": "b", "workspace": BALL})
        self.state.mkdir(parents=True)
        saved.save(self.state / "hub-state.json")
        with self.running("--inherit-socket", "999999") as (code, servers):
            pass
        self.assertEqual(code, 0)
        self.assertFalse(servers[0].inherited)
        self.assertTrue(any(line.startswith("socketul moștenit nu poate fi folosit") for line in self.lines), self.lines)
        self.assertTrue(any(line.startswith("stare încărcată din hub-state.json: 1 dispozitive, 1 workspace-uri") for line in self.lines), self.lines)
        self.assertEqual(servers[0].hub.hub_id, saved.hub_id)
        self.assertEqual(servers[0].hub.devices[device_id("ana")]["status"], "pending")
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            with self.running("--inherit-socket", "-1"):
                pass


class PosixReexecUpdateTests(unittest.TestCase):
    """Bucla de update pe POSIX (os.name înlocuit): după mirror+apply hub-ul salvează starea și face execv cu socketul moștenit."""

    def setUp(self):
        self.temp = temp_dir()
        self.app = Path(self.temp.name) / "app"
        self.app.mkdir()
        (self.app / "team_hub.py").write_bytes(b"# hub vechi\n")
        self.state = Path(self.temp.name) / "state"
        self.channel = LocalChannel().__enter__()
        (self.app / "update-channel.json").write_text(json.dumps({"manifest_url": self.channel.url("/manifest.json"), "auto": True}), encoding="utf-8")
        bundle = make_zip({"scripts/team_hub.py": b"# hub nou\n"}, top="studio-harness-hub-9.9.9")
        self.channel.manifest("9.9.9", hub=self.channel.entry("/studio-harness-hub-9.9.9-ubuntu.zip", bundle))
        self.lines = []
        self.execs = []
        self.inheritable = []
        self.fds = []
        RecordingThread.instances = []

    def tearDown(self):
        self.channel.__exit__(None, None, None)
        self.temp.cleanup()

    def run_main(self, execv):
        servers = []

        def serve_forever(server, poll_interval=0.5):
            # Cât timp „ascultă”, firul de update (neinstanțiat ca fir) rulează o singură iterație; canalul local are nevoie de fire reale.
            servers.append(server)
            self.fds.append(server.socket.fileno())
            update = next(thread for thread in RecordingThread.instances if thread.name == "studio-harness-hub-update")
            with patch.object(team_hub.time, "sleep", lambda seconds: None), patch.object(threading, "Thread", REAL_THREAD):
                update._target()

        self.port = str(free_port())
        argv = ["team_hub.py", "--listen", "127.0.0.1", "--port", self.port, "--state-dir", str(self.state)]
        environ = {"STUDIO_HARNESS_UPDATE_URL": "", "STUDIO_HARNESS_AUTO_UPDATE": "1", "STUDIO_HARNESS_ADMIN_TOKEN": TOKEN, "STUDIO_HARNESS_OPEN_ENROLLMENT": ""}
        with patch.object(sys, "argv", argv), patch.dict(os.environ, environ), patch.object(team_hub, "__file__", str(self.app / "team_hub.py")), \
                patch.object(team_hub, "log", self.lines.append), patch.object(team_hub.signal, "signal", lambda *_: None), \
                patch.object(HubServer, "serve_forever", serve_forever), patch.object(HubServer, "shutdown", lambda server: None), \
                patch.object(team_hub.threading, "Thread", RecordingThread), patch.object(team_hub, "reexec_supported", lambda: True), \
                patch.object(team_hub.os, "execv", execv), patch.object(team_hub.os, "set_inheritable", lambda fd, flag: self.inheritable.append((fd, flag))):
            code = team_hub.main()
        return code, servers[0]

    def test_update_saves_state_and_execs_the_new_code_with_the_listening_socket(self):
        code, server = self.run_main(lambda path, argv: self.execs.append((path, argv)))
        self.assertEqual(code, 0)
        self.assertEqual((self.app / "team_hub.py").read_bytes(), b"# hub nou\n")
        self.assertEqual(len(self.execs), 1)
        path, argv = self.execs[0]
        self.assertEqual(path, sys.executable)
        # Același interpret, scriptul (acum nou) din directorul aplicației, aceleași argumente plus socketul moștenit; fără codul de admin în argv.
        self.assertEqual(argv, [sys.executable, "-u", str(self.app / "team_hub.py"), "--listen", "127.0.0.1", "--port", self.port,
                                "--state-dir", str(self.state), "--inherit-socket", str(self.fds[0])])
        self.assertEqual(self.inheritable, [(self.fds[0], True)])
        state = json.loads((self.state / "hub-state.json").read_text(encoding="utf-8"))
        self.assertEqual((state["version"], state["hub_id"]), (2, server.hub.hub_id))
        update_lines = [line for line in self.lines if line.startswith(("actualizare 9.9.9", "relansez"))]
        self.assertEqual(update_lines, ["actualizare 9.9.9 aplicată; hub-ul se relansează fără întrerupere, cu socketul de ascultare moștenit",
                                        "relansez hub-ul cu socketul moștenit (fd " + argv[-1] + "), fără întrerupere"])
        self.assertNotIn(TOKEN, "\n".join(self.lines))

    def test_failed_exec_is_logged_and_the_process_exits_for_systemd(self):
        def execv(path, argv):
            self.execs.append(argv)
            raise OSError("exec refuzat")

        code, server = self.run_main(execv)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.execs), 1)
        self.assertTrue(any(line.startswith("relansarea a eșuat (OSError); hub-ul se închide") for line in self.lines), self.lines)
        self.assertTrue((self.state / "hub-state.json").is_file())
        self.assertEqual(server.socket.fileno(), -1)


if __name__ == "__main__":
    unittest.main()

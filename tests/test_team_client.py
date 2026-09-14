"""Clientul hub 1.0 (`HubClient`): mașina de stări, sync-ul cu workspace, claims-urile remote și proxy-urile, contra unui hub fals pe loopback.

Hub-ul fals (`FakeHub`) implementează rutele `/hub/*` din contract §3.4 cu un comportament controlat din test (pending, approved,
revoked, dispozitiv uitat, hub vechi, hub stricat). Fără rețea în afara loopback-ului, fără porturile 34871/34880, fără fișiere
în %LOCALAPPDATA%; intervalele clientului sunt injectate, deci nu există sleep-uri lungi.
"""

import copy
import json
import re
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import team_client  # noqa: E402
from claims import ClaimTable, display, normalize  # noqa: E402
from local_state import device_id_for  # noqa: E402
from team_client import (HUB_DOWN, HUB_OLD, HUB_RESTARTED, HUB_UNREACHABLE, HubClient, HubError, TeamClient, TeamError,  # noqa: E402
                         http_request, unavailable)

TOKEN = "0123456789abcdef" * 4
DEVICE_ID = device_id_for(TOKEN)
HUB_ID = "a" * 32
BALL = {"key": "game:987654", "game_id": 987654, "place_id": 1291603, "name": "Ball", "creator_id": 555, "creator_type": "User"}
ARENA = {"key": "game:111", "game_id": 111, "place_id": 222, "name": "Arena", "creator_id": 555, "creator_type": "User"}
ELLOB = {"user_id": 12345, "name": "ellob"}
HEX64 = re.compile(r"[0-9a-f]{64}")
USE_HUB = object()


def wait_until(predicate, timeout=4.0, step=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def raising(error):
    """Un transport injectat care eșuează mereu cu `error`."""
    def request(*_, **__):
        raise error
    return request


def session_row(job_id, state="running", events=(), **extra):
    row = {"job_id": job_id, "kind": "terminal", "provider": "claude", "developer": "ellob", "name": "Claude Code · Ball",
           "state": state, "studio_id": None, "cwd": "C:\\Ball", "started": 1000.0, "last_activity": 1000.0,
           "pending_approval": False, "claims": [], "workspace": BALL["key"], "events": list(events)}
    row.update(extra)
    return row


def remote_session(job_id, events, **extra):
    """O sesiune a altui dispozitiv, așa cum o ține hub-ul fals: meta fără evenimente + lista completă de evenimente."""
    meta = session_row(job_id, developer="ana", **extra)
    del meta["events"]
    return {"meta": meta, "events": list(events)}


def event(seq, text="mesaj"):
    return {"seq": seq, "type": "text", "text": text}


class FakeBridge:
    """Minimul cerut de HubClient de la daemon: identitate, sesiuni proprii cu cursoare, proiect, workspace-ul joburilor."""

    def __init__(self):
        self.bridge_id = "b" * 32
        self.version = "1.0.0"
        self.connected = False
        self.identity = {"roblox": None, "workspace": None}
        self.sessions = []           # rânduri proprii cu lista completă de evenimente
        self.project = None
        self.job_workspaces = {}
        self.transitions = []

    def plugin_connected(self):
        return self.connected

    def identity_payload(self):
        return copy.deepcopy(self.identity)

    def hub_payload(self, pushed):
        rows = []
        for session in self.sessions:
            row = dict(session)
            after = pushed.get(row["job_id"], 0)
            row["events"] = [item for item in session["events"] if item["seq"] > after]
            if row["events"]:
                pushed[row["job_id"]] = row["events"][-1]["seq"]
            rows.append(row)
        return rows

    def project_digest(self):
        return self.project["snapshot_id"] if self.project else None

    def project_payload(self):
        return copy.deepcopy(self.project)

    def job_workspace(self, job_id):
        return self.job_workspaces.get(job_id)

    def hub_state_changed(self, previous, current):
        self.transitions.append((previous, current))


class FakeHub:
    """Hub fals: rutele `/hub/*` din contract, cu stare în memorie și starea dispozitivului controlată din test.

    `mode` decide răspunsul la register (pending | approved | revoked | unknown | old404 | old401 | broken); `member_mode`, dacă este
    setat, suprascrie starea văzută de rutele de membru (sync, claims, GET) — de exemplu un hub care ne-a uitat după înregistrare.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.mode = "pending"
        self.member_mode = None
        self.hub_id = HUB_ID
        self.enrollment = "approve"
        self.now = 1000.0
        self.calls = []                # (method, path, query, device, body)
        self.registrations = []
        self.claims = {}               # workspace → ClaimTable
        self.sessions = {}             # sesiunile altora: job_id → {"meta", "events"}
        self.members = []
        self.journal = []
        self.journal_seq = 0
        self.want_project = False
        self.received_projects = []
        self.projects = {}             # key → proiect complet
        self.workspaces = {}           # key → meta
        self.device_workspace = None
        self.device_roblox = None
        self.sync_extra = {}
        self.reject_project = False    # ca hub-ul real când corpul cu harta proiectului depășește MAX_BODY: 413
        self.redirected = []           # cererile ajunse pe ținta unui redirect (clientul nu trebuie să le facă niciodată)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def table(self, key):
        with self.lock:
            return self.claims.setdefault(key, ClaimTable(clock=lambda: self.now))

    def bodies(self, path):
        with self.lock:
            return [body for method, called, query, device, body in self.calls if called == path]

    def paths(self):
        with self.lock:
            return [called for method, called, query, device, body in self.calls]

    def queries(self, path):
        with self.lock:
            return [query for method, called, query, device, body in self.calls if called == path]

    def summary(self, key):
        with self.lock:
            meta = self.workspaces.get(key) or {"key": key, "name": key}
            project = self.projects.get(key)
            return {**{field: meta.get(field) for field in ("key", "name", "game_id", "place_id", "creator_id", "creator_type")},
                    "last_seen": self.now, "members_online": [dict(row) for row in self.members], "sessions_active": len(self.sessions),
                    "claims": len(self.table(key).snapshot()),
                    "project": {"count": project["count"], "digest": project["snapshot_id"], "groups": {}} if project else None}

    # ----- rute -----

    @staticmethod
    def device_error(status):
        if status == "pending":
            return 403, {"ok": False, "error": "Dispozitivul așteaptă aprobarea adminului.", "status": "pending", "device_id": DEVICE_ID}
        if status == "revoked":
            return 403, {"ok": False, "error": "Dispozitivul a fost revocat de admin.", "status": "revoked", "device_id": DEVICE_ID}
        return 401, {"ok": False, "error": "Dispozitiv necunoscut; înregistrează-te.", "status": "unknown"}

    def register(self, body):
        with self.lock:
            self.registrations.append(body)
            self.device_roblox = body.get("roblox")
            self.device_workspace = (body.get("workspace") or {}).get("key")
            base = {"ok": True, "device_id": DEVICE_ID, "hub_id": self.hub_id, "enrollment": self.enrollment}
            if self.mode == "approved":
                return 200, {**base, "status": "approved"}
            if self.mode == "pending":
                return 202, {**base, "status": "pending"}
            return self.device_error(self.mode)

    def _member_status(self):
        return self.member_mode or self.mode

    def sync(self, body):
        with self.lock:
            if self._member_status() != "approved":
                return self.device_error(self._member_status())
            workspace = body.get("workspace")
            self.device_workspace = workspace.get("key") if isinstance(workspace, dict) else None
            if "roblox" in body:
                self.device_roblox = body["roblox"]
            key = self.device_workspace or "local"
            table = self.table(key)
            for job_id in body.get("touch", []):
                table.touch(job_id)
            for item in body.get("journal", []):
                self.journal_seq += 1
                self.journal.append({**item, "seq": self.journal_seq, "device_id": DEVICE_ID, "developer": "ellob",
                                     "workspace": item.get("workspace") or key})
            if body.get("project") is not None:
                if self.reject_project:
                    return 413, {"ok": False, "error": "Corpul cererii depășește limita permisă."}
                self.received_projects.append(body["project"])
                self.projects[key] = body["project"]
            digest = body.get("project_digest")
            want_project = bool(self.want_project and digest and not any(project["snapshot_id"] == digest for project in self.received_projects))
            events = {}
            for job_id, after in body.get("want", {}).items():
                entry = self.sessions.get(job_id)
                if entry:
                    events[job_id] = [item for item in entry["events"] if item["seq"] > after]
            after = body.get("journal_after", 0)
            journal = [row for row in self.journal if row["seq"] > after and row["workspace"] == key][-50:]
            sessions = sorted((dict(entry["meta"], remote=True, device_id="other", workspace=key) for entry in self.sessions.values()),
                              key=lambda row: row["started"])
            response = {"ok": True, "now": self.now, "hub_id": self.hub_id, "device": {"status": "approved"}, "workspace": self.device_workspace,
                        "members": [dict(row) for row in self.members], "sessions": sessions, "events": events,
                        "claims": [dict(row, workspace=key) for row in table.snapshot()], "journal": journal, "journal_seq": self.journal_seq,
                        "want_project": want_project, "workspaces": [self.summary(name) for name in sorted(set(self.workspaces) | {key})]}
            response.update(self.sync_extra)
            return 200, response

    def claim_route(self, action, body):
        with self.lock:
            if self._member_status() != "approved":
                return self.device_error(self._member_status())
            key = body.get("workspace") or self.device_workspace
            if not key:
                return 400, {"ok": False, "error": "Dispozitivul nu are un workspace curent."}
            table = self.table(key)
            job_id = body.get("job_id")
            if action == "claim":
                try:
                    added, found = table.claim(job_id, "ellob", body["paths"], body.get("reason", ""))
                except ValueError as error:
                    return 400, {"ok": False, "error": str(error)}
                if found:
                    return 409, {"ok": False, "error": "Conflict de claim.", "conflicts": found}
                return 200, {"ok": True, "claimed": [display(claim.path) for claim in added],
                             "held": [display(claim.path) for claim in table.held_by(job_id)]}
            if action == "release":
                released = table.release(job_id, body.get("paths"))
                return 200, {"ok": True, "released": [display(claim.path) for claim in released],
                             "held": [display(claim.path) for claim in table.held_by(job_id)]}
            if action == "touch":
                table.touch(job_id)
                return 200, {"ok": True, "held": [display(claim.path) for claim in table.held_by(job_id)]}
            if action != "wait":
                return 404, {"ok": False, "error": "Rută inexistentă."}
        # `wait` ține conexiunea (în afara lock-ului) cel mult timeout_seconds, ca hub-ul real.
        free = table.wait_free(normalize(body["path"]), min(float(body.get("timeout_seconds", 60)), 60.0))
        return 200, {"ok": True, "free": free}

    def get(self, path, query):
        with self.lock:
            if self._member_status() != "approved":
                return self.device_error(self._member_status())
            key = query.get("key", [None])[0]
            if path == "/hub/status":
                return 200, {"ok": True, "version": "1.0.0", "hub_id": self.hub_id, "enrollment": self.enrollment, "now": self.now, "admin": False,
                             "device": {"device_id": DEVICE_ID, "status": "approved"}, "workspaces": len(self.workspaces), "members_online": 1}
            if path == "/hub/workspaces":
                return 200, {"ok": True, "workspaces": [self.summary(name) for name in sorted(self.workspaces)]}
            if path == "/hub/workspace":
                if key not in self.workspaces:
                    return 404, {"ok": False, "error": "Workspace-ul nu există."}
                return 200, {"ok": True, "workspace": dict(self.workspaces[key]), "members": [dict(row) for row in self.members],
                             "sessions": [], "claims": [], "journal": [], "project": None}
            if path == "/hub/project":
                if key not in self.workspaces:
                    return 404, {"ok": False, "error": "Workspace-ul nu există."}
                return 200, {"ok": True, "project": copy.deepcopy(self.projects.get(key))}
            return 404, {"ok": False, "error": "Rută inexistentă."}

    def _handler(self):
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _send(self, status, payload, raw=None):
                data = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html" if raw is not None else "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _dispatch(self):
                url = urlsplit(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length)) if length else None
                device = self.headers.get("X-Studio-Harness-Device")
                with hub.lock:
                    hub.calls.append((self.command, url.path, url.query, device, body))
                    mode = hub.mode
                if mode == "old404":
                    return self._send(404, {"ok": False, "error": "Rută inexistentă."})
                if mode == "old401":
                    # Hub-ul 0.8 refuză orice rută fără tokenul echipei, fără câmpul `status`.
                    return self._send(401, {"ok": False, "error": "Tokenul echipei este invalid."})
                if mode == "broken":
                    return self._send(503, None, raw=b"<html>service unavailable</html>")
                if mode == "redirect":
                    if url.path == "/mutat":
                        with hub.lock:
                            hub.redirected.append(device)
                        return self._send(200, {"ok": True})
                    self.send_response(302)
                    self.send_header("Location", hub.url + "/mutat")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
                if not device or not HEX64.fullmatch(device):
                    return self._send(401, {"ok": False, "error": "Dispozitiv necunoscut; înregistrează-te.", "status": "unknown"})
                if self.command == "POST" and self.headers.get_content_type() != "application/json":
                    return self._send(415, {"ok": False, "error": "Este necesar Content-Type application/json."})
                if self.command == "POST" and url.path == "/hub/register":
                    return self._send(*hub.register(body))
                if self.command == "POST" and url.path == "/hub/sync":
                    return self._send(*hub.sync(body))
                if self.command == "POST" and url.path.startswith("/hub/claims/"):
                    return self._send(*hub.claim_route(url.path.rsplit("/", 1)[1], body))
                if self.command == "GET":
                    return self._send(*hub.get(url.path, parse_qs(url.query)))
                return self._send(404, {"ok": False, "error": "Rută inexistentă."})

            def do_GET(self):
                self._dispatch()

            def do_POST(self):
                self._dispatch()

        return Handler


class ClientBase(unittest.TestCase):
    def setUp(self):
        self.hub = FakeHub()
        self.bridge = FakeBridge()
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.stop()
            if client.is_alive():
                client.join(2)
        self.hub.close()

    def client(self, autostart=False, token=TOKEN, url=USE_HUB, **options):
        options.setdefault("interval", 0.02)
        options.setdefault("idle_interval", 0.05)
        options.setdefault("pending_interval", 0.05)
        options.setdefault("revoked_interval", 0.06)
        options.setdefault("backoff_start", 0.02)
        options.setdefault("backoff_max", 0.07)
        client = HubClient(self.bridge, self.hub.url if url is USE_HUB else url, token, "PC-TEST", autostart=autostart, **options)
        self.clients.append(client)
        return client

    def approved_client(self, **options):
        """Un client înregistrat și aprobat (doar register; primul sync vine la următorul `step`)."""
        self.hub.mode = "approved"
        self.bridge.identity = {"roblox": dict(ELLOB), "workspace": dict(BALL)}
        client = self.client(**options)
        self.assertEqual(client.step(), 0.0)
        self.assertEqual(client.status()["status"], "approved")
        return client


class HttpRequestTests(ClientBase):
    def test_sends_the_device_header_and_json_and_parses_error_payloads(self):
        self.hub.mode = "approved"
        response = http_request(self.hub.url + "/", TOKEN, "POST", "/hub/register",
                                {"roblox": None, "machine": "PC", "bridge_id": "b", "version": "1.0.0", "workspace": None}, timeout=5)
        self.assertEqual((response["ok"], response["status"], response["device_id"]), (True, "approved", DEVICE_ID))
        method, path, query, device, body = self.hub.calls[-1]
        self.assertEqual((method, path, device, body["machine"]), ("POST", "/hub/register", TOKEN, "PC"))
        with self.assertRaises(HubError) as error:
            http_request(self.hub.url, TOKEN, "GET", "/hub/workspace?key=game:1", timeout=5)
        self.assertEqual((error.exception.status, str(error.exception), error.exception.payload["error"]),
                         (404, "Workspace-ul nu există.", "Workspace-ul nu există."))
        self.assertFalse(error.exception.unreachable)
        self.hub.mode = "revoked"
        with self.assertRaises(HubError) as error:
            http_request(self.hub.url, TOKEN, "POST", "/hub/register", {"machine": "PC"}, timeout=5)
        self.assertEqual((error.exception.status, error.exception.payload["status"]), (403, "revoked"))
        with self.assertRaises(HubError) as error:
            http_request(self.hub.url, "scurt", "GET", "/hub/status", timeout=5)
        self.assertEqual((error.exception.status, error.exception.payload.get("status")), (401, "unknown"))

    def test_non_json_bodies_and_network_failures_become_hub_errors(self):
        self.hub.mode = "broken"
        with self.assertRaises(HubError) as error:
            http_request(self.hub.url, TOKEN, "GET", "/hub/status", timeout=5)
        self.assertEqual((error.exception.status, str(error.exception), error.exception.payload), (503, "Hub-ul a răspuns HTTP 503.", {}))
        self.assertTrue(error.exception.unreachable)
        with patch.object(team_client._OPENER, "open", side_effect=URLError(ConnectionRefusedError("nimic nu ascultă"))):
            with self.assertRaises(HubError) as error:
                http_request("http://127.0.0.1:1", TOKEN, "GET", "/hub/status", timeout=1)
        self.assertEqual((error.exception.status, str(error.exception)), (503, HUB_DOWN))
        self.assertTrue(error.exception.unreachable)
        with patch.object(team_client._OPENER, "open", side_effect=TimeoutError("timeout")):
            with self.assertRaises(HubError) as error:
                http_request("http://127.0.0.1:1", TOKEN, "GET", "/hub/status", timeout=1)
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(HubError("x").status, 503)
        self.assertEqual(str(HubError()), HUB_DOWN)

    def test_redirects_are_refused_so_the_device_token_stays_on_the_hub_host(self):
        """`urllib` ar copia antetele (deci tokenul) către gazda din `Location`, inclusiv pe http: un 30x devine eroare, nu cerere."""
        self.hub.mode = "redirect"
        with self.assertRaises(HubError) as error:
            http_request(self.hub.url, TOKEN, "GET", "/hub/status", timeout=5)
        self.assertEqual((error.exception.status, self.hub.redirected), (302, []))
        self.assertIsNone(team_client._NoRedirect().redirect_request(None, None, 302, "Found", {}, "http://alt-host/hub/status"))

    def test_aliases_keep_the_08_names(self):
        self.assertIs(TeamClient, HubClient)
        self.assertIs(TeamError, HubError)


class StateMachineTests(ClientBase):
    def test_pending_then_approved_then_sync(self):
        self.bridge.identity = {"roblox": dict(ELLOB), "workspace": dict(BALL)}
        client = self.client()
        self.assertEqual(client.status(), {"url": self.hub.url, "status": "connecting", "hub_id": None, "device_id": DEVICE_ID,
                                           "error": None, "last_sync": None, "enrollment": None})
        self.assertEqual(client.step(), client.pending_interval)
        status = client.status()
        self.assertEqual((status["status"], status["hub_id"], status["device_id"], status["error"], status["enrollment"]),
                         ("pending", HUB_ID, DEVICE_ID, None, "approve"))
        self.assertEqual(self.hub.registrations[-1], {"roblox": ELLOB, "machine": "PC-TEST", "bridge_id": "b" * 32, "version": "1.0.0", "workspace": BALL})
        self.assertEqual(self.hub.paths(), ["/hub/register"])
        # Încă în așteptare: doar register, la pending_interval.
        self.assertEqual(client.step(), client.pending_interval)
        self.assertEqual(self.hub.paths(), ["/hub/register", "/hub/register"])
        self.hub.mode = "approved"
        self.assertEqual(client.step(), 0.0)
        self.assertEqual((client.status()["status"], client.status()["last_sync"]), ("approved", None))
        self.assertEqual(client.step(), client.idle_interval)
        self.assertEqual(self.hub.paths()[-2:], ["/hub/register", "/hub/sync"])
        self.assertIsNotNone(client.status()["last_sync"])
        self.assertEqual(self.bridge.transitions, [("connecting", "pending"), ("pending", "approved")])
        # Ritmul: rapid cu pluginul conectat sau cu o sesiune terminal deschisă, altfel lent.
        self.bridge.connected = True
        self.assertEqual(client.step(), client.interval)
        self.bridge.connected = False
        self.bridge.sessions = [session_row("job-a")]
        self.assertEqual(client.step(), client.interval)
        self.bridge.sessions = [session_row("job-a", state="completed")]
        self.assertEqual(client.step(), client.idle_interval)

    def test_offline_backs_off_and_recovers(self):
        failing = {"active": True}

        def request(method, path, body=None, timeout=10.0):
            if failing["active"]:
                raise HubError()
            return http_request(self.hub.url, TOKEN, method, path, body, timeout)

        client = self.client(request=request)
        self.assertEqual([client.step() for _ in range(6)], [0.02, 0.04, 0.07, 0.07, 0.07, 0.07])
        self.assertEqual((client.status()["status"], client.status()["error"]), ("offline", HUB_UNREACHABLE))
        self.assertEqual(self.bridge.transitions, [("connecting", "offline")])
        # Hub-ul revine: înregistrare aprobată, sync imediat, backoff-ul resetat la următoarea cădere.
        self.hub.mode = "approved"
        failing["active"] = False
        self.assertEqual(client.step(), 0.0)
        self.assertEqual(client.status()["status"], "approved")
        failing["active"] = True
        self.assertEqual(client.step(), 0.02)
        self.assertEqual(client.status()["status"], "offline")

    def test_proxy_503_without_json_is_offline_and_other_http_errors_show_their_message(self):
        self.hub.mode = "broken"
        client = self.client()
        client.step()
        self.assertEqual((client.status()["status"], client.status()["error"]), ("offline", HUB_UNREACHABLE))
        client = self.client(request=raising(HubError("Eroare internă a hub-ului.", 500, {"error": "x"})))
        client.step()
        self.assertEqual((client.status()["status"], client.status()["error"]), ("offline", "Eroare internă a hub-ului."))

    def test_revoked_retries_slowly_and_refuses_remote_calls(self):
        self.hub.mode = "revoked"
        client = self.client()
        self.assertEqual(client.step(), client.revoked_interval)
        self.assertEqual((client.status()["status"], client.status()["device_id"], client.status()["error"]), ("revoked", DEVICE_ID, None))
        for call in (lambda: client.claim("job", "ellob", ["Workspace.Map"]), lambda: client.release("job"), lambda: client.touch("job"),
                     lambda: client.wait_free(("Workspace",), 1), client.fetch_workspaces, lambda: client.fetch_workspace("game:1"),
                     lambda: client.fetch_project("game:1")):
            with self.assertRaises(HubError) as error:
                call()
            self.assertEqual((error.exception.status, str(error.exception)), (503, unavailable("revoked")))
        self.assertEqual(self.hub.paths(), ["/hub/register"])
        self.assertEqual((client.snapshot(), client.expire(), client.held_by("job")), ([], [], []))

    def test_old_hub_is_offline_with_the_version_message(self):
        for mode in ("old404", "old401"):
            with self.subTest(mode=mode):
                self.hub.mode = mode
                client = self.client()
                self.assertEqual(client.step(), client.backoff_start)
                self.assertEqual((client.status()["status"], client.status()["error"]), ("offline", HUB_OLD))
        # 401 cu `status: unknown` la register (hub 1.0 care refuză antetul) rămâne offline, cu mesajul hub-ului.
        self.hub.mode = "approved"
        client = self.client(token="a" * 63)
        client.step()
        self.assertEqual((client.status()["status"], client.status()["error"]), ("offline", "Dispozitiv necunoscut; înregistrează-te."))

    def test_unknown_device_at_sync_reregisters_immediately(self):
        client = self.approved_client()
        self.hub.member_mode = "unknown"
        self.assertEqual(client.step(), 0.0)
        self.assertEqual((client.status()["status"], client.status()["error"]), ("connecting", None))
        self.hub.member_mode = None
        self.assertEqual(client.step(), 0.0)
        self.assertEqual(client.status()["status"], "approved")
        self.assertEqual(self.hub.paths(), ["/hub/register", "/hub/sync", "/hub/register"])
        self.assertEqual(client.step(), client.idle_interval)
        # Hub-ul ne uită după fiecare înregistrare: a doua reînregistrare consecutivă nu mai e la 0 s (fără buclă strânsă).
        self.hub.member_mode = "unknown"
        self.assertEqual([client.step() for _ in range(5)], [0.0, 0.0, client.interval, 0.0, client.interval])
        self.assertEqual(client.status()["status"], "connecting")

    def test_pending_or_revoked_at_sync_changes_the_state(self):
        client = self.approved_client()
        self.hub.mode = "pending"
        self.assertEqual(client.step(), client.pending_interval)
        self.assertEqual(client.status()["status"], "pending")
        self.hub.mode = "approved"
        self.assertEqual(client.step(), 0.0)
        self.hub.mode = "revoked"
        self.assertEqual(client.step(), client.revoked_interval)
        self.assertEqual(client.status()["status"], "revoked")
        self.assertEqual(self.bridge.transitions[-3:], [("approved", "pending"), ("pending", "approved"), ("approved", "revoked")])

    def test_hub_restarted_with_another_id_reregisters_and_resets_cursors(self):
        self.bridge.sessions = [session_row("job-a", events=[event(1), event(2)])]
        client = self.approved_client()
        client.step()
        self.assertEqual(client.pushed, {"job-a": 2})
        self.hub.hub_id = "c" * 32
        with self.assertRaises(HubError) as error:
            client.sync_once()
        self.assertEqual((str(error.exception), error.exception.status), (HUB_RESTARTED, 401))
        self.assertEqual((client.status()["status"], client.status()["hub_id"], client.pushed, client.journal_cursor), ("connecting", "c" * 32, {}, 0))
        self.assertEqual(client.step(), 0.0)
        self.assertEqual(client.status()["status"], "approved")
        client.step()
        self.assertEqual([item["seq"] for item in self.hub.bodies("/hub/sync")[-1]["sessions"][0]["events"]], [1, 2])

    def test_bridge_failures_do_not_kill_the_loop(self):
        client = self.approved_client()
        self.bridge.hub_payload = raising(TypeError("payload"))
        self.assertEqual(client.step(), client.backoff_start)
        self.assertEqual((client.status()["status"], client.status()["error"]), ("offline", "Sincronizarea cu hub-ul a eșuat: TypeError"))

    def test_disabled_client_has_no_thread_and_refuses_everything(self):
        client = self.client(url=None, autostart=True)
        self.assertEqual(client.status(), {"url": None, "status": "disabled", "hub_id": None, "device_id": DEVICE_ID, "error": team_client.HUB_DISABLED,
                                           "last_sync": None, "enrollment": None})
        self.assertFalse(client.is_alive())
        self.assertIsNone(client.step())
        client.set_identity(ELLOB, BALL)
        self.assertEqual(client.workspace_key(), BALL["key"])
        with self.assertRaises(HubError) as error:
            client.claim("job", "ellob", ["Workspace.Map"])
        self.assertEqual(str(error.exception), unavailable("disabled"))
        self.assertEqual(HubClient(self.bridge, None, TOKEN, "PC", autostart=False, disabled_error="hub_url invalid (sursa: env)").status()["error"],
                         "hub_url invalid (sursa: env)")
        self.assertEqual(self.hub.calls, [])

    def test_status_and_errors_never_contain_the_token(self):
        self.hub.mode = "revoked"
        client = self.client()
        client.step()
        blob = json.dumps([client.status(), client.member_rows(), client.workspace_rows(), client.journal_rows(10), client.remote_rows()])
        self.assertNotIn(TOKEN, blob)
        self.assertNotIn(TOKEN, json.dumps(vars(client), default=str))
        with self.assertRaises(HubError) as error:
            client.touch("job")
        self.assertNotIn(TOKEN, str(error.exception) + json.dumps(error.exception.payload))


class SyncTests(ClientBase):
    def test_sync_body_carries_workspace_and_roblox_only_when_changed(self):
        client = self.approved_client()
        self.bridge.sessions = [session_row("job-a", events=[event(1), event(2)])]
        self.bridge.project = {"snapshot_id": "snap-1", "count": 3, "groups": {}, "nodes": []}
        client.record({"time": 1.0, "job_id": "job-a", "provider": "claude", "tool": "multi_edit", "paths": ["ServerScriptService.Main"],
                       "summary": "multi_edit · ServerScriptService.Main", "workspace": BALL["key"]})
        client.touch_pending.add("job-a")
        client.step()
        body = self.hub.bodies("/hub/sync")[-1]
        self.assertEqual((body["workspace"], body["journal_after"], body["touch"], body["project_digest"], body["want"]),
                         (BALL, 0, ["job-a"], "snap-1", {}))
        self.assertNotIn("roblox", body)
        self.assertNotIn("project", body)
        self.assertEqual([item["seq"] for item in body["sessions"][0]["events"]], [1, 2])
        self.assertEqual(body["journal"][0]["tool"], "multi_edit")
        self.assertEqual(client.pushed, {"job-a": 2})
        # Al doilea sync: doar evenimentele noi; jurnalul numerotat a ajuns în oglindă, cursorul urmează journal_seq.
        self.bridge.sessions[0]["events"].append(event(3))
        client.step()
        body = self.hub.bodies("/hub/sync")[-1]
        self.assertEqual(([item["seq"] for item in body["sessions"][0]["events"]], body["journal"], body["journal_after"]), ([3], [], 1))
        self.assertEqual([(row["seq"], row["tool"], row["workspace"]) for row in client.journal_rows(10)], [(1, "multi_edit", BALL["key"])])
        self.assertEqual(client.journal_rows(10, workspace="game:1"), [])
        # Contul s-a schimbat: `roblox` apare o singură dată.
        self.bridge.identity["roblox"] = {"user_id": 12345, "name": "ellob2"}
        client.step()
        self.assertEqual(self.hub.bodies("/hub/sync")[-1]["roblox"], {"user_id": 12345, "name": "ellob2"})
        client.step()
        self.assertNotIn("roblox", self.hub.bodies("/hub/sync")[-1])
        self.assertEqual(self.hub.device_roblox, {"user_id": 12345, "name": "ellob2"})
        # user_id 0 = identitate necunoscută → null.
        self.bridge.identity["roblox"] = {"user_id": 0, "name": "Studio"}
        client.step()
        self.assertIsNone(self.hub.bodies("/hub/sync")[-1]["roblox"])

    def test_want_project_sends_the_project_once_and_wakes_immediately(self):
        client = self.approved_client()
        self.bridge.project = {"snapshot_id": "snap-1", "count": 3, "groups": {}, "nodes": []}
        self.hub.want_project = True
        client.step()
        self.assertTrue(client.want_project)
        self.assertTrue(client._wake.is_set())
        client._wake.clear()
        client.step()
        self.assertEqual([project["snapshot_id"] for project in self.hub.received_projects], ["snap-1"])
        self.assertFalse(client.want_project)
        client.step()
        self.assertEqual(len(self.hub.received_projects), 1)
        self.assertNotIn("project", self.hub.bodies("/hub/sync")[-1])

    def test_a_project_refused_with_413_is_not_resent_until_a_new_scan(self):
        """Harta care nu încape în corpul hubului ar cădea identic la fiecare sync: dispozitivul nu ar mai sincroniza nimic."""
        client = self.approved_client()
        self.bridge.project = {"snapshot_id": "snap-1", "count": 3, "groups": {}, "nodes": []}
        self.hub.want_project = True
        self.hub.reject_project = True
        client.step()
        self.assertTrue(client.want_project)
        client.step()
        self.assertIn("project", self.hub.bodies("/hub/sync")[-1])
        self.assertEqual(client.status()["status"], "offline")
        # Reînregistrare, apoi un sync normal: același proiect nu se mai trimite, deci restul stării circulă mai departe.
        client.step()
        client.step()
        self.assertNotIn("project", self.hub.bodies("/hub/sync")[-1])
        self.assertEqual((client.status()["status"], client.status()["error"]), ("approved", None))
        # O scanare nouă are alt digest și se încearcă din nou.
        self.hub.reject_project = False
        self.bridge.project = {"snapshot_id": "snap-2", "count": 4, "groups": {}, "nodes": []}
        client.step()
        client.step()
        self.assertEqual([project["snapshot_id"] for project in self.hub.received_projects], ["snap-2"])

    def test_failed_sync_requeues_journal_and_touches(self):
        client = self.approved_client()
        client.record({"time": 1.0, "job_id": "job-a", "tool": "multi_edit", "paths": [], "summary": "x", "workspace": BALL["key"]})
        client.touch_pending.add("job-a")
        self.hub.mode = "broken"
        client.step()
        self.assertEqual(client.status()["status"], "offline")
        self.assertEqual(([row["tool"] for row in client.pending_journal], client.touch_pending), (["multi_edit"], {"job-a"}))
        self.hub.mode = "approved"
        client.step()
        client.step()
        body = self.hub.bodies("/hub/sync")[-1]
        self.assertEqual(([row["tool"] for row in body["journal"]], body["touch"]), (["multi_edit"], ["job-a"]))
        self.assertEqual((list(client.pending_journal), client.touch_pending), ([], set()))

    def test_journal_is_sent_in_batches_of_thirty_two_without_losing_entries(self):
        """Hub-ul ignoră tăcut intrările peste `MAX_SYNC_JOURNAL`: clientul trimite exact 32 pe sync și păstrează restul."""
        client = self.approved_client()
        for index in range(40):
            client.record({"time": float(index), "job_id": "job-a", "tool": "tool-" + str(index), "paths": [],
                           "summary": "s" + str(index), "workspace": BALL["key"]})
        client._wake.clear()
        client.step()
        body = self.hub.bodies("/hub/sync")[-1]
        self.assertEqual([row["tool"] for row in body["journal"]], ["tool-" + str(index) for index in range(32)])
        self.assertEqual([row["tool"] for row in client.pending_journal], ["tool-" + str(index) for index in range(32, 40)])
        # Coada rămasă trezește imediat ciclul următor, deci restul nu așteaptă un interval întreg.
        self.assertTrue(client._wake.is_set())
        client.step()
        body = self.hub.bodies("/hub/sync")[-1]
        self.assertEqual([row["tool"] for row in body["journal"]], ["tool-" + str(index) for index in range(32, 40)])
        self.assertEqual(list(client.pending_journal), [])
        # Toate cele 40 au ajuns în jurnalul hub-ului, în ordine, fără goluri.
        self.assertEqual([row["tool"] for row in self.hub.journal], ["tool-" + str(index) for index in range(40)])

    def test_mirror_members_sessions_events_claims_and_workspaces(self):
        self.hub.members = [{"device_id": DEVICE_ID, "roblox_user_id": 12345, "roblox_name": "ellob", "machine": "PC-TEST", "last_seen": 1000.0, "online": True},
                            {"device_id": "other", "roblox_user_id": 777, "roblox_name": "ana", "machine": "PC-ANA", "last_seen": 1000.0, "online": True}]
        self.hub.sessions["job-ana"] = remote_session("job-ana", [event(1, "a"), event(2, "b")])
        self.hub.workspaces = {BALL["key"]: dict(BALL), ARENA["key"]: dict(ARENA)}
        self.hub.table(BALL["key"]).claim("job-ana", "ana", ["Workspace.Map"], "harta")
        self.hub.table(ARENA["key"]).claim("job-x", "x", ["Workspace.Arena"], "")
        client = self.approved_client()
        client.step()
        self.assertEqual([(row["roblox_name"], row["me"]) for row in client.member_rows()], [("ellob", True), ("ana", False)])
        rows = client.remote_rows()
        self.assertEqual([(row["job_id"], row["remote"], row["mine"], row["workspace"]) for row in rows], [("job-ana", True, False, BALL["key"])])
        self.assertTrue(client.is_remote("job-ana"))
        self.assertFalse(client.is_remote("job-a"))
        # O sesiune nouă a unui coleg trezește ciclul; evenimentele ei vin la sync-ul următor și se acumulează după cursor.
        self.assertTrue(client._wake.is_set())
        client._wake.clear()
        client.step()
        self.assertEqual(self.hub.bodies("/hub/sync")[-1]["want"], {"job-ana": 0})
        snapshot = client.remote_snapshot("job-ana", 0)
        self.assertEqual(([item["seq"] for item in snapshot["events"]], snapshot["last_seq"], snapshot["remote"], snapshot["state"]), ([1, 2], 2, True, "running"))
        self.assertEqual(client.remote_snapshot("job-ana", 2)["events"], [])
        self.assertIsNone(client.remote_snapshot("job-a", 0))
        self.hub.sessions["job-ana"]["events"].append(event(3, "c"))
        client.step()
        self.assertEqual(self.hub.bodies("/hub/sync")[-1]["want"], {"job-ana": 2})
        self.assertEqual([item["seq"] for item in client.remote_snapshot("job-ana", 0)["events"]], [1, 2, 3])
        # Claims-urile workspace-ului curent, cu `workspace`; deținătorul se vede din oglindă.
        self.assertEqual([(row["path"], row["job_id"], row["workspace"]) for row in client.snapshot()], [("Workspace.Map", "job-ana", BALL["key"])])
        holder = client.holder(("Workspace", "Map", "Zone3"))
        self.assertEqual((holder.job_id, holder.developer, display(holder.path)), ("job-ana", "ana", "Workspace.Map"))
        self.assertIsNone(client.holder(("Workspace", "Map"), exclude_job="job-ana"))
        self.assertIsNone(client.holder(("Workspace", "Arena")))
        self.assertTrue(client.covers("job-ana", ("Workspace", "Map", "Zone3")))
        self.assertFalse(client.covers("job-a", ("Workspace", "Map")))
        # Sumarul workspace-urilor, cu `mine` pe cheia curentă.
        self.assertEqual([(row["key"], row["name"], row["mine"], row["members_online"][0]["roblox_name"]) for row in client.workspace_rows()],
                         [(ARENA["key"], "Arena", False, "ellob"), (BALL["key"], "Ball", True, "ellob")])
        # Sesiunea colegului dispare din oglindă când hub-ul nu o mai trimite.
        del self.hub.sessions["job-ana"]
        client.step()
        self.assertEqual((client.remote_rows(), client.remote_events), ([], {}))

    def test_changing_the_workspace_asks_the_journal_of_the_new_one_from_zero(self):
        self.hub.journal.append({"seq": 1, "time": 0.5, "tool": "insert_asset", "paths": [], "summary": "y", "workspace": ARENA["key"],
                                 "device_id": "other", "developer": "ana"})
        self.hub.journal_seq = 1
        client = self.approved_client()
        client.step()
        self.assertEqual((client.journal_rows(10), client.journal_cursor), ([], 1))
        client.record({"time": 1.0, "job_id": "job-a", "tool": "multi_edit", "paths": [], "summary": "x", "workspace": BALL["key"]})
        client.step()
        self.assertEqual(([row["seq"] for row in client.journal_rows(10)], client.journal_cursor), ([2], 2))
        self.bridge.identity["workspace"] = dict(ARENA)
        client.step()
        body = self.hub.bodies("/hub/sync")[-1]
        self.assertEqual((body["workspace"], body["journal_after"]), (ARENA, 0))
        self.assertEqual([(row["seq"], row["workspace"]) for row in client.journal_rows(10)], [(1, ARENA["key"]), (2, BALL["key"])])
        self.assertEqual([row["seq"] for row in client.journal_rows(10, workspace=ARENA["key"])], [1])
        self.assertEqual(client.hub_workspace, ARENA["key"])
        client.step()
        self.assertEqual(self.hub.bodies("/hub/sync")[-1]["journal_after"], 2)

    def test_sync_once_without_registration_registers_first(self):
        self.hub.mode = "approved"
        client = self.client()
        response = client.sync_once()
        self.assertEqual((response["ok"], self.hub.paths()), (True, ["/hub/register", "/hub/sync"]))
        self.hub.mode = "pending"
        client = self.client()
        with self.assertRaises(HubError) as error:
            client.sync_once()
        self.assertEqual((str(error.exception), client.status()["status"]), (unavailable("pending"), "pending"))

    def test_invalid_responses_are_tolerated(self):
        client = self.approved_client()
        self.hub.sync_extra = {"members": "x", "sessions": [3, {"job_id": ""}], "events": {"job": 1}, "claims": {"a": 1}, "journal": [{"seq": "1"}],
                               "journal_seq": "2", "workspaces": [{"name": "fără cheie"}], "want_project": "yes", "device": "?"}
        client.step()
        self.assertEqual(client.status()["status"], "approved")
        self.assertEqual((client.member_rows(), client.remote_rows(), client.snapshot(), client.journal_rows(5), client.workspace_rows(), client.want_project),
                         ([], [], [], [], [], False))
        client = self.client(request=lambda *args, **kwargs: {"ok": True, "status": "something"})
        with self.assertRaises(HubError):
            client.register()
        self.assertEqual((client.status()["status"], client.status()["error"]), ("offline", team_client.HUB_INVALID))
        client = self.client(request=lambda *args, **kwargs: ["nu", "obiect"])
        with self.assertRaises(HubError) as error:
            client.register()
        self.assertEqual(error.exception.status, 502)


class IdentityTests(ClientBase):
    def test_set_identity_wakes_the_loop_and_reaches_the_hub_at_once(self):
        self.hub.mode = "approved"
        client = self.client(autostart=True, interval=0.5, idle_interval=0.5, pending_interval=0.5)
        self.assertTrue(wait_until(lambda: client.status()["last_sync"] is not None))
        self.assertIsNone(self.hub.registrations[-1]["workspace"])
        synced = len(self.hub.bodies("/hub/sync"))
        # Bridge fără identity_payload: identitatea vine doar prin set_identity.
        self.bridge.identity_payload = None
        client.set_identity(ELLOB, BALL)
        self.assertTrue(wait_until(lambda: self.hub.device_workspace == BALL["key"], timeout=0.4))
        body = self.hub.bodies("/hub/sync")[synced]
        self.assertEqual((body["workspace"], body["roblox"]), (BALL, ELLOB))
        # Aceeași identitate nu trezește nimic; una nouă da.
        before = len(self.hub.bodies("/hub/sync"))
        client.set_identity(dict(ELLOB), dict(BALL))
        time.sleep(0.1)
        self.assertEqual(len(self.hub.bodies("/hub/sync")), before)
        client.set_identity({"user_id": 0, "name": "Studio"}, None)
        self.assertTrue(wait_until(lambda: self.hub.device_workspace is None and self.hub.device_roblox is None, timeout=0.4))
        self.assertIsNone(client.workspace_key())

    def test_pending_device_reregisters_immediately_when_the_identity_changes(self):
        client = self.client(autostart=True, pending_interval=0.5)
        self.assertTrue(wait_until(lambda: client.status()["status"] == "pending"))
        self.bridge.identity_payload = None
        client.set_identity(ELLOB, BALL)
        self.assertTrue(wait_until(lambda: len(self.hub.registrations) == 2, timeout=0.4))
        self.assertEqual((self.hub.registrations[-1]["roblox"], self.hub.registrations[-1]["workspace"]), (ELLOB, BALL))
        self.hub.mode = "approved"
        client.wake()
        self.assertTrue(wait_until(lambda: client.status()["status"] == "approved", timeout=0.4))
        self.assertTrue(wait_until(lambda: client.status()["last_sync"] is not None, timeout=0.4))

    def test_identity_payload_is_cleaned(self):
        self.hub.mode = "approved"
        self.bridge.identity = {"roblox": {"user_id": 12345.0, "name": "  ellob  "}, "workspace": {**BALL, "extra": 1}}
        client = self.client()
        client.step()
        self.assertEqual(self.hub.registrations[-1]["roblox"], {"user_id": 12345, "name": "ellob"})
        self.assertEqual(self.hub.registrations[-1]["workspace"], BALL)
        client.set_identity({"user_id": 7, "name": ""}, {"key": ""})
        self.assertEqual((client.roblox, client.workspace), ({"user_id": 7, "name": "user_7"}, None))
        client.set_identity({"user_id": True, "name": "x"}, {"name": "fără cheie"})
        self.assertEqual((client.roblox, client.workspace), (None, None))


class ClaimTests(ClientBase):
    def test_claims_go_to_the_hub_with_the_job_workspace(self):
        client = self.approved_client()
        self.bridge.job_workspaces["job-a"] = ARENA["key"]
        added, found = client.claim("job-a", "ellob", ["Workspace.Map", "workspace.Map", "game.ReplicatedStorage.Cfg"], "harta")
        self.assertEqual((found, [display(claim.path) for claim in added]), ([], ["Workspace.Map", "ReplicatedStorage.Cfg"]))
        self.assertEqual(self.hub.bodies("/hub/claims/claim")[-1], {"job_id": "job-a", "paths": ["Workspace.Map", "ReplicatedStorage.Cfg"],
                                                                      "reason": "harta", "workspace": ARENA["key"]})
        self.assertEqual([display(claim.path) for claim in self.hub.table(ARENA["key"]).held_by("job-a")], ["Workspace.Map", "ReplicatedStorage.Cfg"])
        self.assertEqual(self.hub.table(BALL["key"]).snapshot(), [])
        self.assertTrue(client.covers("job-a", ("Workspace", "Map", "Zone3")))
        self.assertTrue(client.related_to("job-a", ("Workspace",)))
        self.assertFalse(client.covers("job-a", ("Workspace", "Arena")))
        # Un job fără workspace cunoscut folosește workspace-ul curent al daemon-ului; un `workspace` explicit câștigă.
        client.claim("job-b", "ellob", ["Lighting"])
        self.assertEqual(self.hub.bodies("/hub/claims/claim")[-1]["workspace"], BALL["key"])
        client.claim("job-c", "ellob", ["SoundService"], workspace="game:5")
        self.assertEqual(self.hub.bodies("/hub/claims/claim")[-1]["workspace"], "game:5")
        # Claims-urile joburilor din alte workspace-uri supraviețuiesc sync-ului (care aduce doar workspace-ul curent).
        client.step()
        self.assertEqual([display(claim.path) for claim in client.held_by("job-a")], ["Workspace.Map", "ReplicatedStorage.Cfg"])
        self.assertEqual([display(claim.path) for claim in client.held_by("job-b")], ["Lighting"])
        self.assertEqual([display(claim.path) for claim in client.held_by("job-c")], ["SoundService"])
        self.assertEqual([(row["path"], row["job_id"]) for row in client.snapshot()], [("Lighting", "job-b")])
        self.assertIsNone(client.holder(("Workspace", "Map"), exclude_job="job-b"))
        self.assertEqual(client.holder(("Workspace", "Map"), workspace=ARENA["key"]).job_id, "job-a")
        with self.assertRaises(ValueError):
            client.claim("job-a", "ellob", [])
        with self.assertRaises(ValueError):
            client.claim("job-a", "ellob", ["@ceva"])
        self.assertEqual(len(self.hub.bodies("/hub/claims/claim")), 3)

    def test_conflicts_release_touch_and_wait(self):
        client = self.approved_client()
        self.hub.table(BALL["key"]).claim("job-ana", "ana", ["Workspace.Map"], "harta")
        added, found = client.claim("job-a", "ellob", ["Workspace.Map.Zone3", "Lighting"])
        self.assertEqual((added, [(row["path"], row["holder"], row["developer"], row["held_path"]) for row in found]),
                         ([], [("Workspace.Map.Zone3", "job-ana", "ana", "Workspace.Map")]))
        self.assertEqual(client.held_by("job-a"), [])
        client.claim("job-a", "ellob", ["Lighting", "@play:studio-1"])
        self.assertEqual([display(claim.path) for claim in client.held_by("job-a")], ["Lighting", "@play:studio-1"])
        self.hub.now += 100
        client.touch("job-a")
        self.assertEqual(self.hub.bodies("/hub/claims/touch")[-1], {"job_id": "job-a", "workspace": BALL["key"]})
        self.assertEqual([claim.last_touch for claim in self.hub.table(BALL["key"]).held_by("job-a")], [1100.0, 1100.0])
        released = client.release("job-a", ["Lighting"])
        self.assertEqual(([display(claim.path) for claim in released], [display(claim.path) for claim in client.held_by("job-a")]),
                         (["Lighting"], ["@play:studio-1"]))
        self.assertEqual(self.hub.bodies("/hub/claims/release")[-1], {"job_id": "job-a", "paths": ["Lighting"], "workspace": BALL["key"]})
        self.assertEqual([display(claim.path) for claim in client.release("job-a")], ["@play:studio-1"])
        self.assertEqual((client.held_by("job-a"), client.release("job-a")), ([], []))
        # wait_free: liberă imediat, ținută până la timeout, anulată, apoi eliberată.
        self.assertTrue(client.wait_free(("Workspace", "Arena"), 1.0))
        self.assertEqual(self.hub.bodies("/hub/claims/wait")[-1]["workspace"], BALL["key"])
        self.assertLessEqual(self.hub.bodies("/hub/claims/wait")[-1]["timeout_seconds"], 1.0)
        self.assertFalse(client.wait_free(("Workspace", "Map", "Zone3"), 0.2))
        cancelled = threading.Event()
        cancelled.set()
        self.assertFalse(client.wait_free(("Workspace", "Map"), 5.0, cancelled))
        self.hub.table(BALL["key"]).release("job-ana")
        self.assertTrue(client.wait_free(("Workspace", "Map"), 1.0, workspace=BALL["key"]))
        self.assertFalse(client.wait_free(("Workspace", "Map"), 0.0))

    def test_claim_errors_do_not_change_the_state_but_outages_do(self):
        client = self.approved_client()
        self.bridge.identity["workspace"] = None
        client.step()
        with self.assertRaises(HubError) as error:
            client.claim("job-a", "ellob", ["Workspace.Map"])
        self.assertEqual((error.exception.status, str(error.exception)), (400, "Dispozitivul nu are un workspace curent."))
        self.assertEqual(client.status()["status"], "approved")
        with self.assertRaises(HubError) as error:
            client.fetch_workspace("game:404")
        self.assertEqual((error.exception.status, client.status()["status"]), (404, "approved"))
        self.hub.mode = "broken"
        with self.assertRaises(HubError) as error:
            client.touch("job-a")
        self.assertEqual((error.exception.status, client.status()["status"], client.status()["error"]), (503, "offline", HUB_UNREACHABLE))
        self.hub.mode = "approved"
        self.assertEqual(client.step(), 0.0)
        self.hub.mode = "revoked"
        with self.assertRaises(HubError):
            client.touch("job-a")
        self.assertEqual(client.status()["status"], "revoked")
        self.hub.mode = "approved"
        self.assertEqual(client.step(), 0.0)
        self.hub.member_mode = "unknown"
        with self.assertRaises(HubError):
            client.release("job-a")
        self.assertEqual(client.status()["status"], "connecting")
        self.hub.member_mode = None
        self.assertEqual((client.step(), client.status()["status"]), (0.0, "approved"))


class FetchTests(ClientBase):
    def test_fetch_workspaces_workspace_and_project(self):
        self.hub.workspaces = {BALL["key"]: dict(BALL), ARENA["key"]: dict(ARENA)}
        self.hub.projects[ARENA["key"]] = {"snapshot_id": "snap-arena", "count": 2, "groups": {"graphics": {"count": 2, "entries": []}}, "nodes": []}
        client = self.approved_client()
        client.step()
        response = client.fetch_workspaces()
        self.assertEqual((response["ok"], [row["key"] for row in response["workspaces"]]), (True, [ARENA["key"], BALL["key"]]))
        response = client.fetch_workspace(ARENA["key"])
        self.assertEqual((response["ok"], response["workspace"]["name"], response["members"]), (True, "Arena", []))
        self.assertEqual(self.hub.queries("/hub/workspace"), ["key=game%3A111"])
        with self.assertRaises(HubError) as error:
            client.fetch_workspace("game:404")
        self.assertEqual(error.exception.status, 404)
        with self.assertRaises(HubError) as error:
            client.fetch_workspace("")
        self.assertEqual(error.exception.status, 400)
        # Proiectul complet vine o singură dată per digest; fără proiect în hub → None, fără cache.
        project = client.fetch_project(ARENA["key"])
        self.assertEqual(project["snapshot_id"], "snap-arena")
        self.assertIs(client.fetch_project(ARENA["key"]), project)
        self.assertEqual(self.hub.paths().count("/hub/project"), 1)
        self.hub.projects[ARENA["key"]] = {"snapshot_id": "snap-arena-2", "count": 3, "groups": {}, "nodes": []}
        client.step()
        self.assertEqual(client.fetch_project(ARENA["key"])["snapshot_id"], "snap-arena-2")
        self.assertEqual(self.hub.paths().count("/hub/project"), 2)
        self.assertIsNone(client.fetch_project(BALL["key"]))
        with self.assertRaises(HubError) as error:
            client.fetch_project("game:404")
        self.assertEqual(error.exception.status, 404)
        self.assertEqual(client.fetch_project(ARENA["key"])["snapshot_id"], "snap-arena-2")
        self.assertEqual(self.hub.paths().count("/hub/project"), 4)


if __name__ == "__main__":
    unittest.main()

"""Teste pentru daemonul 1.0: joburi Mod S, proxy MCP cu claims, rute HTTP, identitate Roblox, workspace și hub-ul central.

Fără procese reale și fără rețea în afara loopback-ului: hub-ul este `FakeHub` (rutele `/hub/*` din contract §3.4 pe 127.0.0.1, port 0),
starea locală stă într-un director temporar (nu în %LOCALAPPDATA%), iar intervalele clientului hub sunt scurtate prin fabrică."""

import contextlib
import copy
import hashlib
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
# Slotul din .rbxmx pe care instalarea îl umple cu tokenul UI (restul fișierului rămâne neatins).
SLOT_OPEN = b'<string name="Name">LocalToken</string><string name="Value">'
sys.path.insert(0, str(ROOT / "scripts"))
import studio_bridge
import team_hub
import updater
from claims import ClaimTable, display, normalize
from local_state import DEFAULT_HUB_URL, DEVICE_TOKEN_PATTERN, device_id_for
from studio_bridge import Bridge, BridgeError, BridgeServer, Job
from team_client import HUB_OLD, HubClient, HubError, unavailable
from test_updater import LocalChannel, make_zip

NATIVE_TOOLS = ("inspect_instance", "execute_luau", "multi_edit", "generate_mesh", "start_stop_play",
                "insert_asset", "list_roblox_studios", "http_get")
# Tokenul UI al daemon-ului de test: respectă TOKEN_PATTERN (≥ 16 caractere), ca injectarea în .rbxmx să fie posibilă.
UI_TOKEN = "test-ui-token-0123456789"
DEVICE_TOKEN = hashlib.sha256(b"dispozitiv-de-test").hexdigest()
DEVICE_ID = device_id_for(DEVICE_TOKEN)
IDENTITY = {"user_id": 12345, "name": "ellob", "place_id": 1291603, "game_id": 987654, "place_name": "Ball", "creator_id": 555, "creator_type": "User"}
BALL = {"key": "game:987654", "game_id": 987654, "place_id": 1291603, "name": "Ball", "creator_id": 555, "creator_type": "User"}
TEST_TMP = Path(os.environ.get("HARNESS_TEST_TMP") or Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "studio-harness-1.0" / "daemon")


def temp_dir():
    TEST_TMP.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=str(TEST_TMP))


def wait_until(predicate, timeout=4.0, step=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


class FakeProviders:
    """Registry fals: raportează doar `available`, ca în 0.4, și răspunde fără inferență."""

    def __init__(self):
        self.run_count = 0
        self.available = True

    def status(self):
        return {name: {"available": self.available} for name in ("claude", "codex")}

    def run(self, **kwargs):
        self.run_count += 1
        kwargs["emit"]("text", "Răspuns de test, fără inferență.")
        return "native-test-session"


class FakeNative:
    def __init__(self, studios=None):
        self.calls = []
        self.closed = False
        self.studios = [{"id": "studio-1", "name": "Scenă de test"}] if studios is None else studios

    def list_studios(self):
        return list(self.studios)

    def verify_studio(self, studio_id):
        if not any(studio["id"] == studio_id for studio in self.studios):
            raise BridgeError("Instanță necunoscută.", 409)

    def list_tools(self):
        return [{"name": name, "description": "Test", "inputSchema": {
            "type": "object", "properties": {"studio_id": {"type": "string"}}, "required": ["studio_id"],
        }} for name in NATIVE_TOOLS]

    def call(self, name, arguments, studio_id):
        self.calls.append((name, copy.deepcopy(arguments), studio_id))
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    def close(self):
        self.closed = True


def post_in_two_parts(base, path, body, headers):
    """POST brut în doi pași (antetele, apoi corpul): întoarce (a răspuns înainte de corp?, status, JSON).

    Un răspuns trimis înainte de citirea corpului închide conexiunea cu date necitite; pe Windows clientul poate pierde JSON-ul."""
    address = urlsplit(base)
    data = json.dumps(body).encode()
    all_headers = {"Host": address.netloc, "Content-Type": "application/json", "Content-Length": str(len(data)), "Connection": "close"}
    all_headers.update(headers)
    request = f"POST {path} HTTP/1.1\r\n" + "".join(f"{name}: {value}\r\n" for name, value in all_headers.items()) + "\r\n"
    with socket.create_connection((address.hostname, address.port), timeout=3) as sock:
        sock.sendall(request.encode())
        sock.settimeout(0.3)
        try:
            early = sock.recv(65536)
        except socket.timeout:
            early = b""
        sock.settimeout(3)
        try:
            sock.sendall(data)
        except OSError:
            pass
        received = early
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            received += chunk
    head, _, payload = received.partition(b"\r\n\r\n")
    return bool(early), int(head.split(b" ")[1]), json.loads(payload)


def DEVICE_ID_OF(bridge):
    """`device_id`-ul derivat local din tokenul de dispozitiv al bridge-ului (sha256[:16]), fără să expună tokenul."""
    return device_id_for(bridge._device_token)


def make_bridge(temp, providers=None, native=None, token=UI_TOKEN, **extra):
    """Bridge cu stare locală în directorul temporar și fără hub (`hub_url=None`): nu atinge %LOCALAPPDATA% și nici rețeaua."""
    extra.setdefault("hub_url", None)
    return Bridge(providers or FakeProviders(), Path(temp) / "runtime", native or FakeNative(), token,
                  developer="tester", state_directory=Path(temp) / "state", **extra)


def fast_hub_client(bridge, hub_url, device_token, machine, **kwargs):
    """`HubClient` real cu intervale scurte: pending/backoff/sync la zeci de milisecunde, nu la secunde."""
    return HubClient(bridge, hub_url, device_token, machine, interval=0.05, idle_interval=0.05, pending_interval=0.05,
                     backoff_start=0.05, backoff_max=0.2, **kwargs)


class RecordingHubClient:
    """Client hub fals, fără rețea: reține argumentele primite de la Bridge și se poartă ca un client `disabled`/`connecting` fără fir."""

    def __init__(self, bridge, hub_url, device_token, machine, disabled_error=None):
        self.bridge = bridge
        self.hub_url = hub_url
        self.device_token = device_token
        self.machine = machine
        self.disabled_error = disabled_error
        self.state = "disabled" if hub_url is None else "connecting"
        self.error = (disabled_error or "Hub dezactivat (daemon pornit cu --no-hub).") if self.state == "disabled" else None
        self.device_id = device_id_for(device_token)
        self.identities = []
        self.recorded = []
        self.woken = 0
        self.stopped = False

    def status(self):
        return {"url": self.hub_url, "status": self.state, "hub_id": None, "device_id": self.device_id, "error": self.error,
                "last_sync": None, "enrollment": None}

    def set_identity(self, roblox, workspace):
        self.identities.append((copy.deepcopy(roblox), copy.deepcopy(workspace)))

    def wake(self):
        self.woken += 1

    def stop(self):
        self.stopped = True

    def record(self, entry):
        self.recorded.append(dict(entry))

    @staticmethod
    def member_rows():
        return []

    workspace_rows = remote_rows = member_rows

    @staticmethod
    def journal_rows(limit, workspace=None):
        return []

    @staticmethod
    def is_remote(job_id):
        return False

    @staticmethod
    def remote_snapshot(job_id, after):
        return None

    @staticmethod
    def held_by(job_id):
        return []

    @staticmethod
    def holder(path, exclude_job=None, *, workspace=None):
        return None

    @staticmethod
    def snapshot():
        return []

    def _unavailable(self, *_, **__):
        raise HubError(unavailable(self.state), 503, {"status": self.state})

    fetch_workspaces = fetch_workspace = fetch_project = claim = release = touch = wait_free = _unavailable


class FakeHub:
    """Hub fals pe loopback: rutele `/hub/*` din contract §3.4 cu stare în memorie, controlată din test.

    `mode` decide dispozitivul: `approved`, `pending` (202 la register, 403 în rest), `revoked` (403) sau `old` (404 pe orice `/hub/*`,
    ca un hub 0.8). Claims-urile stau într-un `ClaimTable` per workspace, ca daemon-ul să primească răspunsurile reale (held, conflicts)."""

    def __init__(self, mode="approved"):
        self.mode = mode
        self.hub_id = "f" * 32
        self.lock = threading.Lock()
        self.requests = []
        self.tokens = {}
        self.identities = {}
        self.tables = {}
        self.others = []
        self.events = {}
        self.extra_workspaces = []
        self.journal = []
        self.journal_seq = 0
        self.projects = {}
        self.want_project = False
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _serve(self, method):
                url = urlsplit(self.path)
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else {}
                try:
                    status, payload = hub.route(method, url.path, parse_qs(url.query), body, self.headers.get("X-Studio-Harness-Device"))
                except (KeyError, ValueError, TypeError) as error:
                    status, payload = 400, {"ok": False, "error": type(error).__name__ + ": " + str(error)}
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._serve("GET")

            def do_POST(self):
                self._serve("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def table(self, key):
        with self.lock:
            table = self.tables.get(key)
            if table is None:
                table = self.tables[key] = ClaimTable()
            return table

    def name_of(self, device_id):
        roblox, _ = self.identities.get(device_id, (None, None))
        return roblox["name"] if isinstance(roblox, dict) and roblox.get("name") else "pc"

    def current_key(self, device_id):
        _, meta = self.identities.get(device_id, (None, None))
        return meta["key"] if isinstance(meta, dict) else None

    def workspace_rows(self):
        rows = []
        seen = set()
        for device_id, (roblox, meta) in self.identities.items():
            if not isinstance(meta, dict) or meta["key"] in seen:
                continue
            seen.add(meta["key"])
            table = self.tables.get(meta["key"])
            rows.append({**meta, "last_seen": 1000.0,
                         "members_online": [{"device_id": device_id, "roblox_user_id": roblox["user_id"] if roblox else 0,
                                             "roblox_name": self.name_of(device_id), "machine": "pc", "last_seen": 1000.0}],
                         "sessions_active": 0, "claims": len(table.snapshot()) if table else 0, "project": None})
        return rows + [dict(row) for row in self.extra_workspaces]

    def route(self, method, path, query, body, token):
        with self.lock:
            self.requests.append((method, path, copy.deepcopy(body)))
            if self.mode == "old" or not path.startswith("/hub/"):
                return 404, {"ok": False, "error": "Rută inexistentă."}
            device_id = device_id_for(token) if isinstance(token, str) and DEVICE_TOKEN_PATTERN.fullmatch(token) else None
            if device_id is None:
                return 401, {"ok": False, "error": "Dispozitiv necunoscut; înregistrează-te.", "status": "unknown"}
            if path == "/hub/register":
                self.tokens[token] = device_id
                self.identities[device_id] = (body.get("roblox"), body.get("workspace"))
                base = {"ok": True, "device_id": device_id, "hub_id": self.hub_id, "enrollment": "approve"}
                if self.mode == "revoked":
                    return 403, {"ok": False, "status": "revoked", "device_id": device_id, "error": "Dispozitivul a fost revocat de admin."}
                if self.mode == "pending":
                    return 202, dict(base, status="pending")
                return 200, dict(base, status="approved")
            if token not in self.tokens:
                return 401, {"ok": False, "error": "Dispozitiv necunoscut; înregistrează-te.", "status": "unknown"}
            if self.mode != "approved":
                return 403, {"ok": False, "error": "Dispozitivul nu este aprobat.", "status": self.mode, "device_id": device_id}
            if path == "/hub/sync":
                return 200, self._sync(device_id, body)
            if path == "/hub/workspaces":
                return 200, {"ok": True, "workspaces": self.workspace_rows()}
            if path == "/hub/workspace":
                key = query.get("key", [None])[0]
                row = next((row for row in self.workspace_rows() if row["key"] == key), None)
                if row is None:
                    return 404, {"ok": False, "error": "Workspace-ul nu există."}
                return 200, {"ok": True, "workspace": row, "members": row["members_online"], "sessions": [], "claims": [], "journal": [], "project": None}
            if path == "/hub/project":
                return 200, {"ok": True, "project": copy.deepcopy(self.projects.get(query.get("key", [None])[0]))}
            if not path.startswith("/hub/claims/"):
                return 404, {"ok": False, "error": "Rută inexistentă."}
            key = body.get("workspace") or self.current_key(device_id)
            if key is None:
                return 400, {"ok": False, "error": "Dispozitivul nu are un workspace curent."}
            name = self.name_of(device_id)
        table = self.table(key)
        if path.endswith("/wait"):
            return 200, {"ok": True, "free": table.wait_free(normalize(body["path"]), min(float(body.get("timeout_seconds", 60)), 60.0))}
        job_id = body["job_id"]
        if path.endswith("/claim"):
            added, conflicts = table.claim(job_id, name, body["paths"], body.get("reason", ""))
            if conflicts:
                return 409, {"ok": False, "error": "Conflict de claim.", "conflicts": conflicts}
        elif path.endswith("/release"):
            released = [display(claim.path) for claim in table.release(job_id, body.get("paths"))]
            return 200, {"ok": True, "released": released, "held": [display(claim.path) for claim in table.held_by(job_id)]}
        elif path.endswith("/touch"):
            table.touch(job_id)
        else:
            return 404, {"ok": False, "error": "Rută inexistentă."}
        return 200, {"ok": True, "claimed": [display(claim.path) for claim in table.held_by(job_id)],
                     "held": [display(claim.path) for claim in table.held_by(job_id)]}

    def _sync(self, device_id, body):
        if "workspace" not in body:
            raise ValueError("Câmpul workspace lipsește.")
        roblox = body["roblox"] if "roblox" in body else self.identities.get(device_id, (None, None))[0]
        meta = body["workspace"]
        self.identities[device_id] = (roblox, meta)
        key = meta["key"] if isinstance(meta, dict) else None
        for item in body.get("journal", []):
            self.journal_seq += 1
            self.journal.append(dict(item, seq=self.journal_seq, time=item.get("time") or 1000.0, workspace=item.get("workspace") or key,
                                     device_id=device_id, developer=self.name_of(device_id)))
        after = body.get("journal_after", 0)
        for job_id in body.get("touch", []):
            for table in self.tables.values():
                table.touch(job_id)
        want_project = False
        if key is not None and isinstance(body.get("project"), dict):
            self.projects[key] = copy.deepcopy(body["project"])
        if key is not None and self.want_project and body.get("project_digest"):
            current = self.projects.get(key)
            want_project = current is None or current.get("snapshot_id") != body["project_digest"]
        table = self.tables.get(key) if key else None
        members = []
        if key is not None:
            members.append({"device_id": device_id, "roblox_user_id": roblox["user_id"] if roblox else 0, "roblox_name": self.name_of(device_id),
                            "machine": "pc", "last_seen": 1000.0, "online": True})
        others = [dict(row) for row in self.others if row.get("workspace") == key]
        events = {}
        for job_id, cursor in body.get("want", {}).items():
            if any(row["job_id"] == job_id for row in others):
                events[job_id] = [event for event in self.events.get(job_id, []) if event["seq"] > cursor]
        return {"ok": True, "now": 1000.0, "hub_id": self.hub_id, "device": {"status": "approved"}, "workspace": key,
                "members": members + [{"device_id": row["device_id"], "roblox_user_id": row.get("roblox_user_id", 0), "roblox_name": row["developer"],
                                       "machine": row.get("machine", "pc-" + row["developer"]), "last_seen": 1000.0, "online": True} for row in others],
                "sessions": others, "events": events,
                "claims": [dict(row, workspace=key) for row in table.snapshot()] if table else [],
                "journal": [row for row in self.journal if row["seq"] > after and row["workspace"] == key][-50:],
                "journal_seq": self.journal_seq, "want_project": want_project, "workspaces": self.workspace_rows()}


def other_session(job_id, developer="ana", workspace="game:987654", **extra):
    row = {"job_id": job_id, "kind": "terminal", "provider": "codex", "developer": developer, "name": "Codex · " + developer, "state": "running",
           "studio_id": None, "cwd": None, "started": 900.0, "last_activity": 900.0, "pending_approval": False, "claims": [],
           "workspace": workspace, "device_id": "d" * 16, "roblox_user_id": 777, "machine": "pc-" + developer, "remote": True}
    row.update(extra)
    return row


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = temp_dir()
        self.native = FakeNative()
        self.bridge = make_bridge(self.temp.name, native=self.native)

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def job(self, suffix="1"):
        job = Job(suffix * 32, "claude", "studio-1", "session-1", "test", {}, developer="tester")
        self.bridge.jobs[job.id] = job
        self.bridge.sessions.setdefault(job.session_id, {"provider": job.provider, "studio_id": job.studio_id, "native_id": None})
        if self.bridge.active_job_id is None:
            self.bridge.active_job_id = job.id
        return job

    def claim(self, job, *paths):
        added, conflicts = self.bridge.claims.claim(job.id, job.developer, list(paths))
        self.assertEqual(conflicts, [])
        return added

    def luau(self, job, code, scope=("Workspace.Map.Zone3",)):
        return self.bridge.agent_call(job, "execute_luau", {"code": code, "scope": list(scope)})

    def test_read_only_tool_is_immediate_and_pinned(self):
        result = self.bridge.agent_call(self.job(), "inspect_instance", {"path": "Workspace"})
        self.assertFalse(result["isError"])
        self.assertEqual(self.native.calls[0][1]["studio_id"], "studio-1")
        self.assertEqual(self.native.calls[0][1]["datamodel_type"], "Edit")

    def test_mutation_waits_for_explicit_approval(self):
        job = self.job()
        self.claim(job, "Workspace.Map.Zone3")
        results = []
        thread = threading.Thread(target=lambda: results.append(self.luau(job, "return 1")))
        thread.start()
        with job.condition:
            self.assertTrue(job.condition.wait_for(lambda: job.pending is not None, timeout=2))
            approval_id = job.pending["id"]
        self.assertEqual(self.native.calls, [])
        job.approve(approval_id, True)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(results[0]["isError"])
        self.assertEqual(len(self.native.calls), 1)
        # scope este un parametru al daemon-ului, nu al serverului oficial.
        self.assertNotIn("scope", self.native.calls[0][1])

    def test_refusal_never_executes_mutation(self):
        job = self.job()
        self.claim(job, "Workspace.Map.Zone3")
        results = []
        thread = threading.Thread(target=lambda: results.append(self.luau(job, "print(1)")))
        thread.start()
        with job.condition:
            self.assertTrue(job.condition.wait_for(lambda: job.pending is not None, timeout=2))
            approval_id = job.pending["id"]
        job.approve(approval_id, False)
        thread.join(2)
        self.assertTrue(results[0]["isError"])
        self.assertEqual(self.native.calls, [])

    def test_finished_studio_session_is_reported_closed_to_the_hub_but_leaves_the_board(self):
        """Hub-ul ține sesiunea deschisă până i se raportează starea terminală (contract §3.4: doar cele închise se reciclează)."""
        job = self.job()
        self.assertEqual([row["job_id"] for row in self.bridge.hub_payload({})], [job.id])
        self.bridge._finish(job, "completed", "gata")
        # Tabla nu mai arată sesiunea Studio încheiată (ea rămâne doar pentru ferestrele de terminal)…
        self.assertEqual(self.bridge._session_rows(), [])
        # …dar corpul sync-ului o poartă mai departe, cu starea finală și evenimentul `done`, ca hub-ul să o poată închide.
        rows = self.bridge.hub_payload({})
        self.assertEqual([(row["job_id"], row["state"]) for row in rows], [(job.id, "completed")])
        self.assertEqual(rows[0]["events"][-1]["type"], "done")
        # După fereastra de raportare dispare și din sync: hub-ul are deja starea finală.
        job.closed_at -= studio_bridge.HUB_CLOSE_RETENTION + 1
        self.assertEqual(self.bridge.hub_payload({}), [])

    def test_cancel_wakes_pending_approval(self):
        job = self.job()
        self.claim(job, "Workspace.Map.Zone3")
        results = []
        thread = threading.Thread(target=lambda: results.append(self.luau(job, "return 2")))
        thread.start()
        with job.condition:
            self.assertTrue(job.condition.wait_for(lambda: job.pending is not None, timeout=2))
        job.cancel()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(results[0]["isError"])
        self.assertEqual(job.state, "cancelled")
        self.assertEqual(self.native.calls, [])

    def test_mutation_without_claim_is_refused_before_approval(self):
        job = self.job()
        result = self.luau(job, "return 1")
        self.assertTrue(result["isError"])
        self.assertIn("Workspace.Map.Zone3", result["content"][0]["text"])
        self.assertIsNone(job.pending)
        self.assertEqual(self.native.calls, [])

    def test_wrong_studio_rejected_before_any_approval(self):
        job = self.job()
        self.claim(job, "Workspace.Map.Zone3")
        with self.assertRaises(BridgeError):
            self.bridge.agent_call(job, "execute_luau", {"studio_id": "other", "code": "return 1", "scope": ["Workspace.Map.Zone3"]})
        self.assertIsNone(job.pending)
        self.assertEqual(self.native.calls, [])

    def test_unknown_tool_is_rejected(self):
        with self.assertRaises(BridgeError):
            self.bridge.agent_call(self.job(), "shell", {})

    def test_agent_schema_hides_target_and_marks_execution_mutating(self):
        tools = {tool["name"]: tool for tool in self.bridge.agent_tools(self.job())}
        self.assertNotIn("studio_id", tools["execute_luau"]["inputSchema"]["properties"])
        self.assertNotIn("studio_id", tools["execute_luau"]["inputSchema"]["required"])
        self.assertFalse(tools["execute_luau"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["inspect_instance"]["annotations"]["readOnlyHint"])
        self.assertIn("studio_id", self.native.list_tools()[0]["inputSchema"]["properties"])
        # 0.4: execute_luau cere scope, iar toolurile de coordonare sunt servite de daemon.
        self.assertIn("scope", tools["execute_luau"]["inputSchema"]["properties"])
        self.assertIn("scope", tools["execute_luau"]["inputSchema"]["required"])
        for name in ("hub_board", "hub_claim", "hub_release", "hub_wait", "hub_project"):
            self.assertIn(name, tools)
        self.assertTrue(tools["hub_board"]["annotations"]["readOnlyHint"])
        self.assertNotIn("subagent", tools)

    def test_scoped_tokens_cannot_authorize_other_jobs(self):
        first = self.job("1")
        second = self.job("2")
        with self.assertRaises(BridgeError):
            self.bridge.authenticate_job(second.id, first.token)
        self.assertIs(self.bridge.authenticate_job(first.id, first.token), first)

    def test_terminal_job_token_expires(self):
        job = self.job()
        job.finish("completed")
        with self.assertRaises(BridgeError):
            self.bridge.authenticate_job(job.id, job.token)

    def test_inactive_approval_is_rejected(self):
        with self.assertRaises(BridgeError):
            self.job().approve("not-pending", True)

    def test_event_cursor(self):
        job = self.job()
        job.emit("text", "primul")
        job.emit("text", "al doilea")
        result = job.snapshot(1)
        self.assertEqual([event["text"] for event in result["events"]], ["al doilea"])
        self.assertEqual(result["last_seq"], 2)

    def test_large_text_is_split_without_loss(self):
        job = self.job()
        text = "ș" * 70000
        job.emit("text", text)
        self.assertEqual("".join(event["text"] for event in job.events), text)
        # Fiecare bucată trebuie să încapă serializată în plafonul hub-ului: altfel colegii primesc nota „…a fost omis”
        # în locul textului, la fiecare bucată de dimensiune maximă.
        for event in job.events:
            self.assertLessEqual(len(json.dumps(event, ensure_ascii=False)), team_hub.MAX_EVENT_BYTES)

    def test_expired_approval_has_explicit_resolution_event(self):
        job = self.job()
        self.assertFalse(job.request_approval("execute_luau", {"code": "return 1"}, timeout=0))
        approval = next(event for event in job.events if event["type"] == "approval")
        resolved = job.events[-1]
        self.assertEqual(resolved["approval_id"], approval["approval_id"])
        self.assertIs(resolved["approval_active"], False)
        self.assertIsNone(job.pending)

    def test_old_event_gap_is_reported(self):
        job = self.job()
        for index in range(1025):
            job.emit("text", str(index))
        snapshot = job.snapshot(0)
        self.assertIn("incomplet", snapshot["events"][0]["text"])
        self.assertEqual(snapshot["events"][-1]["text"], "1024")

    def test_unavailable_cli_is_rejected_without_starting_a_job(self):
        # 0.4: nu mai există verificarea contului; singura condiție la enqueue este `available`.
        self.bridge.providers.available = False
        with self.assertRaisesRegex(BridgeError, "CLI-ul oficial"):
            self.bridge.start_chat({"provider": "claude", "prompt": "Salut", "studio_id": "studio-1"})
        self.assertEqual(self.bridge.jobs, {})

    def test_nested_native_agents_are_not_exposed(self):
        with self.assertRaisesRegex(BridgeError, "altor agenți"):
            self.bridge.agent_call(self.job(), "subagent", {})

    def test_chat_with_fake_provider_finishes_and_preserves_native_session(self):
        job = self.bridge.start_chat({"provider": "claude", "prompt": "Salut", "studio_id": "studio-1"})
        with job.condition:
            self.assertTrue(job.condition.wait_for(lambda: job.state == "completed", timeout=3))
        self.assertEqual(self.bridge.sessions[job.session_id]["native_id"], "native-test-session")
        self.assertEqual(self.native.calls, [])
        self.assertTrue(any(event["type"] == "text" for event in job.events))
        self.assertEqual(job.kind, "studio")
        self.assertEqual(job.developer, "tester")
        # Fără identitate încă, jobul nu are workspace; îl primește la primul /v1/identity.
        self.assertIsNone(job.workspace)

    def test_second_unfinished_job_in_same_session_is_rejected(self):
        job = self.job()
        with self.assertRaisesRegex(BridgeError, "deja"):
            self.bridge.start_chat({"provider": "claude", "prompt": "Salut", "studio_id": "studio-1", "session_id": job.session_id})

    def test_resume_cannot_change_provider(self):
        self.bridge.sessions["old"] = {"provider": "claude", "studio_id": "studio-1", "native_id": "saved"}
        with self.assertRaisesRegex(BridgeError, "nu corespunde"):
            self.bridge.start_chat({"provider": "codex", "prompt": "Salut", "studio_id": "studio-1", "session_id": "old"})


class BridgeServerConnectionTests(unittest.TestCase):
    """Pluginul închis din Studio sau un CLI oprit rup conexiunea: daemon.log nu trebuie să se umple de stive."""

    def test_broken_connections_do_not_print_a_traceback(self):
        server = object.__new__(BridgeServer)

        def report(error):
            stream = io.StringIO()
            try:
                raise error
            except type(error):
                with contextlib.redirect_stderr(stream):
                    BridgeServer.handle_error(server, None, ("127.0.0.1", 1))
            return stream.getvalue()

        for error in (ConnectionResetError("ruptă"), BrokenPipeError("ruptă"), ConnectionAbortedError("ruptă"), TimeoutError("expirat")):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(report(error), "")
        # Orice altceva rămâne vizibil: tăcerea este doar pentru conexiunile rupte de client.
        self.assertIn("ValueError", report(ValueError("altceva")))


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = temp_dir()
        self.opened = []
        self.bridge = make_bridge(self.temp.name, open_url=self.opened.append)
        self.server = BridgeServer(0, self.bridge)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.base = self.bridge.base_url

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.bridge.close()
        self.temp.cleanup()

    def request(self, path, body=None, headers=None, token=UI_TOKEN):
        all_headers = {"Content-Type": "application/json"}
        if token is not None:
            all_headers["X-Studio-Harness-Token"] = token
        all_headers.update(headers or {})
        request = Request(self.base + path, data=None if body is None else json.dumps(body).encode(), headers=all_headers)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read()), response.headers
        except HTTPError as error:
            return error.code, json.loads(error.read()), error.headers

    def test_status_does_not_expose_tokens(self):
        status, body, headers = self.request("/v1/status")
        self.assertEqual(status, 200)
        self.assertNotIn(UI_TOKEN, json.dumps(body))
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(headers["Server"], "StudioHarness/1.0")

    def test_missing_pairing_token_is_rejected(self):
        self.assertEqual(self.request("/v1/status", token=None)[0], 401)

    def test_origin_is_rejected_even_with_token(self):
        self.assertEqual(self.request("/v1/status", headers={"Origin": "https://example.com"})[0], 403)

    def test_wrong_host_is_rejected(self):
        self.assertEqual(self.request("/v1/status", headers={"Host": "example.com"})[0], 403)

    def test_agent_token_cannot_use_ui_routes(self):
        job = Job("1" * 32, "claude", "studio-1", "session-1", "test", {})
        self.bridge.jobs[job.id] = job
        for path, body in (("/v1/default-studio", {"studio_id": "studio-1"}), ("/v1/board", None), ("/v1/chat", {"provider": "claude", "prompt": "x", "studio_id": "studio-1"}),
                           ("/v1/identity", IDENTITY), ("/v1/panel/open", {}), ("/v1/hub/workspaces", None)):
            with self.subTest(path=path):
                status, _, _ = self.request(path, body, {"X-Studio-Harness-Job": job.token}, token=None)
                self.assertEqual(status, 401)
        self.assertIsNone(self.bridge.default_studio_id)
        self.assertIsNone(self.bridge.identity)
        self.assertEqual(len(self.bridge.jobs), 1)

    def test_agent_tools_require_scoped_token(self):
        job = Job("1" * 32, "claude", "studio-1", "session-1", "test", {})
        self.bridge.jobs[job.id] = job
        self.bridge.active_job_id = job.id
        self.assertEqual(self.request(f"/agent/{job.id}/tools")[0], 401)
        status, body, _ = self.request(f"/agent/{job.id}/tools", headers={"X-Studio-Harness-Job": job.token}, token=None)
        self.assertEqual(status, 200)
        self.assertIn("tools", body)

    def test_plain_text_post_is_rejected(self):
        status, _, _ = self.request("/v1/jobs/poll", {"jobs": []}, {"Content-Type": "text/plain"})
        self.assertEqual(status, 415)

    def test_unknown_path_does_not_fall_back_to_filesystem(self):
        status, _, _ = self.request("/../../.claude/.credentials.json")
        self.assertEqual(status, 404)

    def test_early_refusals_read_the_request_body_before_answering(self):
        # Refuzurile date înainte de `_body()` (401/403/404/415) consumă totuși corpul: altfel răspunsul JSON se poate pierde pe Windows.
        ui = {"X-Studio-Harness-Token": UI_TOKEN}
        for path, headers, expected in (("/v1/jobs/absent/release", ui, 404), ("/v1/jobs/poll", {"X-Studio-Harness-Token": "wrong"}, 401),
                                        ("/v1/jobs/poll", {**ui, "Origin": "https://example.com"}, 403), ("/v1/nothing", ui, 404),
                                        ("/v1/jobs/poll", {**ui, "Content-Type": "text/plain"}, 415),
                                        ("/agent/absent/call", {"X-Studio-Harness-Job": "x"}, 404)):
            with self.subTest(path=path, headers=headers):
                early, status, body = post_in_two_parts(self.base, path, {"paths": ["Lighting"]}, headers)
                self.assertFalse(early)
                self.assertEqual((status, body["ok"]), (expected, False))

    def test_cancel_accepts_lua_empty_table_encoding(self):
        job = Job("1" * 32, "claude", "studio-1", "session-1", "test", {})
        self.bridge.jobs[job.id] = job
        self.assertEqual(self.request(f"/v1/jobs/{job.id}/cancel", [])[0], 200)
        self.assertEqual(job.state, "cancelled")

    def test_unknown_provider_is_rejected(self):
        self.assertEqual(self.request("/v1/chat", {"provider": "other", "prompt": "Salut", "studio_id": "studio-1"})[0], 400)
        self.assertEqual(self.request("/v1/terminal/sessions", {"provider": "other"})[0], 400)
        self.assertEqual(self.bridge.jobs, {})

    def test_provider_login_and_check_routes_are_gone(self):
        # 0.4: autentificarea nu mai este gestionată de plugin.
        for path in ("/v1/provider/check", "/v1/provider/login"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path, {"provider": "claude"})[0], 404)

    def test_status_advertises_version_features_hub_and_startup_identity(self):
        status, body, _ = self.request("/v1/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["version"], "1.0.0")
        self.assertEqual(body["bridge_id"], self.bridge.bridge_id)
        # 0.6: starea actualizării este publică (fără URL-uri de descărcare); la pornire nu s-a verificat nimic.
        self.assertEqual(body["update"], {"current": "1.0.0", "available": None, "state": "idle", "message": "",
                                          "checked": None, "restart_required": False})
        self.assertEqual(body["features"], {"queued_sessions": True, "batch_events": True, "terminal_sessions": True, "claims": True,
                                            "board": True, "project": True, "plugin_bundle": True, "identity": True, "workspaces": True,
                                            "panel": True, "settings": True})
        # Implicit, fiecare operație a agentului se aprobă de om; setarea se schimbă din plugin și se ține în config.json.
        self.assertEqual(body["settings"], {"auto_approve": "ask"})
        self.assertNotIn("team", body)
        # 1.0: fără hub (--no-hub) daemon-ul este `disabled`, fără panou, fără identitate până la primul /v1/identity.
        self.assertEqual(body["hub"], {"url": None, "status": "disabled", "hub_id": None, "device_id": DEVICE_ID_OF(self.bridge), "error": "Hub dezactivat (daemon pornit cu --no-hub).",
                                       "last_sync": None, "enrollment": None})
        self.assertIsNone(body["panel_url"])
        self.assertIsNone(body["identity"])
        self.assertIsNone(body["workspace"])
        self.assertEqual((body["members"], body["workspaces"]), ([], []))
        self.assertEqual(body["providers"], {"claude": {"available": True}, "codex": {"available": True}})
        self.assertEqual(body["queued_count"], 0)
        self.assertIsNone(body["active_job_id"])
        self.assertIsNone(body["default_studio_id"])
        # status este citit și de shim-ul din terminal, deci nu marchează pluginul ca fiind conectat.
        self.assertFalse(body["plugin_connected"])
        self.assertEqual((body["developer"], body["machine"]), ("tester", self.bridge.machine))
        self.assertEqual(self.bridge.providers.run_count, 0)
        self.assertEqual(self.request("/v1/board")[0], 200)
        self.assertTrue(self.request("/v1/status")[1]["plugin_connected"])

    def test_two_studio_windows_keep_their_own_project(self):
        """1.0: două ferestre Studio deschise nu se mai suprascriu — fiecare primește în /v1/status jocul ei."""
        kart = {"user_id": 777, "name": "ana", "place_id": 5550001, "game_id": 999002, "place_name": "Kart",
                "creator_id": 42, "creator_type": "User", "instance_id": "fereastra-kart"}
        self.assertEqual(self.request("/v1/identity", dict(IDENTITY, instance_id="fereastra-ball"))[1]["instance_id"], "fereastra-ball")
        self.assertEqual(self.request("/v1/identity", kart)[1]["instance_id"], "fereastra-kart")
        # Fiecare fereastră întreabă cu id-ul ei și primește propriul joc, indiferent care a raportat ultima.
        ball_view = self.request("/v1/status?instance=fereastra-ball")[1]
        kart_view = self.request("/v1/status?instance=fereastra-kart")[1]
        self.assertEqual((ball_view["workspace"], ball_view["identity"]["name"]), (BALL, "ellob"))
        self.assertEqual((kart_view["workspace"]["key"], kart_view["identity"]["name"]), ("game:999002", "ana"))
        # Ambele ferestre apar în listă, cea mai recentă prima; fără id rămâne ultima raportare (daemon vechi, plugin vechi).
        self.assertEqual([row["instance_id"] for row in ball_view["instances"]], ["fereastra-kart", "fereastra-ball"])
        self.assertEqual(self.request("/v1/status")[1]["workspace"]["key"], "game:999002")
        # Tabla urmează aceeași regulă.
        self.assertEqual(self.request("/v1/board?instance=fereastra-ball")[1]["workspace"], BALL)
        # Un instance_id gol sau prea lung este refuzat, ca să nu intre gunoi în lista ferestrelor.
        for bad in ("", "   ", "x" * 65, 7, []):
            with self.subTest(bad=bad):
                self.assertEqual(self.request("/v1/identity", dict(IDENTITY, instance_id=bad))[0], 400)

    def test_identity_route_marks_the_plugin_connected_and_fills_status(self):
        self.assertFalse(self.request("/v1/status")[1]["plugin_connected"])
        status, body, _ = self.request("/v1/identity", IDENTITY)
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "workspace": BALL,
                                "identity": {"user_id": 12345, "name": "ellob", "avatar": "rbxthumb://type=AvatarHeadShot&id=12345&w=48&h=48"},
                                "instance_id": "legacy", "studio_id": None})
        summary = self.request("/v1/status")[1]
        self.assertTrue(summary["plugin_connected"])
        self.assertEqual((summary["identity"]["name"], summary["workspace"], summary["developer"]), ("ellob", BALL, "ellob"))
        self.assertEqual(self.request("/v1/identity", {"user_id": "x"})[0], 400)
        chat = self.request("/v1/chat", {"provider": "claude", "prompt": "Salut", "studio_id": "studio-1"})[1]
        self.assertEqual(chat["workspace"], "game:987654")
        terminal = self.request("/v1/terminal/sessions", {"provider": "codex"})[1]
        self.assertEqual((terminal["workspace"], terminal["developer"]), ("game:987654", "ellob"))

    def test_panel_and_hub_proxy_routes_without_hub(self):
        status, body, _ = self.request("/v1/hub/workspaces")
        self.assertEqual((status, body), (503, {"ok": False, "error": "Hub-ul nu este disponibil (stare: disabled)."}))
        self.assertEqual(self.request("/v1/hub/workspace?key=game:1")[0], 503)
        self.assertEqual(self.request("/v1/hub/workspace")[0], 400)
        # Rutele proxy nu marchează pluginul ca fiind conectat; deschiderea panoului (chiar refuzată) da.
        self.assertFalse(self.request("/v1/status")[1]["plugin_connected"])
        status, body, _ = self.request("/v1/panel/open", {})
        self.assertEqual((status, body), (409, {"ok": False, "error": "Hub-ul este dezactivat pe acest PC."}))
        self.assertEqual(self.opened, [])
        self.assertTrue(self.request("/v1/status")[1]["plugin_connected"])

    def test_batch_poll_keeps_each_cursor_and_missing_job_isolated(self):
        first = Job("1" * 32, "claude", "studio-1", "session-1", "test", {})
        second = Job("2" * 32, "codex", "studio-1", "session-2", "test", {}, state="queued")
        first.emit("text", "primul-1")
        first.emit("text", "primul-2")
        second.emit("text", "al-doilea-1")
        self.bridge.jobs.update({first.id: first, second.id: second})
        status, body, _ = self.request("/v1/jobs/poll", {"jobs": [
            {"job_id": first.id, "after": 1}, {"job_id": "absent", "after": 0}, {"job_id": second.id, "after": 0},
        ]})
        self.assertEqual(status, 200)
        self.assertEqual(body["bridge_id"], self.bridge.bridge_id)
        self.assertEqual([event["text"] for event in body["jobs"][0]["events"]], ["primul-2"])
        self.assertEqual(body["jobs"][1], {"ok": False, "job_id": "absent", "status": 404, "error": "Cererea nu există."})
        self.assertEqual([event["text"] for event in body["jobs"][2]["events"]], ["al-doilea-1"])
        self.assertEqual(body["jobs"][2]["state"], "queued")
        self.assertEqual(self.bridge.providers.run_count, 0)

    def test_batch_poll_rejects_invalid_shape_and_limits(self):
        for body in ({"jobs": [{"job_id": "x", "after": 0}] * 17}, {"jobs": "invalid"},
                     {"jobs": [{"job_id": "x", "after": -1}]}, {"jobs": [{"job_id": "x", "after": True}]}):
            with self.subTest(body=body):
                self.assertEqual(self.request("/v1/jobs/poll", body)[0], 400)
        self.assertEqual(self.request("/v1/jobs/poll", {"jobs": []})[1]["jobs"], [])

    def test_job_token_cannot_poll_other_windows(self):
        self.assertEqual(self.request("/v1/jobs/poll", {"jobs": []}, {"X-Studio-Harness-Job": "job-token"}, token=None)[0], 401)

    def test_queued_proxy_token_is_not_active(self):
        job = Job("1" * 32, "claude", "studio-1", "session-1", "test", {}, state="queued")
        self.bridge.jobs[job.id] = job
        status, _, _ = self.request(f"/agent/{job.id}/tools", headers={"X-Studio-Harness-Job": job.token}, token=None)
        self.assertEqual(status, 409)
        self.assertEqual(self.bridge.native.calls, [])

    def test_expired_request_details_return_http_410_without_reexecution(self):
        first_payload = {"provider": "claude", "prompt": "first", "studio_id": "studio-1", "client_request_id": "first-request"}
        with patch.object(studio_bridge, "MAX_JOB_HISTORY", 1):
            _, first, _ = self.request("/v1/chat", first_payload)
            job = self.bridge.get_job(first["job_id"])
            with job.condition:
                self.assertTrue(job.condition.wait_for(lambda: job.state == "completed", timeout=2))
            _, second, _ = self.request("/v1/chat", {**first_payload, "prompt": "second", "client_request_id": "second-request"})
            job = self.bridge.get_job(second["job_id"])
            with job.condition:
                self.assertTrue(job.condition.wait_for(lambda: job.state == "completed", timeout=2))
            status, body, _ = self.request("/v1/chat", first_payload)
            self.assertEqual(status, 410)
            self.assertEqual(body["error"], "Cererea a fost deja tratată; detaliile au expirat. Nu a fost reexecutată.")
            self.assertEqual(self.bridge.providers.run_count, 2)

    def test_full_receipt_table_rejects_new_http_requests_only(self):
        payload = {"provider": "claude", "prompt": "same", "studio_id": "studio-1", "client_request_id": "same-request"}
        with patch.object(studio_bridge, "MAX_REQUEST_RECEIPTS", 1):
            _, first, _ = self.request("/v1/chat", payload)
            status, _, _ = self.request("/v1/chat", {**payload, "client_request_id": "new-request"})
            self.assertEqual(status, 429)
            status, retry, _ = self.request("/v1/chat", payload)
            self.assertEqual(status, 200)
            self.assertEqual(first["job_id"], retry["job_id"])
            job = self.bridge.get_job(first["job_id"])
            with job.condition:
                self.assertTrue(job.condition.wait_for(lambda: job.state == "completed", timeout=2))
            self.assertEqual(self.bridge.providers.run_count, 1)

    def test_post_chat_ack_and_idempotency_are_compatible(self):
        payload = {"provider": "claude", "prompt": "Salut", "studio_id": "studio-1", "client_request_id": "request-1"}
        status, first, _ = self.request("/v1/chat", payload)
        self.assertEqual(status, 200)
        self.assertIn(first["state"], ("queued", "running"))
        job = self.bridge.get_job(first["job_id"])
        with job.condition:
            self.assertTrue(job.condition.wait_for(lambda: job.state == "completed", timeout=2))
        status, retried, _ = self.request("/v1/chat", payload)
        self.assertEqual(status, 200)
        self.assertEqual(retried["job_id"], first["job_id"])
        self.assertEqual(retried["session_id"], first["session_id"])
        self.assertEqual(self.bridge.providers.run_count, 1)
        self.assertEqual(self.request("/v1/chat", {**payload, "prompt": "Alt mesaj"})[0], 409)
        self.assertEqual(self.request(f"/v1/jobs/{job.id}/events?after=0")[0], 200)


class HubSetupTests(unittest.TestCase):
    """1.0: hub_url din env → config.json → DEFAULT_HUB_URL, --no-hub, tokenul de dispozitiv, team.json ignorat; fără rețea (client fals)."""

    def setUp(self):
        self.temp = temp_dir()
        self.bridges = []
        self.environ = patch.dict(os.environ)
        self.environ.start()
        os.environ.pop("STUDIO_HARNESS_HUB_URL", None)

    def tearDown(self):
        self.environ.stop()
        for bridge in self.bridges:
            bridge.close()
        self.temp.cleanup()

    def bridge(self, state=None, logger=None, **extra):
        base = Path(self.temp.name) / str(len(self.bridges))
        bridge = Bridge(FakeProviders(), base / "runtime", FakeNative(), UI_TOKEN, developer="tester", state_directory=state or base / "state",
                        hub_client_factory=RecordingHubClient, logger=logger, **extra)
        self.bridges.append(bridge)
        return bridge

    def test_hub_url_comes_from_env_then_config_then_default(self):
        self.assertEqual(studio_bridge.DEFAULT_HUB_URL, "https://lostcube.pro")
        bridge = self.bridge()
        self.assertEqual((bridge.hub.hub_url, bridge.hub_source, bridge.hub.disabled_error), (DEFAULT_HUB_URL, "default", None))
        self.assertEqual((bridge.status()["hub"]["status"], bridge.status()["hub"]["url"]), ("connecting", DEFAULT_HUB_URL))
        self.assertEqual(bridge.status()["panel_url"], DEFAULT_HUB_URL + "/panel")
        state = Path(self.temp.name) / "configured"
        state.mkdir()
        (state / "config.json").write_text(json.dumps({"hub_url": "http://127.0.0.1:1/"}), encoding="utf-8")
        bridge = self.bridge(state=state)
        self.assertEqual((bridge.hub.hub_url, bridge.hub_source), ("http://127.0.0.1:1", "config"))
        with patch.dict(os.environ, {"STUDIO_HARNESS_HUB_URL": "https://hub.example/roblox/harness/"}):
            bridge = self.bridge(state=state)
        self.assertEqual((bridge.hub.hub_url, bridge.hub_source), ("https://hub.example/roblox/harness", "env"))
        # O valoare setată dar neacceptată nu cade pe hub-ul public: daemon-ul rămâne `disabled` cu un mesaj despre sursă.
        (state / "config.json").write_text(json.dumps({"hub_url": "ftp://hub"}), encoding="utf-8")
        bridge = self.bridge(state=state)
        self.assertEqual((bridge.hub.hub_url, bridge.hub_source, bridge.hub.disabled_error), (None, "config", "hub_url invalid (sursa: config)"))
        self.assertEqual((bridge.status()["hub"]["status"], bridge.status()["hub"]["error"], bridge.status()["panel_url"]),
                         ("disabled", "hub_url invalid (sursa: config)", None))
        with self.assertRaises(BridgeError) as error:
            bridge.panel_open()
        self.assertEqual(error.exception.status, 409)
        # --no-hub: fără adresă și fără mesaj de eroare propriu (clientul îl pune pe al lui).
        bridge = self.bridge(hub_url=None)
        self.assertEqual((bridge.hub.hub_url, bridge.hub_source, bridge.hub.disabled_error), (None, "no-hub", None))
        bridge = self.bridge(hub_url="http://127.0.0.1:2")
        self.assertEqual((bridge.hub.hub_url, bridge.hub_source), ("http://127.0.0.1:2", "explicit"))
        bridge.close()
        self.assertTrue(bridge.hub.stopped)

    def test_device_token_is_created_once_and_never_exposed(self):
        bridge = self.bridge()
        path = bridge.state_directory / "device-token"
        token = path.read_text(encoding="utf-8").strip()
        self.assertTrue(DEVICE_TOKEN_PATTERN.fullmatch(token))
        self.assertEqual((bridge.hub.device_token, bridge.hub.machine), (token, bridge.machine))
        self.assertEqual(bridge.status()["hub"]["device_id"], device_id_for(token))
        self.assertNotIn(token, json.dumps(bridge.status()))
        self.assertNotIn(token, json.dumps(bridge.board()))
        # Același director → același token; un token explicit (teste) nu creează fișierul.
        self.assertEqual(self.bridge(state=bridge.state_directory).hub.device_token, token)
        explicit = self.bridge(device_token=DEVICE_TOKEN)
        self.assertEqual(explicit.hub.device_token, DEVICE_TOKEN)
        self.assertFalse((explicit.state_directory / "device-token").exists())

    def test_team_json_is_ignored_with_a_single_log_line(self):
        state = Path(self.temp.name) / "vechi"
        state.mkdir()
        (state / "team.json").write_text(json.dumps({"developer": "eva", "team_token": "x" * 20, "hub_url": "http://127.0.0.1:9"}), encoding="utf-8")
        lines = []
        bridge = self.bridge(state=state, logger=lines.append)
        self.assertEqual(len(lines), 1)
        self.assertIn("team.json", lines[0])
        self.assertNotIn("x" * 20, lines[0])
        # Nimic din team.json nu mai contează: nici numele, nici adresa, nici tokenul.
        self.assertEqual((bridge.developer, bridge.hub.hub_url), ("tester", DEFAULT_HUB_URL))
        self.assertNotIn("team", bridge.status())
        self.assertNotIn("team", bridge.status()["features"])
        self.assertEqual(self.bridge(logger=lines.append).developer, "tester")
        self.assertEqual(len(lines), 1)

    def test_identity_updates_status_jobs_developer_and_the_hub_client(self):
        bridge = self.bridge()
        early = bridge.create_terminal_session({"provider": "claude"})
        self.assertIsNone(early.workspace)
        for body in ({"user_id": -1}, {"user_id": "12"}, {"user_id": True}, {"user_id": 2 ** 53}, {"game_id": 1.5}, {"place_id": -2},
                     {"creator_id": "5"}, {"name": "n" * 65}, {"name": 3}, {"place_name": "p" * 201}, {"creator_type": 5}):
            with self.subTest(body=body), self.assertRaises(BridgeError) as error:
                bridge.set_identity(body)
            self.assertEqual(error.exception.status, 400)
        self.assertEqual((bridge.identity, bridge.workspace, bridge.hub.identities), (None, None, []))
        result = bridge.set_identity(IDENTITY)
        # Răspunsul spune și cu ce fereastră Studio a fost asociată raportarea (fără `instance_id` intră pe cheia „legacy”).
        self.assertEqual(result, {"ok": True, "workspace": BALL, "identity": {"user_id": 12345, "name": "ellob", "avatar": "rbxthumb://type=AvatarHeadShot&id=12345&w=48&h=48"},
                                  "instance_id": "legacy", "studio_id": None})
        self.assertEqual(bridge.developer, "ellob")
        self.assertEqual(bridge.hub.identities, [({"user_id": 12345, "name": "ellob"}, BALL)])
        # Joburile fără workspace o primesc acum; cele noi o primesc la creare; sesiunile poartă cheia în tablă și în sync.
        self.assertEqual(early.workspace, "game:987654")
        later = bridge.create_terminal_session({"provider": "codex"})
        self.assertEqual((later.workspace, later.developer), ("game:987654", "ellob"))
        self.assertEqual({row["workspace"] for row in bridge.hub_payload({})}, {"game:987654"})
        self.assertEqual(bridge.identity_payload(), {"roblox": {"user_id": 12345, "name": "ellob"}, "workspace": BALL})
        # Fără cont (user_id 0): identitate necunoscută, numele „Studio”, fără avatar; developer-ul revine la fallback.
        result = bridge.set_identity({"user_id": 0, "place_id": 0, "game_id": 0, "place_name": ""})
        self.assertEqual(result["identity"], {"user_id": 0, "name": "Studio", "avatar": None})
        self.assertEqual(result["workspace"], {"key": "local", "game_id": 0, "place_id": 0, "name": "", "creator_id": 0, "creator_type": "necunoscut"})
        self.assertEqual((bridge.developer, bridge.identity_payload()["roblox"]), ("tester", None))
        # Un cont fără nume primește user_<id>; un joc nepublicat cu place_id are cheia place:<id>; valorile din Luau pot fi float integrale.
        result = bridge.set_identity({"user_id": 7.0, "place_id": 42, "game_id": 0, "place_name": " Test \x01 "})
        self.assertEqual((result["identity"]["name"], result["workspace"]["key"], result["workspace"]["name"]), ("user_7", "place:42", "Test"))
        self.assertEqual(len(bridge.hub.identities), 3)


class HubLoopbackTests(unittest.TestCase):
    """Hub fals pe loopback + `HubClient` real: înregistrare, sync cu identitate și workspace, claims în hub, tablă, panou, claims locale."""

    def setUp(self):
        self.temp = temp_dir()
        self.opened = []
        self.hub = None
        self.bridge = None

    def tearDown(self):
        if self.bridge is not None:
            self.bridge.close()
        if self.hub is not None:
            self.hub.stop()
        self.temp.cleanup()

    def start(self, mode="approved", native=None):
        self.hub = FakeHub(mode).start()
        self.bridge = Bridge(FakeProviders(), Path(self.temp.name) / "runtime", native or FakeNative(), UI_TOKEN, developer="tester",
                             state_directory=Path(self.temp.name) / "state", hub_url=self.hub.url, device_token=DEVICE_TOKEN,
                             hub_client_factory=fast_hub_client, open_url=self.opened.append)
        return self.bridge

    def hub_state(self):
        return self.bridge.hub_status()["status"]

    def wait_state(self, state):
        self.assertTrue(wait_until(lambda: self.hub_state() == state), self.bridge.hub_status())

    def syncs(self):
        with self.hub.lock:
            return [body for method, path, body in self.hub.requests if path == "/hub/sync"]

    def calls(self, path):
        with self.hub.lock:
            return [body for method, request_path, body in self.hub.requests if request_path == path]

    def text(self, result):
        return result["content"][0]["text"]

    def test_registration_sync_and_status_follow_identity_and_workspace(self):
        bridge = self.start()
        self.wait_state("approved")
        # `syncs()` numără cererile ajunse la hub, dar `last_sync` se scrie în client abia după ce răspunsul este procesat: așteptăm starea clientului.
        self.assertTrue(wait_until(lambda: len(self.syncs()) >= 1 and bridge.hub_status()["last_sync"] is not None))
        hub = bridge.hub_status()
        self.assertEqual({key: hub[key] for key in ("url", "status", "hub_id", "device_id", "error", "enrollment")},
                         {"url": self.hub.url, "status": "approved", "hub_id": "f" * 32, "device_id": DEVICE_ID, "error": None, "enrollment": "approve"})
        self.assertIsInstance(hub["last_sync"], float)
        register = self.calls("/hub/register")[0]
        self.assertEqual({key: register[key] for key in ("roblox", "machine", "bridge_id", "version", "workspace")},
                         {"roblox": None, "machine": bridge.machine, "bridge_id": bridge.bridge_id, "version": "1.0.0", "workspace": None})
        self.assertEqual(self.syncs()[0]["workspace"], None)
        self.assertEqual(bridge.status()["members"], [])
        # Identitatea ajunge în sync-ul următor (trezit imediat), iar status-ul arată prezența și workspace-ul.
        bridge.set_identity(IDENTITY)
        self.assertTrue(wait_until(lambda: any(body["workspace"] == BALL and body.get("roblox") == {"user_id": 12345, "name": "ellob"} for body in self.syncs())))
        self.assertTrue(wait_until(lambda: bridge.status()["members"] != []))
        status = bridge.status()
        self.assertEqual((status["developer"], status["identity"]["name"], status["workspace"], status["panel_url"]), ("ellob", "ellob", BALL, self.hub.url + "/panel"))
        self.assertEqual([(row["device_id"], row["roblox_name"], row["me"]) for row in status["members"]], [(DEVICE_ID, "ellob", True)])
        self.assertEqual(status["workspaces"], [{"key": "game:987654", "name": "Ball", "members_online": 1, "sessions_active": 0, "claims": 0, "mine": True}])
        self.assertNotIn(DEVICE_TOKEN, json.dumps(status))
        # Panoul se deschide cu tokenul în fragment; răspunsul nu îl conține.
        self.assertEqual(bridge.panel_open(), {"ok": True, "url": self.hub.url + "/panel"})
        self.assertEqual(self.opened, [self.hub.url + "/panel#device=" + DEVICE_TOKEN])
        # Proxy-ul read-only spre hub, cu memorare 2 s.
        before = len(self.calls("/hub/workspaces"))
        workspaces = bridge.hub_workspaces()
        self.assertEqual((workspaces["ok"], [row["key"] for row in workspaces["workspaces"]]), (True, ["game:987654"]))
        self.assertEqual(bridge.hub_workspaces(), workspaces)
        self.assertEqual(len(self.calls("/hub/workspaces")), before + 1)
        self.assertEqual(bridge.hub_workspace("game:987654")["workspace"]["name"], "Ball")
        with self.assertRaises(BridgeError) as error:
            bridge.hub_workspace("game:1")
        self.assertEqual((error.exception.status, str(error.exception)), (404, "Workspace-ul nu există."))
        with self.assertRaises(BridgeError) as error:
            bridge.hub_workspace("")
        self.assertEqual(error.exception.status, 400)

    def test_claims_go_to_the_hub_with_the_job_workspace_and_the_board_mirrors_the_workspace(self):
        bridge = self.start()
        self.wait_state("approved")
        bridge.set_identity(IDENTITY)
        job = bridge.create_terminal_session({"provider": "claude", "cwd": "Ball"})
        self.assertEqual(job.workspace, "game:987654")
        claimed = bridge.agent_call(job, "hub_claim", {"paths": ["Workspace.Map.Zone3"], "reason": "test"})
        self.assertFalse(claimed["isError"])
        self.assertEqual(json.loads(self.text(claimed)), {"ok": True, "claimed": ["Workspace.Map.Zone3"], "scope": "hub"})
        self.assertEqual(self.calls("/hub/claims/claim")[-1], {"job_id": job.id, "paths": ["Workspace.Map.Zone3"], "reason": "test", "workspace": "game:987654"})
        self.assertEqual([display(claim.path) for claim in self.hub.table("game:987654").held_by(job.id)], ["Workspace.Map.Zone3"])
        self.assertEqual(bridge.claims.local.snapshot(), [])
        self.assertTrue(bridge.claims.covers(job.id, normalize("Workspace.Map.Zone3.Part")))
        # Conflict decis în hub: alt dispozitiv ține Lighting.
        self.hub.table("game:987654").claim("job-ana", "ana", ["Lighting"])
        conflict = bridge.agent_call(job, "hub_claim", {"paths": ["Lighting.Sky"]})
        self.assertTrue(conflict["isError"])
        payload = json.loads(self.text(conflict))
        self.assertEqual((payload["ok"], payload["conflicts"][0]["developer"], payload["conflicts"][0]["held_path"]), (False, "ana", "Lighting"))
        # Deținătorul apare în refuzul de impunere după ce sync-ul aduce claims-urile colegilor în oglindă.
        self.assertTrue(wait_until(lambda: bridge.claims.holder(normalize("Lighting"), exclude_job=job.id, workspace="game:987654") is not None))
        refused = bridge.agent_call(job, "execute_luau", {"code": "return 1", "scope": ["Lighting"]})
        self.assertTrue(refused["isError"])
        self.assertIn("ana", self.text(refused))
        # Tabla: sesiunile proprii (mine) + ale altora din workspace (remote), claims cu `mine`, prezenții, jurnalul hub-ului.
        self.hub.others = [other_session("job-ana")]
        self.hub.events["job-ana"] = [{"seq": 1, "type": "text", "text": "salut de la ana"}]
        self.assertTrue(wait_until(lambda: any(row["job_id"] == "job-ana" for row in bridge.board()["sessions"])))
        self.assertTrue(wait_until(lambda: any(claim["job_id"] == "job-ana" for claim in bridge.board()["claims"])))
        board = bridge.board()
        self.assertEqual(set(board), {"ok", "bridge_id", "studios", "connected", "default_studio_id", "developer", "identity", "workspace", "hub", "instances",
                                      "members", "workspaces", "sessions", "claims", "journal"})
        rows = {row["job_id"]: row for row in board["sessions"]}
        own = rows[job.id]
        self.assertEqual({key: own[key] for key in ("workspace", "device_id", "roblox_user_id", "machine", "remote", "mine", "developer", "claims")},
                         {"workspace": "game:987654", "device_id": DEVICE_ID, "roblox_user_id": 12345, "machine": bridge.machine, "remote": False,
                          "mine": True, "developer": "ellob", "claims": ["Workspace.Map.Zone3"]})
        self.assertEqual((rows["job-ana"]["remote"], rows["job-ana"]["mine"], rows["job-ana"]["developer"]), (True, False, "ana"))
        self.assertEqual({(claim["path"], claim["mine"], claim["workspace"]) for claim in board["claims"]},
                         {("Workspace.Map.Zone3", True, "game:987654"), ("Lighting", False, "game:987654")})
        self.assertEqual((board["hub"]["status"], board["workspace"], board["identity"]["user_id"]), ("approved", BALL, 12345))
        self.assertEqual([(row["roblox_name"], row["me"]) for row in board["members"]], [("ellob", True), ("ana", False)])
        # Sesiunea colegului se citește din oglindă, dar nu se controlează. Evenimentele vin cu un ciclu de sync după rândul sesiunii
        # (daemon-ul le cere prin `want` abia după ce știe că jobul există), deci le așteptăm în loc să presupunem că au sosit deja.
        def mirrored_events():
            poll = bridge.poll_jobs({"jobs": [{"job_id": "job-ana", "after": 0}]})
            return ([event["text"] for event in poll["jobs"][0]["events"]], poll["jobs"][0]["remote"])

        self.assertTrue(wait_until(lambda: mirrored_events()[0] == ["salut de la ana"]), mirrored_events())
        self.assertEqual(mirrored_events(), (["salut de la ana"], True))
        with self.assertRaises(BridgeError) as error:
            bridge.cancel_job("job-ana")
        self.assertEqual(error.exception.status, 403)
        # Jurnalul: intrarea pleacă la hub cu workspace și revine numerotată.
        bridge._record(job, "multi_edit", ["Workspace.Map.Zone3"], "multi_edit · Workspace.Map.Zone3")
        self.assertTrue(wait_until(lambda: bridge.board()["journal"] != []))
        entry = bridge.board()["journal"][-1]
        self.assertEqual((entry["seq"], entry["workspace"], entry["developer"], entry["tool"]), (1, "game:987654", "ellob", "multi_edit"))
        self.assertEqual(list(bridge.journal), [])
        # hub_board pentru agent: hub fără notice, workspace-ul jobului, colegii, claims-urile.
        agent_board = json.loads(self.text(bridge.agent_call(job, "hub_board", {})))
        self.assertEqual((agent_board["hub"], agent_board["workspace"]["key"], agent_board["developer"]),
                         ({"status": "approved", "url": self.hub.url, "notice": None}, "game:987654", "ellob"))
        self.assertEqual({row["job_id"] for row in agent_board["sessions"]}, {job.id, "job-ana"})
        self.assertEqual([row["roblox_name"] for row in agent_board["members"]], ["ellob", "ana"])
        # Eliberarea și închiderea ajung în hub.
        released = json.loads(self.text(bridge.agent_call(job, "hub_release", {"paths": ["Workspace.Map.Zone3"]})))
        self.assertEqual(released, {"ok": True, "released": ["Workspace.Map.Zone3"]})
        self.assertEqual(self.hub.table("game:987654").held_by(job.id), [])
        bridge.agent_call(job, "hub_claim", {"paths": ["ServerStorage"]})
        bridge.close_terminal(job)
        self.assertEqual(self.hub.table("game:987654").held_by(job.id), [])
        self.assertEqual(bridge.claims.held_by(job.id), [])

    def test_pending_device_keeps_claims_local_and_reconciles_them_on_approval(self):
        bridge = self.start("pending")
        self.wait_state("pending")
        bridge.set_identity(IDENTITY)
        job = bridge.create_terminal_session({"provider": "claude"})
        claimed = json.loads(self.text(bridge.agent_call(job, "hub_claim", {"paths": ["Workspace.Map.Zone3", "Lighting"], "reason": "local"})))
        self.assertEqual(claimed, {"ok": True, "claimed": ["Workspace.Map.Zone3", "Lighting"], "scope": "local",
                                   "notice": "Hub-ul nu este disponibil (stare: pending): claims-urile sunt locale, colegii nu le văd."})
        self.assertEqual(self.calls("/hub/claims/claim"), [])
        self.assertEqual({claim["path"] for claim in bridge.claims.local.snapshot()}, {"Workspace.Map.Zone3", "Lighting"})
        board = json.loads(self.text(bridge.agent_call(job, "hub_board", {})))
        self.assertEqual(board["hub"], {"status": "pending", "url": self.hub.url, "notice": "Hub-ul nu este disponibil (stare: pending): claims-urile sunt locale, colegii nu le văd."})
        self.assertEqual((board["members"], [(claim["path"], claim["workspace"]) for claim in board["claims"]]),
                         ([], [("Workspace.Map.Zone3", "game:987654"), ("Lighting", "game:987654")]))
        self.assertEqual((bridge.board()["hub"]["status"], bridge.board()["members"], bridge.status()["workspaces"]), ("pending", [], []))
        # Modificările nu sunt blocate de lipsa hub-ului: claim-ul local ajunge.
        self.assertFalse(bridge.agent_call(job, "multi_edit", {"file_path": "Workspace.Map.Zone3.Script", "edits": []})["isError"])
        # Panoul se deschide și în pending (arată ecranul de așteptare).
        self.assertEqual(bridge.panel_open()["url"], self.hub.url + "/panel")
        # Adminul aprobă dispozitivul: la revenirea în approved, claims-urile locale sunt retrimise; Lighting este ținută de ana → refuzată.
        self.hub.table("game:987654").claim("job-ana", "ana", ["Lighting"])
        self.hub.mode = "approved"
        self.wait_state("approved")
        self.assertTrue(wait_until(lambda: bridge.claims.local.snapshot() == []))
        self.assertEqual([display(claim.path) for claim in self.hub.table("game:987654").held_by(job.id)], ["Workspace.Map.Zone3"])
        claim_events = [event for event in job.events if event["type"] == "claim"]
        denied = next(event for event in claim_events if event["action"] == "denied")
        self.assertEqual((denied["paths"], denied["holder"]), (["Lighting"], "ana"))
        taken = next(event for event in claim_events if "preluate de hub" in event["text"])
        self.assertEqual((taken["action"], taken["paths"]), ("claimed", ["Workspace.Map.Zone3"]))
        self.assertTrue(wait_until(lambda: [display(claim.path) for claim in bridge.claims.held_by(job.id)] == ["Workspace.Map.Zone3"]))
        self.assertEqual(bridge.claims.backend(job.id), "hub")
        self.assertTrue(wait_until(lambda: bridge.status()["members"] != []))

    def test_old_or_revoking_hub_leaves_claims_local_with_an_explicit_message(self):
        bridge = self.start("old")
        self.wait_state("offline")
        self.assertEqual(bridge.hub_status()["error"], HUB_OLD)
        self.assertEqual(HUB_OLD, "Hub-ul rulează o versiune mai veche.")
        bridge.set_identity(IDENTITY)
        job = bridge.create_terminal_session({"provider": "codex"})
        claimed = json.loads(self.text(bridge.agent_call(job, "hub_claim", {"paths": ["@play"]})))
        self.assertEqual((claimed["scope"], claimed["claimed"]), ("local", ["@play:studio-1"]))
        self.assertIn("stare: offline", claimed["notice"])
        self.assertIn("stare: offline", json.loads(self.text(bridge.agent_call(job, "hub_board", {})))["hub"]["notice"])
        with self.assertRaises(BridgeError) as error:
            bridge.hub_workspaces()
        self.assertEqual((error.exception.status, str(error.exception)), (503, "Hub-ul nu este disponibil (stare: offline)."))
        self.assertEqual(bridge.panel_open()["url"], self.hub.url + "/panel")
        self.assertEqual(bridge.status()["panel_url"], self.hub.url + "/panel")
        self.bridge.close()
        self.hub.stop()
        self.bridge = self.start("revoked")
        self.wait_state("revoked")
        job = self.bridge.create_terminal_session({"provider": "claude"})
        self.assertIn("stare: revoked", json.loads(self.text(self.bridge.agent_call(job, "hub_claim", {"paths": ["Lighting"]})))["notice"])
        self.assertTrue(self.bridge.claims.local.held_by(job.id))

    def test_a_session_picks_its_studio_window_instead_of_the_daemon_guessing(self):
        """1.0: cu mai multe ferestre deschise nu se mai ghicește după numele jocului — sesiunea alege cu `studio_use`."""
        native = FakeNative([{"id": "studio-ball", "name": "Ball (placeId: 111)"}, {"id": "studio-kart", "name": "Kart (placeId: 222)"}])
        bridge = self.start(native=native)
        job = bridge.create_terminal_session({"provider": "claude"})
        with self.assertRaisesRegex(BridgeError, "Mai multe instanțe Studio") as error:
            bridge.agent_call(job, "inspect_instance", {"path": "Workspace"})
        self.assertEqual(error.exception.status, 409)
        self.assertIn("Ball", str(error.exception))
        # Identitatea raportată de o fereastră nu mai trage sesiunile din terminal după ea.
        bridge.set_identity(dict(IDENTITY, place_id=111, place_name="Ball", instance_id="fereastra-ball"))
        with self.assertRaisesRegex(BridgeError, "Mai multe instanțe Studio"):
            bridge.agent_call(job, "inspect_instance", {"path": "Workspace"})
        # Lista este vizibilă înainte de alegere, cu jocul fiecărei ferestre.
        listing = json.loads(self.text(bridge.agent_call(job, "studio_use", {})))
        self.assertIsNone(listing["chosen"])
        self.assertEqual({row["studio_id"]: row["place_name"] for row in listing["studios"]},
                         {"studio-ball": "Ball", "studio-kart": None})
        # Alegerea după nume, după placeId și după id duce toate la aceeași fereastră.
        for wanted in ("Kart", "222", "studio-kart"):
            with self.subTest(wanted=wanted):
                chosen = json.loads(self.text(bridge.agent_call(job, "studio_use", {"studio": wanted})))
                self.assertEqual(chosen["chosen"], "studio-kart")
                bridge.agent_call(job, "inspect_instance", {"path": "Workspace"})
                self.assertEqual(native.calls[-1][2], "studio-kart")
        # Trecerea la altă fereastră eliberează claims-urile proiectului anterior.
        bridge.claims.claim(job.id, job.developer, ["Workspace.Map"])
        moved = json.loads(self.text(bridge.agent_call(job, "studio_use", {"studio": "Ball"})))
        self.assertEqual((moved["chosen"], moved["released"]), ("studio-ball", ["Workspace.Map"]))
        self.assertEqual(bridge.claims.snapshot(), [])
        self.assertEqual(moved["workspace"], "game:987654")
        # Un nume care nu există sau care se potrivește cu două ferestre nu schimbă nimic.
        for wanted, message in (("Minecraft", "Nicio fereastră"), ("placeId", "mai multe ferestre")):
            with self.subTest(wanted=wanted), self.assertRaisesRegex(BridgeError, message):
                bridge.agent_call(job, "studio_use", {"studio": wanted})
        self.assertEqual(job.studio_id, "studio-ball")
        # Suprascrierea manuală din plugin rămâne pentru sesiunile care nu au ales.
        other = bridge.create_terminal_session({"provider": "claude", "cli_session_id": "alta"})
        bridge.set_default_studio({"studio_id": "studio-kart"})
        bridge.agent_call(other, "inspect_instance", {"path": "Workspace"})
        self.assertEqual(native.calls[-1][2], "studio-kart")


class UpdateTests(unittest.TestCase):
    """0.6: verificarea canalului și aplicarea pachetului `plugin`, cu plugin_root într-un director temporar (repo-ul real nu este atins)."""

    def setUp(self):
        self.temp = temp_dir()
        self.bridge = make_bridge(self.temp.name)
        self.root = Path(self.temp.name) / "plugin"
        (self.root / ".claude-plugin").mkdir(parents=True)
        (self.root / ".claude-plugin" / "plugin.json").write_text('{"version": "1.0.0"}', encoding="utf-8")
        (self.root / "README.md").write_bytes(b"vechi\n")
        self.bridge.plugin_root = self.root
        self.channel = LocalChannel().__enter__()
        self.local = Path(self.temp.name) / "local"
        self.environ = patch.dict(os.environ, {"STUDIO_HARNESS_UPDATE_URL": self.channel.url("/manifest.json"),
                                               "STUDIO_HARNESS_AUTO_UPDATE": "1", "LOCALAPPDATA": str(self.local)})
        self.environ.start()

    def tearDown(self):
        self.environ.stop()
        self.channel.__exit__(None, None, None)
        self.bridge.close()
        self.temp.cleanup()

    def publish(self, version, rbxmx=b"<roblox>nou</roblox>"):
        """Manifest cu un pachet `plugin` construit aici; întoarce (manifest, intrarea plugin)."""
        bundle = make_zip({"README.md": b"nou\n", "scripts/studio_bridge.py": b"VERSION = 'nou'\n", "dist/StudioHarness.rbxmx": rbxmx},
                          top="roblox-studio-harness-" + version)
        entry = self.channel.entry("/roblox-studio-harness-" + version + ".zip", bundle)
        return self.channel.manifest(version, plugin=entry), entry

    def running_job(self):
        job = Job("7" * 32, "claude", "studio-1", "session-7", "test", {}, developer="tester")
        self.bridge.jobs[job.id] = job
        return job

    def test_status_starts_idle_and_check_marks_a_newer_version_available(self):
        self.assertEqual(self.bridge.status()["update"], {"current": "1.0.0", "available": None, "state": "idle", "message": "",
                                                          "checked": None, "restart_required": False})
        manifest, _ = self.publish("1.1.0")
        result = self.bridge.update_check(timeout=3)
        self.assertEqual((result["newer"], result["available"], result["auto"], result["error"], result["manifest"]), (True, "1.1.0", True, None, manifest))
        status = self.bridge.status()["update"]
        self.assertEqual((status["current"], status["state"], status["available"], status["message"], status["restart_required"]),
                         ("1.0.0", "available", "1.1.0", "", False))
        self.assertIsInstance(status["checked"], float)
        # Doar manifestul a fost citit; statusul nu expune manifestul sau URL-uri de descărcare.
        self.assertEqual(self.channel.requests, ["/manifest.json"])
        self.assertNotIn(self.channel.base, json.dumps(status))
        self.assertEqual((self.root / "README.md").read_bytes(), b"vechi\n")

    def test_check_reports_same_version_and_channel_errors(self):
        self.publish("1.0.0")
        result = self.bridge.update_check(timeout=3)
        self.assertEqual((result["newer"], result["available"]), (False, "1.0.0"))
        self.assertEqual((self.bridge.update_status()["state"], self.bridge.update_status()["available"]), ("idle", "1.0.0"))
        self.channel.serve("/manifest.json", b"{")
        self.assertIn("JSON valid", self.bridge.update_check(timeout=3)["error"])
        status = self.bridge.update_status()
        self.assertEqual((status["state"], status["available"]), ("error", None))
        self.assertIn("JSON valid", status["message"])
        requests = list(self.channel.requests)
        with patch.dict(os.environ, {"STUDIO_HARNESS_UPDATE_URL": "http://example.com/manifest.json"}):
            result = self.bridge.update_check(timeout=3)
        self.assertIn("neconfigurat", result["error"])
        self.assertEqual(self.bridge.update_status()["state"], "error")
        self.assertEqual(self.channel.requests, requests)

    def test_apply_waits_for_active_jobs_then_installs_with_backup_and_the_local_token(self):
        placeholder = updater.LOCAL_TOKEN_PLACEHOLDER.encode("utf-8")
        manifest, _ = self.publish("1.1.0", rbxmx=b'<roblox>nou ' + SLOT_OPEN + placeholder + b'</string></roblox>')
        job = self.running_job()
        self.assertFalse(self.bridge.update_apply(manifest))
        self.assertEqual(self.bridge.update_status()["state"], "idle")
        self.assertEqual(self.channel.requests, [])
        self.assertEqual((self.root / "README.md").read_bytes(), b"vechi\n")
        job.state = "completed"
        self.assertTrue(self.bridge.update_apply(manifest))
        status = self.bridge.status()["update"]
        self.assertEqual((status["state"], status["restart_required"], status["current"]), ("installed", True, "1.0.0"))
        self.assertIn("Actualizare 1.1.0 instalată", status["message"])
        self.assertEqual(self.channel.requests, ["/roblox-studio-harness-1.1.0.zip"])
        self.assertEqual((self.root / "README.md").read_bytes(), b"nou\n")
        self.assertEqual((self.root / "scripts" / "studio_bridge.py").read_bytes(), b"VERSION = 'nou'\n")
        backups = list((Path(self.temp.name) / "state" / "backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "README.md").read_bytes(), b"vechi\n")
        plugin = self.local / "Roblox" / "Plugins" / "StudioHarness.rbxmx"
        if os.name == "nt":
            # 1.0: fișierul instalat primește tokenul UI în locul placeholder-ului; pachetul din dist/ rămâne cu placeholder-ul.
            self.assertEqual(plugin.read_bytes(), b'<roblox>nou ' + SLOT_OPEN + UI_TOKEN.encode("utf-8") + b'</string></roblox>')
            self.assertEqual((self.root / "dist" / "StudioHarness.rbxmx").read_bytes(), b'<roblox>nou ' + SLOT_OPEN + placeholder + b'</string></roblox>')
            self.assertIn("repornește Studio", status["message"])
        else:
            self.assertFalse(plugin.exists())

    def test_apply_refuses_bad_downloads_and_manifests_without_a_plugin_bundle(self):
        manifest, entry = self.publish("1.1.0")
        self.assertFalse(self.bridge.update_apply({"version": "1.1.0", "files": {"hub": entry}}))
        self.assertEqual(self.bridge.update_status()["state"], "idle")
        self.assertFalse(self.bridge.update_apply({"version": "1.1.0", "files": {"plugin": {**entry, "sha256": "0" * 64}}}))
        status = self.bridge.update_status()
        self.assertEqual((status["state"], status["restart_required"]), ("error", False))
        self.assertIn("SHA256", status["message"])
        self.assertEqual((self.root / "README.md").read_bytes(), b"vechi\n")
        self.assertFalse((Path(self.temp.name) / "state" / "backups").exists())
        # După o eroare, un pachet corect se aplică.
        self.assertTrue(self.bridge.update_apply(manifest))
        self.assertEqual(self.bridge.update_status()["state"], "installed")


if __name__ == "__main__":
    unittest.main()

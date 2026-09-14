"""Hub 1.0 — dispozitive (pending/approved/revoked, înrolare), workspace-uri, sync scoped, claims per workspace, rute HTTP, avatar, CLI.

Hub cu ceas injectat; HubServer pe 127.0.0.1 port 0; fără rețea în afara loopback-ului (fetcher de avatar fals); starea doar în
%TEMP%\\studio-harness-1.0\\hub. Nu se folosesc porturile 34871/34880 (daemon-ul și Studio-ul real rulează pe acest PC)."""

import contextlib
import hashlib
import http.client
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import team_hub  # noqa: E402
import updater  # noqa: E402
from team_hub import Actor, Hub, HubError, HubServer, TeamHub  # noqa: E402
from test_updater import LocalChannel, make_zip  # noqa: E402

# Codul de administrator al hub-ului de test (test_project_api îl importă tot de aici).
TOKEN = "cod-admin-de-test-0123456789"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
USER_IDS = {"ana": 101, "dan": 202, "eva": 303}
BALL = {"key": "game:987654", "game_id": 987654, "place_id": 1291603, "name": "Ball", "creator_id": 555, "creator_type": "User"}
KART = {"key": "game:222", "game_id": 222, "place_id": 333, "name": "Kart", "creator_id": 9, "creator_type": "Group"}
TEST_TMP = Path(os.environ.get("HARNESS_TEST_TMP") or Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "studio-harness-1.0" / "hub")


def temp_dir():
    TEST_TMP.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=str(TEST_TMP))


def device_token(name):
    """Token de dispozitiv determinist (64 hex) pentru un developer de test."""
    return hashlib.sha256(("dispozitiv-" + name).encode("utf-8")).hexdigest()


def device_id(name):
    return hashlib.sha256(device_token(name).encode("utf-8")).hexdigest()[:16]


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def session_row(job_id, state="running", events=(), **extra):
    """O sesiune așa cum o trimite daemon-ul în sync; `developer` din corp este ignorat de hub."""
    row = {"job_id": job_id, "kind": "terminal", "provider": "claude", "developer": "ignorat", "name": "Claude Code · joc",
           "state": state, "studio_id": None, "cwd": "joc", "started": 1000.0, "last_activity": 1000.0,
           "pending_approval": False, "claims": [], "events": list(events)}
    row.update(extra)
    return row


def event(seq, text="mesaj"):
    return {"seq": seq, "type": "text", "text": text}


def journal_item(tool="multi_edit", **extra):
    item = {"time": 1000.5, "job_id": "job-a", "provider": "claude", "tool": tool, "paths": ["ServerScriptService.Main"], "summary": tool + " · Main"}
    item.update(extra)
    return item


def project(snapshot_id="snap-1", count=2):
    nodes = [[0, -1, "Workspace", "Workspace", 0], [1, 0, "Part", "Podea", 0]][:count]
    return {"snapshot_id": snapshot_id, "place_id": 1291603, "place_name": "Ball", "studio_id": "s", "count": count, "taken": 999.0,
            "truncated": False, "groups": {"assets": {"key": "assets", "label": "Asseturi", "count": 1, "by_class": {"Part": 1}, "entries": []},
                                           "settings": {"key": "settings", "label": "Setări", "count": 1, "by_class": {"Workspace": 1}, "entries": []}},
            "nodes": nodes}


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
    status = int(head.split(b" ", 2)[1])
    return bool(early), status, json.loads(payload)


class HubBase(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.lines = []
        self.hub = Hub(TOKEN, clock=lambda: self.now[0], logger=self.lines.append, avatar_fetcher=lambda user, size: None)

    def actor(self, name, admin=False):
        return self.hub.actor(device_token(name), TOKEN if admin else None, allow_unknown=True)

    def admin(self):
        return self.hub.actor(None, TOKEN)

    def register(self, name, workspace=BALL, roblox="auto", machine=None, **extra):
        body = {"roblox": {"user_id": USER_IDS.get(name, 1), "name": name} if roblox == "auto" else roblox,
                "machine": machine or "pc-" + name, "bridge_id": "bridge-" + name, "version": "1.0.0", "workspace": workspace}
        body.update(extra)
        return self.hub.register(self.actor(name), body)

    def approve(self, *names):
        for name in names:
            self.hub.approve_device(self.admin(), {"device_id": device_id(name)})

    def join(self, name, workspace=BALL, **extra):
        """Înregistrare + aprobare de admin: întoarce device_id-ul."""
        response = self.register(name, workspace, **extra)
        self.assertEqual(response["status"], "pending")
        self.approve(name)
        return response["device_id"]

    def sync(self, name, workspace=BALL, **body):
        return self.hub.sync(self.actor(name), {"workspace": workspace, **body})

    def claim(self, name, job_id, *paths, reason="test", **extra):
        return self.hub.claim(self.actor(name), {"job_id": job_id, "paths": list(paths), "reason": reason, **extra})

    def held(self, key="game:987654"):
        table = self.hub.claims.get(key)
        return [(row["path"], row["job_id"]) for row in table.snapshot()] if table else []

    def present(self, response):
        return [(row["roblox_name"], row["machine"]) for row in response["members"]]


class RegisterTests(HubBase):
    def test_register_validates_fields_and_needs_the_device_header(self):
        with self.assertRaises(HubError) as error:
            self.hub.register(self.admin(), {"machine": "pc", "bridge_id": "b", "workspace": None})
        self.assertEqual((error.exception.status, str(error.exception)), (400, team_hub.DEVICE_REQUIRED))
        invalid = [
            {}, {"machine": "pc"}, {"machine": "", "bridge_id": "b"}, {"machine": "   ", "bridge_id": "b"}, {"machine": "\x01\x02", "bridge_id": "b"},
            {"machine": "m" * 65, "bridge_id": "b"}, {"machine": 3, "bridge_id": "b"}, {"machine": "pc", "bridge_id": ""},
            {"machine": "pc", "bridge_id": "b", "version": 4}, {"machine": "pc", "bridge_id": "b", "version": "v" * 33},
            {"machine": "pc", "bridge_id": "b", "roblox": "ana"}, {"machine": "pc", "bridge_id": "b", "roblox": {"user_id": -1}},
            {"machine": "pc", "bridge_id": "b", "roblox": {"user_id": True}}, {"machine": "pc", "bridge_id": "b", "roblox": {"user_id": 1, "name": "n" * 65}},
            {"machine": "pc", "bridge_id": "b", "workspace": "game:1"}, {"machine": "pc", "bridge_id": "b", "workspace": {"game_id": -1}},
            {"machine": "pc", "bridge_id": "b", "workspace": {"game_id": True}}, {"machine": "pc", "bridge_id": "b", "workspace": {"game_id": 1, "place_name": 5}},
        ]
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(HubError) as error:
                self.hub.register(self.actor("ana"), body)
            self.assertEqual(error.exception.status, 400)
        self.assertEqual((self.hub.devices, self.hub.workspaces), ({}, {}))

    def test_new_device_waits_for_the_admin_and_only_register_and_status_work_meanwhile(self):
        response = self.register("ana")
        ana = device_id("ana")
        self.assertEqual(response, {"ok": True, "status": "pending", "device_id": ana, "hub_id": self.hub.hub_id, "enrollment": "approve"})
        self.assertEqual(ana, hashlib.sha256(device_token("ana").encode()).hexdigest()[:16])
        device = self.hub.devices[ana]
        self.assertEqual(device, {"device_id": ana, "token_hash": hashlib.sha256(device_token("ana").encode()).hexdigest(), "roblox_user_id": 101,
                                  "roblox_name": "ana", "machine": "pc-ana", "bridge_id": "bridge-ana", "version": "1.0.0", "status": "pending",
                                  "first_seen": 1000.0, "last_seen": 1000.0, "approved_at": 0.0, "approved_by": "", "workspace": "game:987654"})
        self.assertEqual(self.hub.workspaces["game:987654"], {**BALL, "first_seen": 1000.0, "last_seen": 1000.0, "project": None})
        self.assertTrue(any(line.startswith("dispozitiv nou în așteptare: ana @ pc-ana (" + ana + ")") for line in self.lines), self.lines)
        self.assertTrue(any(line.startswith("workspace nou: Ball (game:987654)") for line in self.lines), self.lines)
        self.assertNotIn(device_token("ana"), "\n".join(self.lines))
        # Reînregistrarea (daemon repornit, alt bridge_id) nu creează alt dispozitiv și rămâne în așteptare.
        self.now[0] = 1010.0
        again = self.register("ana", bridge_id="bridge-2", version="1.0.1")
        self.assertEqual((again["status"], sorted(self.hub.devices)), ("pending", [ana]))
        self.assertEqual((device["bridge_id"], device["version"], device["last_seen"], device["first_seen"]), ("bridge-2", "1.0.1", 1010.0, 1000.0))
        # Rutele de membru refuză cu 403 și starea; /hub/status răspunde.
        for call in (lambda: self.sync("ana"), lambda: self.hub.workspaces_summary(self.actor("ana")), lambda: self.hub.panel_data(self.actor("ana")),
                     lambda: self.claim("ana", "job-a", "Lighting"), lambda: self.hub.session(self.actor("ana"), "job-a")):
            with self.assertRaises(HubError) as error:
                call()
            self.assertEqual((error.exception.status, str(error.exception), error.exception.payload),
                             (403, team_hub.PENDING_DEVICE, {"status": "pending", "device_id": ana}))
        status = self.hub.status(self.actor("ana"))
        self.assertEqual(status, {"ok": True, "version": "1.0.0", "hub_id": self.hub.hub_id, "enrollment": "approve", "now": 1010.0, "admin": False,
                                  "device": {"device_id": ana, "status": "pending", "roblox_user_id": 101, "roblox_name": "ana", "machine": "pc-ana"},
                                  "workspaces": 1, "members_online": 0})
        # Adminul aprobă; a doua aprobare nu schimbă nimic.
        approved = self.hub.approve_device(self.admin(), {"device_id": ana})
        self.assertEqual((approved["ok"], approved["device"]["status"], approved["device"]["approved_at"], approved["device"]["approved_by"]),
                         (True, "approved", 1010.0, "admin"))
        self.assertNotIn("token_hash", approved["device"])
        self.now[0] = 1020.0
        self.assertEqual(self.hub.approve_device(self.admin(), {"device_id": ana})["device"]["approved_at"], 1010.0)
        self.assertEqual(self.register("ana")["status"], "approved")
        response = self.sync("ana")
        self.assertEqual((response["ok"], response["device"], response["workspace"], self.present(response)),
                         (True, {"status": "approved"}, "game:987654", [("ana", "pc-ana")]))
        self.assertTrue(any(line.startswith("dispozitiv aprobat de admin: ana @ pc-ana") for line in self.lines), self.lines)

    def test_a_device_without_a_roblox_account_is_logged_only_with_its_machine(self):
        """Înaintea primului /v1/identity numele cade pe mașină: șablonul `<nume> @ <mașină>` ar scrie același text de două ori."""
        self.register("ombra", roblox=None, machine="OMBRA")
        device = device_id("ombra")
        self.assertIn("dispozitiv nou în așteptare: OMBRA (" + device + ") (daemon 1.0.0)", self.lines)
        self.approve("ombra")
        self.assertIn("dispozitiv aprobat de admin: OMBRA (" + device + ")", self.lines)
        # După identitate, jurnalul revine la forma completă.
        self.register("ombra", roblox={"user_id": 101, "name": "ellob"}, machine="OMBRA")
        self.hub.revoke_device(self.admin(), {"device_id": device})
        self.assertTrue(any(line.startswith("dispozitiv revocat de admin: ellob @ OMBRA (" + device + ")") for line in self.lines), self.lines)

    def test_pending_devices_are_capped_and_a_stale_one_makes_room(self):
        """`/hub/register` este singura rută deschisă necunoscuților: fără plafon, oricine ar umple fila Dispozitive și starea."""
        for index in range(team_hub.MAX_PENDING_DEVICES):
            self.now[0] += 1
            self.register("nou-" + str(index), workspace=None)
        self.assertEqual(len(self.hub.devices), team_hub.MAX_PENDING_DEVICES)
        with self.assertRaises(HubError) as error:
            self.register("peste-plafon", workspace=None)
        self.assertEqual((error.exception.status, str(error.exception)), (429, team_hub.TOO_MANY_PENDING))
        # Un dispozitiv aprobat nu ocupă loc în lista de așteptare.
        self.approve("nou-0")
        self.register("dupa-aprobare", workspace=None)
        # Peste plafon, un dispozitiv în așteptare neatins de peste 24 h (cel mai vechi) face loc; cel aprobat rămâne.
        self.now[0] += team_hub.PENDING_IDLE_SECONDS + 1
        self.register("peste-plafon", workspace=None)
        self.assertEqual((device_id("nou-1") in self.hub.devices, device_id("nou-0") in self.hub.devices,
                          device_id("peste-plafon") in self.hub.devices), (False, True, True))
        self.assertTrue(any(line.startswith("dispozitiv în așteptare uitat (inactiv de peste 24 h, listă plină)") for line in self.lines), self.lines)

    def test_open_enrollment_approves_new_and_waiting_devices(self):
        self.register("ana")
        self.hub.enrollment = "open"
        response = self.register("dan")
        self.assertEqual((response["status"], response["enrollment"]), ("approved", "open"))
        self.assertEqual((self.hub.devices[device_id("dan")]["approved_by"], self.hub.devices[device_id("dan")]["approved_at"]), ("open", 1000.0))
        # Dispozitivul care aștepta este aprobat la următoarea înregistrare.
        self.assertEqual(self.register("ana")["status"], "approved")
        self.assertEqual(self.hub.devices[device_id("ana")]["approved_by"], "open")
        self.assertEqual(sum(1 for line in self.lines if line.startswith("dispozitiv aprobat automat (înrolare deschisă)")), 2)
        # Adminul închide înrolarea: dispozitivele noi așteaptă din nou.
        self.assertEqual(self.hub.set_enrollment(self.admin(), {"mode": "approve"}), {"ok": True, "enrollment": "approve"})
        self.assertEqual(self.register("eva")["status"], "pending")
        for body in ({}, {"mode": "closed"}, {"mode": 3}):
            with self.subTest(body=body), self.assertRaises(HubError) as error:
                self.hub.set_enrollment(self.admin(), body)
            self.assertEqual(error.exception.status, 400)
        with self.assertRaises(HubError) as error:
            self.hub.set_enrollment(self.actor("ana"), {"mode": "open"})
        self.assertEqual((error.exception.status, str(error.exception)), (403, team_hub.ADMIN_REQUIRED))

    def test_revoked_device_is_refused_everywhere_and_forget_lets_it_come_back_as_pending(self):
        ana, dan = self.join("ana"), self.join("dan")
        self.sync("ana", sessions=[session_row("job-a", events=[event(1)])])
        self.claim("ana", "job-a", "Workspace.Map")
        self.claim("dan", "job-d", "Lighting")
        revoked = self.hub.revoke_device(self.admin(), {"device_id": ana})
        self.assertEqual((revoked["ok"], revoked["device"]["status"]), (True, "revoked"))
        self.assertEqual((sorted(self.hub.sessions), self.held()), ([], [("Lighting", "job-d")]))
        self.assertTrue(any(line.startswith("dispozitiv revocat de admin: ana @ pc-ana") for line in self.lines), self.lines)
        with self.assertRaises(HubError) as error:
            self.register("ana")
        self.assertEqual((error.exception.status, str(error.exception), error.exception.payload),
                         (403, team_hub.REVOKED_DEVICE, {"status": "revoked", "device_id": ana}))
        # Datele trimise la înregistrare sunt totuși reținute.
        self.assertEqual(self.hub.devices[ana]["last_seen"], 1000.0)
        with self.assertRaises(HubError) as error:
            self.sync("ana")
        self.assertEqual((error.exception.status, error.exception.payload), (403, {"status": "revoked", "device_id": ana}))
        self.assertEqual(self.hub.status(self.actor("ana"))["device"]["status"], "revoked")
        self.assertEqual(self.hub.revoke_device(self.admin(), {"device_id": ana})["device"]["status"], "revoked")
        # Aprobarea din nou îl readuce; `forget` îl șterge de tot și revine ca pending.
        self.assertEqual(self.hub.approve_device(self.admin(), {"device_id": ana})["device"]["status"], "approved")
        self.assertEqual(self.sync("ana")["device"], {"status": "approved"})
        self.assertEqual(self.hub.forget_device(self.admin(), {"device_id": ana}), {"ok": True})
        self.assertEqual(sorted(self.hub.devices), [dan])
        with self.assertRaises(HubError) as error:
            self.hub.actor(device_token("ana"), None)
        self.assertEqual((error.exception.status, error.exception.payload), (401, {"status": "unknown"}))
        self.assertEqual(self.register("ana")["status"], "pending")
        for body in ({"device_id": "absent"}, {}, {"device_id": 3}):
            with self.subTest(body=body), self.assertRaises(HubError) as error:
                self.hub.forget_device(self.admin(), body)
            self.assertIn(error.exception.status, (400, 404))
        with self.assertRaises(HubError) as error:
            self.hub.approve_device(self.admin(), {"device_id": "absent"})
        self.assertEqual((error.exception.status, str(error.exception)), (404, "Dispozitivul nu există."))

    def test_actor_validation_and_admin_privileges(self):
        for code in ("", "alt-cod-0123456789"):
            with self.subTest(code=code), self.assertRaises(HubError) as error:
                self.hub.actor(None, code)
            self.assertEqual((error.exception.status, str(error.exception)), (401, team_hub.ADMIN_INVALID))
        for token in ("", "scurt", "g" * 64, device_token("ana")):
            with self.subTest(token=token), self.assertRaises(HubError) as error:
                self.hub.actor(token, None)
            self.assertEqual((error.exception.status, str(error.exception), error.exception.payload), (401, team_hub.UNKNOWN_DEVICE, {"status": "unknown"}))
        # Codul de admin este verificat chiar dacă tokenul este necunoscut (înregistrare cu ambele antete).
        with self.assertRaises(HubError) as error:
            self.hub.actor(device_token("ana"), "alt-cod-0123456789", allow_unknown=True)
        self.assertEqual(error.exception.status, 401)
        self.assertEqual(self.hub.actor(None, None), Actor())
        self.assertEqual(self.hub.actor(None, TOKEN), Actor(admin=True))
        unknown = self.hub.actor(device_token("ana"), None, allow_unknown=True)
        self.assertEqual((unknown.admin, unknown.device_id, unknown.token_hash), (False, device_id("ana"), hashlib.sha256(device_token("ana").encode()).hexdigest()))
        # Fără niciun antet: 401 pe rutele de membru și pe /hub/status.
        for call in (lambda: self.hub.status(Actor()), lambda: self.hub.workspaces_summary(Actor()), lambda: self.hub.panel_data(Actor())):
            with self.assertRaises(HubError) as error:
                call()
            self.assertEqual((error.exception.status, error.exception.payload), (401, {"status": "unknown"}))
        # Doar cod de admin: status cu device null, rutele care acționează ca dispozitiv cer antetul (400).
        status = self.hub.status(self.admin())
        self.assertEqual((status["admin"], status["device"]), (True, None))
        for call in (lambda: self.hub.sync(self.admin(), {"workspace": None}), lambda: self.hub.claim(self.admin(), {"job_id": "j", "paths": ["Lighting"]}),
                     lambda: self.hub.wait(self.admin(), {"path": "Lighting", "timeout_seconds": 0})):
            with self.assertRaises(HubError) as error:
                call()
            self.assertEqual((error.exception.status, str(error.exception)), (400, team_hub.DEVICE_REQUIRED))
        # Admin + dispozitiv pending: contextul dispozitivului cu privilegii de admin, indiferent de stare.
        self.register("ana")
        response = self.hub.sync(self.actor("ana", admin=True), {"workspace": BALL})
        self.assertEqual((response["device"], self.present(response)), ({"status": "pending"}, []))
        panel = self.hub.panel_data(self.actor("ana", admin=True))
        self.assertEqual((panel["me"]["device_id"], panel["me"]["status"], panel["me"]["admin"], panel["pending_devices"]), (device_id("ana"), "pending", True, 1))
        # Dispozitivul aprobat fără cod de admin nu vede numărul celor în așteptare.
        self.approve("ana")
        panel = self.hub.panel_data(self.actor("ana"))
        self.assertEqual((panel["me"]["status"], panel["me"]["admin"]), ("approved", False))
        self.assertNotIn("pending_devices", panel)

    def test_roblox_identity_is_informative_and_falls_back_to_the_machine(self):
        self.join("ana", roblox=None)
        device = self.hub.devices[device_id("ana")]
        self.assertEqual((device["roblox_user_id"], device["roblox_name"]), (0, ""))
        self.sync("ana", sessions=[session_row("job-a")])
        self.assertEqual(self.hub.sessions["job-a"]["meta"]["developer"], "pc-ana")
        self.claim("ana", "job-a", "Lighting")
        self.assertEqual(self.hub.claims["game:987654"].snapshot()[0]["developer"], "pc-ana")
        # user_id 0 cu nume = identitate necunoscută; user_id fără nume = user_<id>; numele se curăță de caractere de control.
        self.register("dan", roblox={"user_id": 0, "name": "dan"})
        self.assertEqual(self.hub.devices[device_id("dan")]["roblox_name"], "")
        self.register("eva", roblox={"user_id": 303})
        self.assertEqual(self.hub.devices[device_id("eva")]["roblox_name"], "user_303")
        self.register("eva", roblox={"user_id": 303, "name": " eva\x00 "})
        self.assertEqual(self.hub.devices[device_id("eva")]["roblox_name"], "eva")
        # În sync `roblox` apare doar când s-a schimbat; fără cheie identitatea rămâne.
        self.sync("ana", roblox={"user_id": 101, "name": "ana"}, sessions=[session_row("job-a")])
        self.assertEqual((device["roblox_user_id"], device["roblox_name"], self.hub.sessions["job-a"]["meta"]["developer"]), (101, "ana", "ana"))
        self.sync("ana")
        self.assertEqual(device["roblox_name"], "ana")
        self.sync("ana", roblox=None)
        self.assertEqual((device["roblox_user_id"], device["roblox_name"]), (0, ""))
        with self.assertRaises(HubError):
            self.sync("ana", roblox=[1])


class SyncTests(HubBase):
    def test_sync_validations(self):
        self.join("ana")
        with self.assertRaises(HubError) as error:
            self.hub.sync(self.actor("ana"), {})
        self.assertEqual((error.exception.status, str(error.exception)), (400, "Câmpul workspace lipsește (meta sau null)."))
        for body in ({"workspace": "game:1"}, {"workspace": {"game_id": "x"}}, {"workspace": {"game_id": 2 ** 53}}, {"sessions": "x"},
                     {"sessions": [session_row("j")] * 65}, {"sessions": ["x"]}, {"sessions": [{"kind": "terminal"}]}, {"sessions": [{"job_id": ""}]},
                     {"sessions": [session_row("j" * 129)]}, {"sessions": [{**session_row("j"), "events": "x"}]}, {"roblox": "ana"}):
            with self.subTest(body=body), self.assertRaises(HubError) as error:
                self.sync("ana", **body)
            self.assertEqual(error.exception.status, 400)
        # Câmpurile opționale cu formă greșită sunt ignorate, nu refuzate.
        response = self.sync("ana", want="x", journal="x", journal_after="x", touch="x", project_digest="x" * 129, project="x")
        member = {"device_id": device_id("ana"), "roblox_user_id": 101, "roblox_name": "ana", "machine": "pc-ana", "last_seen": 1000.0, "online": True}
        self.assertEqual(response, {"ok": True, "now": 1000.0, "hub_id": self.hub.hub_id, "device": {"status": "approved"}, "workspace": "game:987654",
                                    "members": [member], "sessions": [], "events": {}, "claims": [], "journal": [], "journal_seq": 0, "want_project": False,
                                    "workspaces": [{"key": "game:987654", "name": "Ball", "game_id": 987654, "place_id": 1291603, "creator_id": 555,
                                                    "creator_type": "User", "last_seen": 1000.0, "members_online": [member], "sessions_active": 0,
                                                    "claims": 0, "project": None}]})

    def test_sessions_and_events_are_mirrored_only_inside_the_workspace(self):
        ana, dan, eva = self.join("ana"), self.join("dan"), self.join("eva", KART)
        self.sync("ana", sessions=[session_row("job-a", events=[event(1, "unu"), event(2, "doi")])])
        # Reluarea unor evenimente deja trimise nu le dublează; seq nevalid sau vechi este ignorat.
        self.sync("ana", sessions=[session_row("job-a", events=[event(2, "doi"), {"seq": True, "type": "text"}, {"seq": "4"},
                                                                event(3, "trei"), "x", event(1, "unu")])])
        self.assertEqual([item["seq"] for item in self.hub.sessions["job-a"]["events"]], [1, 2, 3])
        self.assertEqual((self.hub.sessions["job-a"]["workspace"], self.hub.sessions["job-a"]["device_id"]), ("game:987654", ana))
        # Propriile sesiuni nu se întorc, iar `want` pe propriul job nu întoarce nimic.
        own = self.sync("ana", want={"job-a": 0})
        self.assertEqual((own["sessions"], own["events"]), ([], {}))
        first = self.sync("dan", want={"job-a": 0, "job-absent": 0})
        self.assertEqual(len(first["sessions"]), 1)
        row = first["sessions"][0]
        self.assertEqual(row, {"job_id": "job-a", "kind": "terminal", "provider": "claude", "developer": "ana", "name": "Claude Code · joc",
                               "state": "running", "studio_id": None, "cwd": "joc", "started": 1000.0, "last_activity": 1000.0,
                               "pending_approval": False, "claims": [], "device_id": ana, "roblox_user_id": 101, "machine": "pc-ana",
                               "remote": True, "workspace": "game:987654"})
        self.assertEqual([item["text"] for item in first["events"]["job-a"]], ["unu", "doi", "trei"])
        self.assertEqual([item["seq"] for item in self.sync("dan", want={"job-a": 2})["events"]["job-a"]], [3])
        self.assertEqual(self.sync("dan", want={"job-a": 3})["events"], {"job-a": []})
        for cursor in ("0", True, None):
            with self.subTest(cursor=cursor):
                self.assertEqual(self.sync("dan", want={"job-a": cursor})["events"], {})
        # eva este în alt workspace: nu vede sesiunea și nici evenimentele ei, chiar dacă le cere.
        other = self.sync("eva", KART, want={"job-a": 0})
        self.assertEqual((other["workspace"], other["sessions"], other["events"], self.present(other)), ("game:222", [], {}, [("eva", "pc-eva")]))
        self.assertEqual(self.present(first), [("ana", "pc-ana"), ("dan", "pc-dan")])
        # Un dispozitiv nu poate suprascrie sesiunea altuia.
        self.sync("dan", sessions=[session_row("job-a", state="cancelled", name="furt")])
        self.assertEqual((self.hub.sessions["job-a"]["meta"]["state"], self.hub.sessions["job-a"]["device_id"]), ("running", ana))
        # Sesiunile celorlalți sunt ordonate după `started`, iar `remote` nu poate fi anulat de daemon.
        self.sync("dan", sessions=[session_row("job-d", started=900.0, remote=False)])
        rows = self.sync("ana")["sessions"]
        self.assertEqual([(item["job_id"], item["remote"]) for item in rows], [("job-d", True)])
        # Sesiunea poartă workspace-ul din rând (jobul creat într-un alt joc), altfel pe cel curent; o cheie invalidă cade pe cel curent.
        self.sync("ana", sessions=[session_row("job-k", workspace="game:222"), session_row("job-x", workspace="jocul:1")])
        self.assertEqual((self.hub.sessions["job-k"]["workspace"], self.hub.sessions["job-x"]["workspace"]), ("game:222", "game:987654"))
        self.assertEqual([item["job_id"] for item in self.sync("eva", KART)["sessions"]], ["job-k"])
        self.assertEqual([item["job_id"] for item in self.sync("dan")["sessions"]], ["job-a", "job-x"])
        # Sesiunea rămâne în workspace-ul ei și după ce dispozitivul schimbă jocul.
        self.sync("ana", KART)
        self.assertEqual([item["job_id"] for item in self.sync("dan")["sessions"]], ["job-a", "job-x"])
        self.assertEqual(self.present(self.sync("dan")), [("dan", "pc-dan")])
        self.assertEqual(self.present(self.sync("eva", KART)), [("ana", "pc-ana"), ("eva", "pc-eva")])

    def test_workspace_null_means_present_nowhere_and_sessions_fall_back_to_local(self):
        ana = self.join("ana")
        response = self.sync("ana", None, sessions=[session_row("job-a")], want={"job-a": 0})
        self.assertEqual((response["workspace"], response["members"], response["sessions"], response["claims"], response["journal"]), (None, [], [], [], []))
        self.assertEqual(self.hub.devices[ana]["workspace"], None)
        self.assertEqual(self.hub.sessions["job-a"]["workspace"], "local")
        self.assertEqual(self.hub.workspaces["local"], {"key": "local", "game_id": 0, "place_id": 0, "name": "local", "creator_id": 0,
                                                        "creator_type": "necunoscut", "first_seen": 1000.0, "last_seen": 1000.0, "project": None})
        self.assertEqual([row["key"] for row in response["workspaces"]], ["game:987654", "local"])
        self.assertEqual(self.hub.status(self.actor("ana"))["members_online"], 1)
        # Revenirea în joc: prezentă din nou, iar sesiunea a rămas în `local`.
        self.assertEqual(self.present(self.sync("ana")), [("ana", "pc-ana")])
        self.assertEqual(self.hub.sessions["job-a"]["workspace"], "local")
        # Fără workspace curent, un claim fără `workspace` în corp este refuzat.
        self.join("dan")
        self.sync("dan", None)
        with self.assertRaises(HubError) as error:
            self.claim("dan", "job-d", "Lighting")
        self.assertEqual((error.exception.status, str(error.exception)), (400, team_hub.NO_WORKSPACE))
        self.assertEqual(self.claim("dan", "job-d", "Lighting", workspace="local")["claimed"], ["Lighting"])

    def test_hub_keeps_only_the_last_1024_events_per_job(self):
        self.join("ana")
        self.sync("ana", sessions=[session_row("job-a", events=[event(seq) for seq in range(1, 1031)])])
        stored = self.hub.sessions["job-a"]["events"]
        self.assertEqual((len(stored), stored[0]["seq"], stored[-1]["seq"]), (1024, 7, 1030))
        self.assertEqual(team_hub.MAX_EVENTS, 1024)

    def test_session_meta_and_journal_entries_are_validated_and_capped(self):
        """Hub-ul nu stochează structuri arbitrare: doar câmpurile cunoscute, cu tipuri și lungimi plafonate."""
        self.join("ana")
        row = {"job_id": "job-a", "kind": "terminal", "provider": {"nu": "e text"}, "name": "n" * 500, "state": "running",
               "started": 1000.0, "last_activity": "acum", "pending_approval": "da", "claims": ["Workspace.A"] * 100 + [5],
               "secret": "nu se stochează", "cwd": "c" * 900}
        self.sync("ana", sessions=[dict(row, events=[{"seq": 1, "type": "text", "text": "x" * (team_hub.MAX_EVENT_BYTES + 10)}])])
        meta = self.hub.sessions["job-a"]["meta"]
        self.assertEqual((meta["provider"], len(meta["name"]), len(meta["cwd"]), meta["last_activity"], meta["pending_approval"]),
                         (None, team_hub.MAX_META_TEXT, team_hub.MAX_META_TEXT, None, False))
        self.assertEqual(len(meta["claims"]), team_hub.MAX_META_CLAIMS)
        self.assertNotIn("secret", meta)
        # Un eveniment peste plafon devine o notă scurtă, cu același `seq` (cursorul clientului rămâne corect).
        self.assertEqual(self.hub.sessions["job-a"]["events"], [{"seq": 1, "type": "text", "text": team_hub.EVENT_TOO_LARGE}])
        # Jurnal: cel mult MAX_SYNC_JOURNAL intrări pe sync, cu texte și căi plafonate.
        entries = [{"tool": "t" * 300, "summary": "s" * 900, "paths": ["Workspace.A"] * 100, "job_id": 7}
                   for _ in range(team_hub.MAX_SYNC_JOURNAL + 5)]
        self.sync("ana", journal=entries)
        rows = [row for row in self.hub.journal]
        self.assertEqual(len(rows), team_hub.MAX_SYNC_JOURNAL)
        self.assertEqual((len(rows[0]["tool"]), len(rows[0]["summary"]), len(rows[0]["paths"]), rows[0]["job_id"]),
                         (80, 300, team_hub.MAX_JOURNAL_PATHS, None))

    def test_a_device_cannot_hold_more_sessions_than_the_ceiling(self):
        """Plafonul este per dispozitiv, nu per cerere: o sesiune închisă face loc, altfel hub-ul refuză cu 429."""
        self.join("ana")
        for index in range(team_hub.MAX_DEVICE_SESSIONS):
            self.sync("ana", sessions=[session_row("job-" + str(index), state="running")])
        self.assertEqual(len(self.hub.sessions), team_hub.MAX_DEVICE_SESSIONS)
        with self.assertRaises(HubError) as error:
            self.sync("ana", sessions=[session_row("job-peste", state="running")])
        self.assertEqual((error.exception.status, str(error.exception)), (429, team_hub.SESSIONS_FULL))
        self.sync("ana", sessions=[session_row("job-0", state="completed")])
        self.sync("ana", sessions=[session_row("job-peste", state="running")])
        self.assertEqual(("job-0" in self.hub.sessions, "job-peste" in self.hub.sessions), (False, True))

    def test_workspaces_have_a_ceiling_and_only_the_idle_ones_are_forgotten(self):
        """Un dispozitiv raportează oricâte workspace-uri (fiecare loc deschis în Studio): fără plafon, `self.workspaces` ar crește la nesfârșit."""
        self.join("ana")
        self.sync("ana", sessions=[session_row("job-a")])
        self.claim("ana", "job-a", "Lighting", workspace="game:3")
        for index in range(2, team_hub.MAX_WORKSPACES + 2):
            self.now[0] += 1
            self.sync("ana", workspace={"game_id": index, "place_id": index, "place_name": "W" + str(index)})
        self.assertLessEqual(len(self.hub.workspaces), team_hub.MAX_WORKSPACES)
        # Rămân cele folosite: workspace-ul sesiunii, cel cu un claim și cel curent al dispozitivului; cel mai vechi inactiv pleacă.
        self.assertEqual([key in self.hub.workspaces for key in (BALL["key"], "game:3", "game:" + str(team_hub.MAX_WORKSPACES + 1), "game:2")],
                         [True, True, True, False])
        self.assertNotIn("game:2", self.hub.claims)
        self.assertEqual(self.held("game:3"), [("Lighting", "job-a")])
        # Claim-ul eliberat lasă workspace-ul inactiv: la următoarea creare este uitat și el.
        self.hub.release(self.actor("ana"), {"job_id": "job-a", "workspace": "game:3"})
        self.now[0] += 1
        self.sync("ana", workspace={"game_id": 9001, "place_id": 9001, "place_name": "Ultim"})
        self.assertEqual(("game:3" in self.hub.workspaces, "game:9001" in self.hub.workspaces), (False, True))

    def test_journal_rows_get_seq_time_identity_and_workspace_and_page_after_the_cursor(self):
        ana, dan, eva = self.join("ana"), self.join("dan"), self.join("eva", KART)
        entry = journal_item(developer="fals", machine="fals", seq=99, device_id="fals")
        response = self.sync("ana", journal=[entry, "x", {"tool": "insert_asset"}])
        self.assertEqual(response["journal_seq"], 2)
        self.assertEqual(response["journal"][0], {"seq": 1, "time": 1000.5, "workspace": "game:987654", "device_id": ana, "developer": "ana",
                                                  "roblox_user_id": 101, "machine": "pc-ana", "job_id": "job-a", "provider": "claude",
                                                  "tool": "multi_edit", "paths": ["ServerScriptService.Main"], "summary": "multi_edit · Main"})
        # Fără `time` valid intrarea primește momentul sync-ului; textele lipsă rămân None, `paths` este mereu o listă.
        self.assertEqual((response["journal"][1]["seq"], response["journal"][1]["time"], response["journal"][1]["tool"], response["journal"][1]["paths"]),
                         (2, 1000.0, "insert_asset", []))
        response = self.sync("dan", journal=[journal_item("execute_luau")], journal_after=1)
        self.assertEqual([(row["seq"], row["developer"], row["machine"]) for row in response["journal"]], [(2, "ana", "pc-ana"), (3, "dan", "pc-dan")])
        self.assertEqual(response["journal_seq"], 3)
        self.assertEqual(self.sync("dan", journal_after=3)["journal"], [])
        self.assertEqual(self.sync("dan", journal_after=7)["journal"], [])
        for cursor in (-1, True, "2", None):
            with self.subTest(cursor=cursor):
                self.assertEqual([row["seq"] for row in self.sync("dan", journal_after=cursor)["journal"]], [1, 2, 3])
        # eva (Kart) nu vede jurnalul Ball-ului; intrarea ei ajunge în Kart. Numerotarea este globală.
        kart = self.sync("eva", KART, journal=[journal_item("generate_mesh")])
        self.assertEqual([(row["seq"], row["workspace"]) for row in kart["journal"]], [(4, "game:222")])
        self.assertEqual([row["seq"] for row in self.sync("dan")["journal"]], [1, 2, 3])
        # Workspace-ul intrării: din rând, altfel din sesiune, altfel cel curent.
        self.sync("ana", sessions=[session_row("job-k", workspace="game:222")])
        self.sync("ana", journal=[journal_item(workspace="game:222"), journal_item(job_id="job-k"), journal_item(job_id="job-nou"), journal_item(workspace="rau")])
        self.assertEqual([(row["seq"], row["workspace"]) for row in list(self.hub.journal)[-4:]],
                         [(5, "game:222"), (6, "game:222"), (7, "game:987654"), (8, "game:987654")])
        # Sync-ul întoarce cele mai recente SYNC_JOURNAL intrări de după cursor (nu primele).
        with patch.object(team_hub, "SYNC_JOURNAL", 2):
            self.assertEqual([row["seq"] for row in self.sync("dan")["journal"]], [7, 8])
            self.assertEqual([row["seq"] for row in self.sync("dan", journal_after=7)["journal"]], [8])
        self.assertEqual((team_hub.SYNC_JOURNAL, team_hub.MAX_JOURNAL, self.hub.journal.maxlen), (50, 1000, 1000))

    def test_sync_touch_renews_claims_of_own_jobs_only(self):
        self.join("ana"), self.join("dan")
        self.claim("ana", "job-a", "Workspace.Map")
        self.claim("ana", "job-b", "Lighting")
        self.claim("dan", "job-d", "ServerStorage")
        self.now[0] = 1500.0
        self.sync("ana", touch=["job-a", 3, "job-absent", "job-d"])
        # job-a atins la 1500 rămâne; job-b neatins din 1000 a expirat după 600 s; job-d (al lui dan) nu a fost atins de ana.
        self.now[0] = 1700.0
        self.assertEqual(self.held(), [("Workspace.Map", "job-a")])
        self.assertEqual(self.hub.claims["game:987654"].snapshot()[0]["expires"], 2100.0)

    def test_finished_sessions_are_kept_thirty_minutes_then_dropped(self):
        self.join("ana"), self.join("dan")
        self.sync("ana", sessions=[session_row("job-a", state="completed")])
        self.assertEqual(self.hub.sessions["job-a"]["closed_at"], 1000.0)
        self.now[0] = 1300.0
        # Aceeași sesiune raportată din nou ca activă nu mai are termen de expirare.
        self.sync("ana", sessions=[session_row("job-a")])
        self.assertIsNone(self.hub.sessions["job-a"]["closed_at"])
        self.now[0] = 1700.0
        self.sync("ana", sessions=[session_row("job-a")])
        self.assertEqual([(row["job_id"], row["state"]) for row in self.sync("dan")["sessions"]], [("job-a", "running")])
        self.sync("ana", sessions=[session_row("job-a", state="cancelled")])
        self.assertEqual(self.hub.sessions["job-a"]["closed_at"], 1700.0)
        self.now[0] = 3500.0
        self.sync("ana")
        self.assertEqual([(row["job_id"], row["state"]) for row in self.sync("dan")["sessions"]], [("job-a", "cancelled")])
        self.now[0] = 3501.0
        self.sync("ana")
        self.assertEqual(self.sync("dan")["sessions"], [])
        self.assertEqual((self.hub.sessions, self.hub.job_devices), ({}, {}))
        self.assertEqual(team_hub.SESSION_RETENTION, 1800)

    def test_project_is_attached_to_the_current_workspace_and_requested_sparingly(self):
        ana, dan = self.join("ana"), self.join("dan")
        # Fără proiect hub-ul cere snapshot-ul anunțat; proiectul trimis se atașează workspace-ului curent.
        self.assertTrue(self.sync("ana", project_digest="snap-a")["want_project"])
        self.assertFalse(self.sync("ana", project_digest="snap-a", project=project("snap-a"))["want_project"])
        stored = self.hub.workspaces["game:987654"]["project"]
        self.assertEqual((stored["snapshot_id"], stored["count"], stored["reported_by"], stored["developer"], stored["machine"], stored["received"], stored["taken"]),
                         ("snap-a", 2, ana, "ana", "pc-ana", 1000.0, 999.0))
        self.assertTrue(any(line.startswith("proiect primit de la ana @ pc-ana") and "Ball (game:987654): 2 noduri" in line for line in self.lines), self.lines)
        full = self.hub.project(self.actor("dan"), "game:987654")["project"]
        self.assertEqual((full["snapshot_id"], full["nodes"], sorted(full["groups"])), ("snap-a", project()["nodes"], ["assets", "settings"]))
        self.assertEqual(self.sync("dan")["workspaces"][0]["project"], {"count": 2, "digest": "snap-a", "groups": {"assets": 1, "settings": 1}})
        # dan are alt snapshot_id (GUID per scanare): hub-ul nu i-l cere cât timp harta lui ana este proaspătă (< 300 s).
        self.assertFalse(self.sync("dan", project_digest="snap-d")["want_project"])
        self.now[0] = 1299.0
        self.assertFalse(self.sync("dan", project_digest="snap-d")["want_project"])
        # ana a rescanat: propriul dispozitiv este întrebat imediat.
        self.assertTrue(self.sync("ana", project_digest="snap-a2")["want_project"])
        self.assertFalse(self.sync("ana", project_digest="snap-a")["want_project"])
        self.now[0] = 1300.0
        self.assertTrue(self.sync("dan", project_digest="snap-d")["want_project"])
        self.assertFalse(self.sync("dan", project_digest="snap-d", project=project("snap-d", count=1))["want_project"])
        self.assertEqual((self.hub.workspaces["game:987654"]["project"]["reported_by"], self.hub.workspaces["game:987654"]["project"]["count"]), (dan, 1))
        self.assertFalse(self.sync("ana", project_digest="snap-a2")["want_project"])
        # Proiect invalid: ignorat și digest-ul lui nu mai este cerut; un digest nou este cerut din nou.
        self.now[0] = 2000.0
        self.assertFalse(self.sync("ana", project_digest="snap-rau", project={"snapshot_id": "snap-rau", "nodes": "x", "groups": {}})["want_project"])
        self.assertEqual(self.hub.devices[ana]["project_rejected"], "snap-rau")
        self.assertFalse(self.sync("ana", project_digest="snap-rau")["want_project"])
        self.assertTrue(self.sync("ana", project_digest="snap-bun")["want_project"])
        self.assertTrue(any(line.startswith("proiect invalid de la ana @ pc-ana") for line in self.lines), self.lines)
        # Fără workspace curent nu se cere și nu se reține nimic.
        self.assertFalse(self.sync("ana", None, project_digest="snap-x", project=project("snap-x"))["want_project"])
        self.assertEqual(self.hub.workspaces["game:987654"]["project"]["snapshot_id"], "snap-d")
        # Alt workspace are propriul proiect.
        self.assertTrue(self.sync("ana", KART, project_digest="snap-k")["want_project"])
        self.sync("ana", KART, project_digest="snap-k", project=project("snap-k"))
        self.assertEqual({key: row["project"]["snapshot_id"] for key, row in self.hub.workspaces.items()}, {"game:987654": "snap-d", "game:222": "snap-k"})
        self.assertEqual(team_hub.PROJECT_REFRESH_SECONDS, 300)

    def test_members_present_and_workspace_summaries(self):
        self.join("ana"), self.join("dan", KART)
        self.register("eva")
        self.now[0] = 1010.0
        self.sync("dan", KART, sessions=[session_row("job-d"), session_row("job-e", state="completed")])
        self.claim("dan", "job-d", "Lighting")
        rows = self.hub.workspaces_summary(self.actor("ana"))["workspaces"]
        self.assertEqual([(row["key"], row["name"], [item["roblox_name"] for item in row["members_online"]], row["sessions_active"], row["claims"]) for row in rows],
                         [("game:222", "Kart", ["dan"], 1, 1), ("game:987654", "Ball", ["ana"], 0, 0)])
        # ana nu a mai sincronizat de 15 s: încă online (≤ 15 s); după 16 s dispare din prezenți, dar nu e încă offline (reap la 60 s).
        self.now[0] = 1015.0
        self.assertEqual(self.present(self.hub.workspace(self.actor("dan"), "game:987654")), [("ana", "pc-ana")])
        self.now[0] = 1016.0
        self.assertEqual(self.hub.workspace(self.actor("dan"), "game:987654")["members"], [])
        self.assertFalse(self.hub.devices[device_id("ana")].get("reaped"))
        self.assertEqual(self.hub.status(self.actor("dan"))["members_online"], 1)
        self.assertEqual(team_hub.ONLINE_SECONDS, 15)


class ClaimTests(HubBase):
    def test_claim_validations(self):
        self.join("ana")
        for body in ({"job_id": "j"}, {"job_id": "j", "paths": []}, {"job_id": "j", "paths": "Lighting"}, {"job_id": "j", "paths": [1]},
                     {"job_id": "j", "paths": ["L"] * 33}, {"job_id": "", "paths": ["Lighting"]}, {"paths": ["Lighting"]},
                     {"job_id": "j", "paths": ["Lighting"], "reason": 3}, {"job_id": "j", "paths": ["Lighting"], "reason": "r" * 201},
                     {"job_id": "j", "paths": ["@bogus"]}, {"job_id": "j", "paths": ["Lighting", ""]}, {"job_id": "j", "paths": ["Lighting"], "workspace": "x"}):
            with self.subTest(body=body), self.assertRaises(HubError) as error:
                self.hub.claim(self.actor("ana"), body)
            self.assertEqual(error.exception.status, 400)
        self.assertEqual(self.held(), [])
        with self.assertRaises(HubError) as error:
            self.hub.claim(self.hub.actor(device_token("nimeni"), None, allow_unknown=True), {"job_id": "j", "paths": ["Lighting"]})
        self.assertEqual(error.exception.status, 401)

    def test_conflicts_exist_only_inside_the_same_workspace(self):
        self.join("ana"), self.join("dan"), self.join("eva", KART)
        response = self.claim("ana", "job-a", "game.Workspace.Map.Zone3", "Lighting", reason="zona 3")
        self.assertEqual(response, {"ok": True, "claimed": ["Workspace.Map.Zone3", "Lighting"], "held": ["Workspace.Map.Zone3", "Lighting"]})
        # Aceeași cale în alt workspace este liberă.
        self.assertEqual(self.claim("eva", "job-e", "Workspace.Map")["claimed"], ["Workspace.Map"])
        with self.assertRaises(HubError) as error:
            self.claim("dan", "job-b", "ServerStorage", "Workspace.Map")
        self.assertEqual((error.exception.status, str(error.exception)), (409, "Conflict de claim."))
        self.assertEqual(error.exception.payload, {"conflicts": [{"path": "Workspace.Map", "holder": "job-a", "developer": "ana",
                                                                  "held_path": "Workspace.Map.Zone3", "since": 1000.0}]})
        # Nici ServerStorage, calea liberă, nu a fost revendicată (atomic).
        self.assertEqual(self.held(), [("Workspace.Map.Zone3", "job-a"), ("Lighting", "job-a")])
        self.assertEqual(self.held("game:222"), [("Workspace.Map", "job-e")])
        # Workspace-ul explicit din corp: dan (în Ball) poate revendica în Kart doar ce este liber acolo.
        with self.assertRaises(HubError) as error:
            self.claim("dan", "job-b", "Workspace.Map.Zone1", workspace="game:222")
        self.assertEqual(error.exception.payload["conflicts"][0]["developer"], "eva")
        self.assertEqual(self.claim("dan", "job-b", "ServerStorage", workspace="game:222")["held"], ["ServerStorage"])
        # Claims-urile apar în sync doar pentru workspace-ul curent, cu cheia; sumarul numără per workspace.
        self.assertEqual([(row["path"], row["workspace"]) for row in self.sync("dan")["claims"]], [("Workspace.Map.Zone3", "game:987654"), ("Lighting", "game:987654")])
        self.assertEqual([(row["path"], row["job_id"]) for row in self.sync("eva", KART)["claims"]], [("Workspace.Map", "job-e"), ("ServerStorage", "job-b")])
        self.assertEqual({row["key"]: row["claims"] for row in self.sync("ana")["workspaces"]}, {"game:987654": 2, "game:222": 2})
        # Un workspace necunoscut din corp este creat minimal (jobul a fost creat înaintea identității).
        self.assertEqual(self.claim("ana", "job-a", "SoundService", workspace="place:77")["claimed"], ["SoundService"])
        self.assertEqual((self.hub.workspaces["place:77"]["name"], self.hub.workspaces["place:77"]["place_id"]), ("place:77", 77))
        snapshot = self.hub.claims["game:987654"].snapshot()
        self.assertEqual(({row["developer"] for row in snapshot}, snapshot[0]["reason"], snapshot[0]["expires"]), ({"ana"}, "zona 3", 1600.0))

    def test_release_touch_and_job_ownership(self):
        ana, dan = self.join("ana"), self.join("dan")
        self.claim("ana", "job-a", "Workspace.Map.Zone3", "Lighting", "ServerStorage")
        self.claim("ana", "job-k", "Lighting", workspace="game:222")
        for body in ({"job_id": "job-a", "paths": "Lighting"}, {"job_id": "job-a", "paths": [1]}, {"job_id": "job-a", "paths": ["@nope"]}, {"paths": ["Lighting"]},
                     {"job_id": "job-a", "workspace": "x"}):
            with self.subTest(body=body), self.assertRaises(HubError) as error:
                self.hub.release(self.actor("ana"), body)
            self.assertEqual(error.exception.status, 400)
        # Jobul altui dispozitiv nu poate fi eliberat, atins sau extins.
        for call in (lambda: self.hub.release(self.actor("dan"), {"job_id": "job-a"}), lambda: self.hub.touch(self.actor("dan"), {"job_id": "job-a"}),
                     lambda: self.claim("dan", "job-a", "SoundService")):
            with self.assertRaises(HubError) as error:
                call()
            self.assertEqual((error.exception.status, str(error.exception)), (403, "Sesiunea aparține altui dispozitiv."))
        self.assertEqual(len(self.held()), 3)
        response = self.hub.release(self.actor("ana"), {"job_id": "job-a", "paths": ["workspace.Map.Zone3", "Absent"]})
        self.assertEqual(response, {"ok": True, "released": ["Workspace.Map.Zone3"], "held": ["Lighting", "ServerStorage"]})
        # Fără workspace, eliberarea și `held` acoperă toate workspace-urile jobului; cu workspace, doar pe acela.
        self.assertEqual(self.hub.touch(self.actor("ana"), {"job_id": "job-k"}), {"ok": True, "held": ["Lighting"]})
        self.assertEqual(self.hub.touch(self.actor("ana"), {"job_id": "job-k", "workspace": "game:987654"}), {"ok": True, "held": []})
        self.assertEqual(self.hub.release(self.actor("ana"), {"job_id": "job-k", "workspace": "game:222"}), {"ok": True, "released": ["Lighting"], "held": []})
        self.assertEqual(self.hub.release(self.actor("ana"), {"job_id": "job-a"}), {"ok": True, "released": ["Lighting", "ServerStorage"], "held": []})
        self.assertEqual(self.hub.release(self.actor("ana"), {"job_id": "job-a"}), {"ok": True, "released": [], "held": []})
        for body in ({}, {"job_id": 3}):
            with self.subTest(body=body), self.assertRaises(HubError):
                self.hub.touch(self.actor("ana"), body)
        self.claim("ana", "job-a", "Workspace.Map")
        self.now[0] = 1500.0
        self.assertEqual(self.hub.touch(self.actor("ana"), {"job_id": "job-a"}), {"ok": True, "held": ["Workspace.Map"]})
        self.now[0] = 2050.0
        # 550 s de la ultimul touch: încă ținută, iar touch-ul (cu workspace explicit) o prelungește din nou.
        self.assertEqual(self.hub.touch(self.actor("ana"), {"job_id": "job-a", "workspace": "game:987654"})["held"], ["Workspace.Map"])
        self.assertEqual(self.hub.claims["game:987654"].snapshot()[0]["expires"], 2650.0)
        self.now[0] = 2700.0
        # 650 s fără apeluri: sync-ul altui dispozitiv o expiră; touch întoarce lista rămasă, goală.
        self.assertEqual(self.sync("dan")["claims"], [])
        self.assertEqual(self.hub.touch(self.actor("ana"), {"job_id": "job-a"}), {"ok": True, "held": []})
        self.assertEqual(self.claim("dan", "job-b", "Workspace.Map")["claimed"], ["Workspace.Map"])

    def test_wait_reports_free_paths_and_wakes_on_release(self):
        self.join("ana"), self.join("dan")
        for body in ({}, {"path": 3}, {"path": "Lighting", "timeout_seconds": "5"}, {"path": "Lighting", "timeout_seconds": True}, {"path": "@bogus"},
                     {"path": "Lighting", "timeout_seconds": 0, "workspace": "x"}):
            with self.subTest(body=body), self.assertRaises(HubError) as error:
                self.hub.wait(self.actor("ana"), body)
            self.assertEqual(error.exception.status, 400)
        # wait nu revendică; se uită doar în workspace-ul curent (sau în cel din corp).
        self.assertEqual(self.hub.wait(self.actor("ana"), {"path": "Lighting"}), {"ok": True, "free": True})
        self.assertEqual(self.held(), [])
        self.claim("ana", "job-a", "Workspace.Map")
        self.assertEqual(self.hub.wait(self.actor("dan"), {"path": "game.Workspace.Map.Zone3", "timeout_seconds": 0}), {"ok": True, "free": False})
        self.assertEqual(self.hub.wait(self.actor("dan"), {"path": "Workspace", "timeout_seconds": -5}), {"ok": True, "free": False})
        self.assertEqual(self.hub.wait(self.actor("dan"), {"path": "Workspace", "timeout_seconds": 0, "workspace": "game:222"}), {"ok": True, "free": True})
        self.assertEqual(self.hub.wait(self.actor("dan"), {"path": "ServerStorage", "timeout_seconds": 0}), {"ok": True, "free": True})
        started = time.monotonic()
        with patch.object(team_hub, "MAX_WAIT", 0.05):
            self.assertEqual(self.hub.wait(self.actor("dan"), {"path": "Workspace.Map", "timeout_seconds": 500}), {"ok": True, "free": False})
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(team_hub.MAX_WAIT, 60)
        results = []
        waiter = threading.Thread(target=lambda: results.append(self.hub.wait(self.actor("dan"), {"path": "Workspace.Map.Zone3", "timeout_seconds": 5})))
        waiter.start()
        time.sleep(0.05)
        self.assertEqual(results, [])
        self.hub.release(self.actor("ana"), {"job_id": "job-a"})
        waiter.join(2)
        self.assertFalse(waiter.is_alive())
        self.assertEqual(results, [{"ok": True, "free": True}])


class OfflineTests(HubBase):
    def test_device_offline_after_60s_loses_claims_and_sessions_become_lost(self):
        ana, dan = self.join("ana"), self.join("dan")
        self.sync("ana", sessions=[session_row("job-a"), session_row("job-done", state="completed")])
        self.claim("ana", "job-a", "Workspace.Map")
        self.claim("ana", "job-solo", "Lighting")
        self.now[0] = 1059.0
        response = self.sync("dan")
        self.assertEqual([(row["job_id"], row["state"]) for row in response["sessions"]], [("job-a", "running"), ("job-done", "completed")])
        self.assertEqual([row["path"] for row in response["claims"]], ["Workspace.Map", "Lighting"])
        # ana nu mai este prezentă (> 15 s), dar nu este încă offline.
        self.assertEqual(self.present(response), [("dan", "pc-dan")])
        self.now[0] = 1060.0
        response = self.sync("dan")
        # Sesiunile neterminale devin `lost`, cele încheiate își păstrează starea; toate claims-urile ei (cu sau fără sesiune) sunt eliberate.
        self.assertEqual([(row["job_id"], row["state"]) for row in response["sessions"]], [("job-a", "lost"), ("job-done", "completed")])
        self.assertEqual(response["claims"], [])
        self.assertTrue(any(line.startswith("dispozitiv offline: ana @ pc-ana (" + ana + "); claims-urile lui au fost eliberate") for line in self.lines), self.lines)
        self.assertEqual(self.claim("dan", "job-b", "Workspace.Map")["claimed"], ["Workspace.Map"])
        self.assertEqual(self.hub.admin_devices(self.admin())["devices"][0]["online"], False)
        # Revenirea: sync-ul ei înlocuiește `lost` cu starea reală și o aduce online, fără un nou mesaj offline la următorul reap.
        self.now[0] = 1070.0
        back = self.sync("ana", sessions=[session_row("job-a")])
        self.assertEqual(self.present(back), [("ana", "pc-ana"), ("dan", "pc-dan")])
        self.assertEqual([(row["job_id"], row["state"]) for row in self.sync("dan")["sessions"]], [("job-a", "running"), ("job-done", "completed")])
        self.assertIsNone(self.hub.sessions["job-a"]["closed_at"])
        # Claims-urile eliberate nu revin singure.
        self.assertEqual([(row["path"], row["job_id"]) for row in self.sync("ana")["claims"]], [("Workspace.Map", "job-b")])
        with self.assertRaises(HubError) as error:
            self.claim("ana", "job-a", "Workspace.Map")
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(team_hub.OFFLINE_SECONDS, 60)

    def test_lost_sessions_expire_after_retention_and_the_device_stays_listed_offline(self):
        self.join("ana"), self.join("dan")
        self.sync("ana", sessions=[session_row("job-a")])
        self.now[0] = 1100.0
        self.assertEqual([row["state"] for row in self.sync("dan")["sessions"]], ["lost"])
        self.assertEqual(self.hub.sessions["job-a"]["closed_at"], 1100.0)
        self.now[0] = 2900.0
        self.assertEqual([row["state"] for row in self.sync("dan")["sessions"]], ["lost"])
        self.now[0] = 2901.0
        self.assertEqual(self.sync("dan")["sessions"], [])
        self.assertEqual([(row["roblox_name"], row["online"], row["status"]) for row in self.hub.admin_devices(self.admin())["devices"]],
                         [("ana", False, "approved"), ("dan", True, "approved")])
        self.assertEqual(sum(1 for line in self.lines if line.startswith("dispozitiv offline")), 1)


class QueryTests(HubBase):
    def setUp(self):
        super().setUp()
        self.ana, self.dan, self.eva = self.join("ana"), self.join("dan"), self.join("eva", KART)
        self.register("nou")
        self.sync("ana", sessions=[session_row("job-a", events=[event(1, "unu"), event(2, "doi")])], journal=[journal_item()])
        self.sync("eva", KART, sessions=[session_row("job-e", started=900.0)], journal=[journal_item("execute_luau", job_id="job-e")],
                  project_digest="snap-k", project=project("snap-k"))
        self.claim("ana", "job-a", "Workspace.Map")

    def test_workspace_detail_project_and_session(self):
        for key in (None, ""):
            with self.subTest(key=key), self.assertRaises(HubError) as error:
                self.hub.workspace(self.actor("ana"), key)
            self.assertEqual((error.exception.status, str(error.exception)), (400, "Parametrul key lipsește."))
        for key in ("game:1", "x", "local"):
            with self.subTest(key=key), self.assertRaises(HubError) as error:
                self.hub.workspace(self.actor("ana"), key)
            self.assertEqual((error.exception.status, str(error.exception)), (404, "Workspace-ul nu există."))
        detail = self.hub.workspace(self.actor("dan"), "game:987654")
        self.assertEqual(detail["workspace"], {**BALL, "first_seen": 1000.0, "last_seen": 1000.0})
        self.assertEqual(self.present(detail), [("ana", "pc-ana"), ("dan", "pc-dan")])
        self.assertEqual([(row["job_id"], row["developer"]) for row in detail["sessions"]], [("job-a", "ana")])
        self.assertEqual([(row["path"], row["workspace"]) for row in detail["claims"]], [("Workspace.Map", "game:987654")])
        self.assertEqual([(row["seq"], row["tool"]) for row in detail["journal"]], [(1, "multi_edit")])
        self.assertIsNone(detail["project"])
        kart = self.hub.workspace(self.actor("ana"), "game:222")
        self.assertEqual(kart["project"], {"digest": "snap-k", "snapshot_id": "snap-k", "count": 2, "truncated": False, "groups": {"assets": 1, "settings": 1},
                                           "reported_by": self.eva, "at": 1000.0})
        self.assertEqual(([row["job_id"] for row in kart["sessions"]], [row["seq"] for row in kart["journal"]]), (["job-e"], [2]))
        # Jurnalul workspace-ului: ultimele WORKSPACE_JOURNAL intrări.
        self.sync("ana", journal=[journal_item(tool="t" + str(index)) for index in range(5)])
        with patch.object(team_hub, "WORKSPACE_JOURNAL", 3):
            self.assertEqual([row["tool"] for row in self.hub.workspace(self.actor("ana"), "game:987654")["journal"]], ["t2", "t3", "t4"])
        self.assertEqual(team_hub.WORKSPACE_JOURNAL, 200)
        self.assertIsNone(self.hub.project(self.actor("ana"), "game:987654")["project"])
        self.assertEqual(self.hub.project(self.actor("ana"), "game:222")["project"]["reported_by"], self.eva)
        with self.assertRaises(HubError) as error:
            self.hub.project(self.actor("ana"), "game:1")
        self.assertEqual(error.exception.status, 404)
        # Fluxul unei sesiuni (pentru panou): meta + evenimentele de după `after`.
        session = self.hub.session(self.actor("dan"), "job-a")
        self.assertEqual((session["ok"], session["meta"]["job_id"], [item["seq"] for item in session["events"]], session["last_seq"]), (True, "job-a", [1, 2], 2))
        self.assertEqual([item["seq"] for item in self.hub.session(self.actor("dan"), "job-a", 1)["events"]], [2])
        self.assertEqual(self.hub.session(self.actor("dan"), "job-a", -3)["events"][0]["seq"], 1)
        for job in (None, "", "job-absent"):
            with self.subTest(job=job), self.assertRaises(HubError) as error:
                self.hub.session(self.actor("dan"), job)
            self.assertEqual(error.exception.status, 400 if not job else 404)
        self.assertEqual(str(error.exception), "Sesiunea nu există.")

    def test_panel_data_for_devices_and_for_the_admin_alone(self):
        panel = self.hub.panel_data(self.actor("ana"))
        self.assertEqual(panel["me"], {"device_id": self.ana, "status": "approved", "roblox_user_id": 101, "roblox_name": "ana", "machine": "pc-ana",
                                       "workspace": "game:987654", "admin": False})
        self.assertEqual(panel["hub"], {"hub_id": self.hub.hub_id, "version": "1.0.0", "enrollment": "approve", "now": 1000.0})
        # Sumarul: după `last_seen` descrescător, apoi după nume.
        self.assertEqual([row["key"] for row in panel["workspaces"]], ["game:987654", "game:222"])
        # Fără `workspace` se selectează workspace-ul curent al dispozitivului; necunoscut → null, nu 404.
        self.assertEqual((panel["selected"]["workspace"]["key"], [row["job_id"] for row in panel["selected"]["sessions"]]), ("game:987654", ["job-a"]))
        self.assertNotIn("pending_devices", panel)
        self.assertEqual(self.hub.panel_data(self.actor("ana"), "game:222")["selected"]["workspace"]["name"], "Kart")
        self.assertIsNone(self.hub.panel_data(self.actor("ana"), "game:1")["selected"])
        self.assertIsNone(self.hub.panel_data(self.actor("ana"), "rau")["selected"])
        self.sync("ana", None)
        self.assertIsNone(self.hub.panel_data(self.actor("ana"))["selected"])
        admin = self.hub.panel_data(self.admin())
        self.assertEqual(admin["me"], {"device_id": None, "status": "admin", "roblox_user_id": 0, "roblox_name": "admin", "machine": "",
                                       "workspace": None, "admin": True})
        self.assertEqual((admin["selected"], admin["pending_devices"]), (None, 1))
        self.assertEqual(self.hub.panel_data(self.admin(), "game:222")["selected"]["workspace"]["key"], "game:222")
        self.assertNotIn(TOKEN, json.dumps(admin))

    def test_admin_devices_are_sorted_without_the_token_hash(self):
        self.hub.revoke_device(self.admin(), {"device_id": self.dan})
        rows = self.hub.admin_devices(self.admin())["devices"]
        self.assertEqual([(row["roblox_name"], row["status"], row["online"]) for row in rows],
                         [("nou", "pending", False), ("ana", "approved", True), ("eva", "approved", True), ("dan", "revoked", False)])
        self.assertEqual(sorted(rows[1]), ["approved_at", "approved_by", "bridge_id", "device_id", "first_seen", "last_seen", "machine", "online",
                                           "roblox_name", "roblox_user_id", "status", "version", "workspace"])
        self.assertEqual((rows[1]["workspace"], rows[2]["workspace"], rows[0]["workspace"]), ("game:987654", "game:222", "game:987654"))
        for actor in (self.actor("ana"), Actor()):
            with self.subTest(actor=actor), self.assertRaises(HubError) as error:
                self.hub.admin_devices(actor)
            self.assertEqual((error.exception.status, str(error.exception)), (403, team_hub.ADMIN_REQUIRED))


class ServerBase(unittest.TestCase):
    """HubServer real pe port efemer; cererile pleacă cu antetele de dispozitiv/admin ale testului."""

    def setUp(self):
        self.now = [1000.0]
        self.lines = []
        self.fetches = []
        self.avatar_data = PNG
        self.hub = Hub(TOKEN, clock=lambda: self.now[0], logger=self.lines.append, avatar_fetcher=self.fetch)
        self.server = HubServer("127.0.0.1", 0, self.hub)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def fetch(self, user, size):
        self.fetches.append((user, size))
        if isinstance(self.avatar_data, Exception):
            raise self.avatar_data
        return self.avatar_data

    def raw(self, path, body=None, device=None, admin=None, headers=None, method=None):
        all_headers = {}
        if body is not None:
            all_headers["Content-Type"] = "application/json"
        if device is not None:
            all_headers[team_hub.DEVICE_HEADER] = device_token(device) if len(device) != 64 else device
        if admin is not None:
            all_headers[team_hub.ADMIN_HEADER] = admin
        all_headers.update(headers or {})
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        request = Request(self.base + path, data=data, headers=all_headers, method=method)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            return error.code, error.read(), error.headers

    def request(self, path, body=None, device=None, admin=None, headers=None, method=None):
        status, payload, response_headers = self.raw(path, body, device, admin, headers, method)
        return status, json.loads(payload), response_headers

    def register(self, name, workspace=BALL, admin=None):
        status, body, _ = self.request("/hub/register", {"roblox": {"user_id": USER_IDS.get(name, 1), "name": name}, "machine": "pc-" + name,
                                                          "bridge_id": "b", "version": "1.0.0", "workspace": workspace}, device=name, admin=admin)
        return status, body

    def approve(self, *names):
        for name in names:
            status, body, _ = self.request("/hub/admin/devices/approve", {"device_id": device_id(name)}, admin=TOKEN)
            self.assertEqual((status, body["device"]["status"]), (200, "approved"))


class HubServerTests(ServerBase):
    def test_authorization_matrix(self):
        # Fără antete: 401 necunoscut pe rutele de membru și pe /hub/status; /hub/register cere antetul dispozitivului.
        for path in ("/hub/status", "/hub/workspaces", "/hub/panel-data", "/hub/workspace?key=local", "/hub/session?job=x"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[:2], (401, {"ok": False, "error": team_hub.UNKNOWN_DEVICE, "status": "unknown"}))
        self.assertEqual(self.request("/hub/register", {"machine": "pc", "bridge_id": "b", "workspace": None})[:2],
                         (401, {"ok": False, "error": team_hub.UNKNOWN_DEVICE, "status": "unknown"}))
        # Doar cod de admin pe o rută care acționează ca dispozitiv: 400, antetul dispozitivului este obligatoriu.
        self.assertEqual(self.request("/hub/sync", {"workspace": None}, admin=TOKEN)[:2], (400, {"ok": False, "error": team_hub.DEVICE_REQUIRED}))
        self.assertEqual(self.request("/hub/register", {"machine": "pc", "bridge_id": "b", "workspace": None}, admin=TOKEN)[0], 400)
        for token in ("scurt", "z" * 64, device_token("necunoscut")):
            with self.subTest(token=token):
                status, body, _ = self.request("/hub/status", device=token)
                self.assertEqual((status, body["status"]), (401, "unknown"))
                self.assertEqual(self.request("/hub/sync", {"workspace": None}, device=token)[0], 401)
        for code in ("", "alt-cod-0123456789"):
            with self.subTest(code=code):
                self.assertEqual(self.request("/hub/status", admin=code)[:2], (401, {"ok": False, "error": team_hub.ADMIN_INVALID}))
        self.assertEqual(self.hub.devices, {})
        # Doar cod de admin: status cu device null, lista dispozitivelor; rutele admin fără cod → 403.
        status, body, _ = self.request("/hub/status", admin=TOKEN)
        self.assertEqual((status, body["admin"], body["device"], body["workspaces"], body["members_online"]), (200, True, None, 0, 0))
        self.assertEqual(self.request("/hub/admin/devices", admin=TOKEN)[:2], (200, {"ok": True, "devices": []}))
        self.assertEqual(self.request("/hub/admin/devices")[:2], (403, {"ok": False, "error": team_hub.ADMIN_REQUIRED}))
        self.assertEqual(self.request("/hub/admin/devices/approve", {"device_id": "x"}, device=device_token("ana"))[0], 401)
        # Înregistrare: 202 pending; apoi rutele de membru → 403 pending; admin-ul aprobă → 200.
        status, body = self.register("ana")
        self.assertEqual((status, body["status"], body["device_id"], body["hub_id"]), (202, "pending", device_id("ana"), self.hub.hub_id))
        self.assertEqual(self.request("/hub/admin/devices", device="ana")[:2], (403, {"ok": False, "error": team_hub.ADMIN_REQUIRED}))
        for path, body in (("/hub/workspaces", None), ("/hub/sync", {"workspace": None}), ("/hub/claims/claim", {"job_id": "j", "paths": ["Lighting"]})):
            with self.subTest(path=path):
                self.assertEqual(self.request(path, body, device="ana")[:2],
                                 (403, {"ok": False, "error": team_hub.PENDING_DEVICE, "status": "pending", "device_id": device_id("ana")}))
        status, body, _ = self.request("/hub/status", device="ana")
        self.assertEqual((status, body["device"]["status"], body["admin"]), (200, "pending", False))
        # Ambele antete: contextul dispozitivului cu privilegii de admin.
        status, body, _ = self.request("/hub/panel-data", device="ana", admin=TOKEN)
        self.assertEqual((status, body["me"]["device_id"], body["me"]["status"], body["me"]["admin"], body["pending_devices"]), (200, device_id("ana"), "pending", True, 1))
        self.approve("ana")
        self.assertEqual(self.register("ana")[0], 200)
        status, body, _ = self.request("/hub/workspaces", device="ana")
        self.assertEqual((status, body["ok"], [row["key"] for row in body["workspaces"]]), (200, True, ["game:987654"]))
        # Revocare: register/sync 403 revoked, status 200 cu starea.
        self.assertEqual(self.request("/hub/admin/devices/revoke", {"device_id": device_id("ana")}, admin=TOKEN)[1]["device"]["status"], "revoked")
        status, body = self.register("ana")
        self.assertEqual((status, body), (403, {"ok": False, "error": team_hub.REVOKED_DEVICE, "status": "revoked", "device_id": device_id("ana")}))
        self.assertEqual(self.request("/hub/sync", {"workspace": None}, device="ana")[1]["status"], "revoked")
        self.assertEqual(self.request("/hub/status", device="ana")[1]["device"]["status"], "revoked")
        self.assertEqual(self.request("/hub/admin/devices/forget", {"device_id": device_id("ana")}, admin=TOKEN)[:2], (200, {"ok": True}))
        self.assertEqual(self.request("/hub/status", device="ana")[0], 401)
        self.assertEqual(self.request("/hub/admin/enrollment", {"mode": "open"}, admin=TOKEN)[1], {"ok": True, "enrollment": "open"})
        self.assertEqual(self.register("ana")[0], 200)
        self.assertNotIn(device_token("ana"), "\n".join(self.lines))
        self.assertNotIn(TOKEN, "\n".join(self.lines))

    def test_wrong_admin_codes_are_rate_limited_per_client(self):
        """Singurul secret ghicibil este codul de administrator: după 10 încercări greșite într-un minut, clientul primește 429."""
        for _ in range(team_hub.AUTH_FAIL_LIMIT):
            self.assertEqual(self.request("/hub/status", admin="cod-gresit-0123456789")[0], 401)
        status, body, _ = self.request("/hub/status", admin="cod-gresit-0123456789")
        self.assertEqual((status, body), (429, {"ok": False, "error": team_hub.TOO_MANY_ATTEMPTS}))
        # Blocarea ține și pentru codul corect, dar atinge doar cererile cu cod de admin: în spatele unui proxy toate
        # dispozitivele împart aceeași adresă, deci traficul lor nu trebuie oprit de codurile greșite ale altcuiva.
        self.assertEqual(self.request("/hub/status", admin=TOKEN)[0], 429)
        self.assertEqual(self.request("/hub/status")[0], 401)
        self.assertEqual(self.register("ana")[0], 202)
        self.assertEqual(self.request("/hub/status", device="ana")[0], 200)
        self.assertTrue(any(line.startswith("prea multe coduri de administrator greșite de la 127.0.0.1") for line in self.lines), self.lines)
        self.assertNotIn("cod-gresit", "\n".join(self.lines))
        # Fereastra este glisantă: după un minut cererile trec din nou.
        self.now[0] += team_hub.AUTH_FAIL_WINDOW + 1
        self.assertEqual(self.request("/hub/status", admin=TOKEN)[0], 200)

    def test_a_client_that_drops_the_connection_does_not_print_a_traceback(self):
        """O filă de panou închisă în timpul polling-ului rupe conexiunea înainte de `handle_one_request`: fără stive în jurnal."""
        def report(error):
            stream = io.StringIO()
            try:
                raise error
            except type(error):
                with contextlib.redirect_stderr(stream):
                    self.server.handle_error(None, ("127.0.0.1", 1))
            return stream.getvalue()

        for error in (ConnectionResetError("ruptă"), BrokenPipeError("ruptă"), ConnectionAbortedError("ruptă"), TimeoutError("expirat")):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(report(error), "")
        # Orice altceva rămâne vizibil: tăcerea este doar pentru conexiunile rupte de client.
        self.assertIn("ValueError", report(ValueError("altceva")))

    def test_origin_options_and_team_routes(self):
        for origin in ("https://example.com", "null"):
            with self.subTest(origin=origin):
                status, body, _ = self.request("/hub/status", admin=TOKEN, headers={"Origin": origin})
                self.assertEqual((status, body["ok"]), (403, False))
        self.assertEqual(self.request("/hub/register", {"machine": "pc", "bridge_id": "b"}, device="ana", headers={"Origin": "http://x"})[0], 403)
        self.assertEqual(self.request("/hub/status", method="OPTIONS")[0], 403)
        self.assertEqual(self.request("/healthz", headers={"Origin": "https://example.com"})[0], 403)
        # Same-origin (panoul) este acceptat.
        self.assertEqual(self.request("/hub/status", admin=TOKEN, headers={"Origin": self.base})[0], 200)
        self.assertEqual(self.hub.devices, {})
        # Rutele 0.8 /team/* au dispărut (404, indiferent de antete), ca și metodele greșite.
        for path, body, method, kwargs in (("/team/status", None, None, {}), ("/team/status", None, None, {"admin": TOKEN}), ("/team/register", {"developer": "ana"}, None, {}),
                                           ("/team/sync", {}, None, {"admin": TOKEN}), ("/hub/unknown", None, None, {"admin": TOKEN}), ("/hub/status", {}, "POST", {"admin": TOKEN}),
                                           ("/hub/register", None, "GET", {"admin": TOKEN}), ("/", {}, "POST", {}), ("/hub/claims/steal", {}, None, {"admin": TOKEN}),
                                           ("/hub/admin/nothing", None, None, {"admin": TOKEN}), ("/hub/status/../register", None, None, {})):
            with self.subTest(path=path, method=method):
                status, body, _ = self.request(path, body, method=method, **kwargs)
                self.assertEqual((status, body), (404, {"ok": False, "error": "Rută inexistentă."}))

    def test_post_requires_a_json_object(self):
        self.assertEqual(self.request("/hub/register", {"machine": "pc"}, device="ana", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("/hub/register", b"nu-e-json", device="ana")[0], 400)
        self.assertEqual(self.request("/hub/register", [1, 2], device="ana")[0], 400)
        self.assertEqual(self.request("/hub/register", {"machine": "pc"}, device="ana")[0], 400)
        self.assertEqual(self.hub.devices, {})

    def test_early_refusals_read_the_request_body_before_answering(self):
        # Refuzul dat înainte de `_body()` consumă corpul, ca răspunsul JSON să nu se piardă pe Windows.
        admin = {team_hub.ADMIN_HEADER: TOKEN}
        device = {team_hub.DEVICE_HEADER: device_token("ana")}
        for path, headers, expected in (("/hub/sync", {}, 401), ("/hub/sync", device, 401), ("/hub/sync", {team_hub.ADMIN_HEADER: "alt"}, 401),
                                        ("/hub/sync", {**admin, "Origin": "http://x"}, 403), ("/hub/nothing", admin, 404), ("/hub/sync", admin, 400),
                                        ("/hub/admin/devices/approve", device, 401), ("/hub/sync", {**admin, "Content-Type": "text/plain"}, 415)):
            with self.subTest(path=path, headers=headers):
                early, status, body = post_in_two_parts(self.base, path, {"workspace": None}, headers)
                self.assertFalse(early)
                self.assertEqual((status, body["ok"]), (expected, False))

    def test_register_sync_claims_and_queries_over_http(self):
        status, body, headers = self.request("/healthz")
        self.assertEqual((status, body, headers["Content-Type"]), (200, {"ok": True, "version": "1.0.0"}, "application/json; charset=utf-8"))
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(headers["Server"], "StudioHarnessHub/1.0")
        self.assertEqual((self.register("ana")[0], self.register("dan")[0], self.register("eva", KART)[0]), (202, 202, 202))
        self.approve("ana", "dan", "eva")
        status, body, headers = self.request("/hub/sync", {"workspace": BALL, "sessions": [session_row("job-a", events=[event(1)])],
                                                            "journal": [journal_item()]}, device="ana")
        self.assertEqual((status, body["ok"], body["hub_id"], body["sessions"], body["journal_seq"], headers["Cache-Control"]), (200, True, self.hub.hub_id, [], 1, "no-store"))
        status, body, _ = self.request("/hub/claims/claim", {"job_id": "job-a", "paths": ["ServerScriptService.Main"], "reason": "r"}, device="ana")
        self.assertEqual((status, body), (200, {"ok": True, "claimed": ["ServerScriptService.Main"], "held": ["ServerScriptService.Main"]}))
        status, body, _ = self.request("/hub/claims/claim", {"job_id": "job-b", "paths": ["ServerScriptService"]}, device="dan")
        self.assertEqual((status, body["ok"], body["error"]), (409, False, "Conflict de claim."))
        self.assertEqual((body["conflicts"][0]["developer"], body["conflicts"][0]["holder"], body["conflicts"][0]["held_path"]), ("ana", "job-a", "ServerScriptService.Main"))
        # Același drum în alt workspace este liber; eva nu vede sesiunea lui ana.
        self.assertEqual(self.request("/hub/claims/claim", {"job_id": "job-e", "paths": ["ServerScriptService"]}, device="eva")[0], 200)
        status, body, _ = self.request("/hub/sync", {"workspace": KART, "want": {"job-a": 0}}, device="eva")
        self.assertEqual((status, body["workspace"], body["sessions"], body["events"], [row["path"] for row in body["claims"]]), (200, "game:222", [], {}, ["ServerScriptService"]))
        status, body, _ = self.request("/hub/sync", {"workspace": BALL, "want": {"job-a": 0}}, device="dan")
        self.assertEqual((status, [row["job_id"] for row in body["sessions"]], body["events"]["job-a"][0]["seq"]), (200, ["job-a"], 1))
        self.assertEqual([(row["path"], row["workspace"]) for row in body["claims"]], [("ServerScriptService.Main", "game:987654")])
        self.assertEqual({row["roblox_name"] for row in body["members"]}, {"ana", "dan"})
        self.assertEqual([(row["key"], row["claims"], len(row["members_online"])) for row in body["workspaces"]], [("game:987654", 1, 2), ("game:222", 1, 1)])
        self.assertEqual(self.request("/hub/claims/touch", {"job_id": "job-a"}, device="ana")[1], {"ok": True, "held": ["ServerScriptService.Main"]})
        self.assertEqual(self.request("/hub/claims/wait", {"path": "ServerScriptService", "timeout_seconds": 0}, device="dan")[1], {"ok": True, "free": False})
        self.assertEqual(self.request("/hub/claims/wait", {"path": "ServerScriptService", "timeout_seconds": 0, "workspace": "game:222"}, device="dan")[1], {"ok": True, "free": False})
        self.assertEqual(self.request("/hub/claims/wait", {"path": "Lighting", "timeout_seconds": 0}, device="dan")[1], {"ok": True, "free": True})
        self.assertEqual(self.request("/hub/claims/release", {"job_id": "job-a"}, device="ana")[1], {"ok": True, "released": ["ServerScriptService.Main"], "held": []})
        self.assertEqual(self.request("/hub/claims/release", {"job_id": "job-a"}, device="dan")[0], 403)
        self.assertEqual(self.request("/hub/claims/claim", {"job_id": "job-a", "paths": ["@bogus"]}, device="ana")[0], 400)
        # Interogările de membru (și adminul le poate face fără dispozitiv).
        status, body, _ = self.request("/hub/workspaces", device="dan")
        self.assertEqual((status, [row["key"] for row in body["workspaces"]]), (200, ["game:987654", "game:222"]))
        self.assertEqual(self.request("/hub/workspaces", admin=TOKEN)[1], body)
        self.assertEqual(self.request("/hub/workspace")[0], 401)
        self.assertEqual(self.request("/hub/workspace", admin=TOKEN)[:2], (400, {"ok": False, "error": "Parametrul key lipsește."}))
        self.assertEqual(self.request("/hub/workspace?key=game:1", admin=TOKEN)[:2], (404, {"ok": False, "error": "Workspace-ul nu există."}))
        status, body, _ = self.request("/hub/workspace?key=game:987654", device="dan")
        self.assertEqual((status, body["ok"], body["workspace"]["name"], [row["job_id"] for row in body["sessions"]], [row["seq"] for row in body["journal"]]),
                         (200, True, "Ball", ["job-a"], [1]))
        self.assertEqual(self.request("/hub/project?key=game:987654", device="dan")[1], {"ok": True, "project": None})
        self.assertEqual(self.request("/hub/project?key=game:9", device="dan")[0], 404)
        status, body, _ = self.request("/hub/panel-data?workspace=game:987654", device="dan")
        self.assertEqual((status, body["me"]["roblox_name"], body["selected"]["workspace"]["key"], "pending_devices" in body), (200, "dan", "game:987654", False))
        self.assertEqual(self.request("/hub/panel-data", device="dan")[1]["selected"]["workspace"]["key"], "game:987654")
        self.assertIsNone(self.request("/hub/panel-data?workspace=nimic", device="dan")[1]["selected"])
        status, body, _ = self.request("/hub/panel-data?workspace=game:222", admin=TOKEN)
        self.assertEqual((body["me"]["status"], body["selected"]["workspace"]["name"], body["pending_devices"]), ("admin", "Kart", 0))
        status, body, _ = self.request("/hub/session?job=job-a", device="dan")
        self.assertEqual((status, body["meta"]["job_id"], [item["seq"] for item in body["events"]], body["last_seq"]), (200, "job-a", [1], 1))
        self.assertEqual(self.request("/hub/session?job=job-a&after=1", device="dan")[1]["events"], [])
        self.assertEqual(self.request("/hub/session?job=job-a&after=x", device="dan")[1]["events"][0]["seq"], 1)
        self.assertEqual(self.request("/hub/session?job=absent", device="dan")[:2], (404, {"ok": False, "error": "Sesiunea nu există."}))
        self.assertEqual(self.request("/hub/session", device="dan")[0], 400)
        # Prefixul public merge identic.
        self.assertEqual(self.request("/roblox/harness/hub/status", device="dan")[0], 200)
        self.assertEqual(self.request("/roblox/harness/hub/status")[0], 401)
        self.assertNotIn(device_token("ana"), json.dumps(self.hub.export()))


class AvatarTests(ServerBase):
    def setUp(self):
        super().setUp()
        # Ruta este publică, dar servește doar conturile Roblox raportate de dispozitive: înregistrăm două.
        self.request("/hub/register", {"roblox": {"user_id": 12345, "name": "ana"}, "machine": "pc-ana", "bridge_id": "b",
                                       "version": "1.0.0", "workspace": BALL}, device="ana")
        self.request("/hub/register", {"roblox": {"user_id": 777, "name": "dan"}, "machine": "pc-dan", "bridge_id": "b",
                                       "version": "1.0.0", "workspace": BALL}, device="dan")

    def test_avatar_is_public_cached_and_falls_back_to_204(self):
        status, data, headers = self.raw("/hub/avatar?user=12345")
        self.assertEqual((status, data, headers["Content-Type"], headers["Cache-Control"], headers["X-Content-Type-Options"]),
                         (200, PNG, "image/png", "public, max-age=3600", "nosniff"))
        self.assertEqual(headers["Content-Length"], str(len(PNG)))
        self.assertEqual(self.fetches, [(12345, 48)])
        # Cache 1 h per (user, size): al doilea apel nu mai atinge fetcher-ul; altă dimensiune da.
        self.assertEqual(self.raw("/hub/avatar?user=12345&size=48")[0], 200)
        self.assertEqual(self.fetches, [(12345, 48)])
        self.assertEqual(self.raw("/hub/avatar?user=12345&size=100")[0], 200)
        self.assertEqual(self.fetches, [(12345, 48), (12345, 100)])
        self.now[0] = 4599.0
        self.raw("/hub/avatar?user=12345")
        self.assertEqual(len(self.fetches), 2)
        self.now[0] = 4601.0
        self.raw("/hub/avatar?user=12345")
        self.assertEqual(len(self.fetches), 3)
        self.assertEqual(self.raw("/roblox/harness/hub/avatar?user=12345&size=60")[0], 200)
        # Parametri invalizi → 400; alte metode → 404; Origin străin → 403.
        for query in ("", "?user=0", "?user=-3", "?user=abc", "?user=1&size=50", "?user=1&size=x", "?user=" + str(2 ** 53), "?user=1.5"):
            with self.subTest(query=query):
                status, data, _ = self.raw("/hub/avatar" + query)
                self.assertEqual((status, json.loads(data)["ok"]), (400, False))
        self.assertEqual(self.raw("/hub/avatar?user=1", body={}, method="POST")[0], 404)
        self.assertEqual(self.raw("/hub/avatar?user=1", headers={"Origin": "https://x"})[0], 403)
        self.assertEqual(len(self.fetches), 4)
        # Un cont necunoscut hub-ului nu declanșează nicio cerere ieșită: 204 direct (fără el, ruta publică ar fi un amplificator).
        self.assertEqual(self.raw("/hub/avatar?user=424242")[0], 204)
        self.assertEqual(len(self.fetches), 4)
        # Eșec (None sau excepție) → 204 fără corp, reținut 60 s, apoi se reîncearcă; conținutul non-PNG este tot eșec.
        self.avatar_data = None
        status, data, headers = self.raw("/hub/avatar?user=777")
        self.assertEqual((status, data, headers["Cache-Control"], headers.get("Content-Type")), (204, b"", "no-store", None))
        self.assertEqual(self.raw("/hub/avatar?user=777")[0], 204)
        self.assertEqual(self.fetches[-1], (777, 48))
        self.assertEqual(self.fetches.count((777, 48)), 1)
        self.now[0] = 4662.0
        self.avatar_data = URLError("fără rețea")
        self.assertEqual(self.raw("/hub/avatar?user=777")[0], 204)
        self.assertEqual(self.fetches.count((777, 48)), 2)
        self.assertTrue(any(line == "avatar indisponibil pentru utilizatorul 777: URLError" for line in self.lines), self.lines)
        self.now[0] = 4723.0
        self.avatar_data = b"<html>nu e png</html>"
        self.assertEqual(self.raw("/hub/avatar?user=777")[0], 204)
        self.avatar_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * team_hub.AVATAR_MAX_BYTES
        self.now[0] = 4784.0
        self.assertEqual(self.raw("/hub/avatar?user=777")[0], 204)
        self.now[0] = 4845.0
        self.avatar_data = PNG
        self.assertEqual(self.raw("/hub/avatar?user=777")[:2], (200, PNG))
        self.assertEqual((team_hub.AVATAR_CACHE_SECONDS, team_hub.AVATAR_RETRY_SECONDS, team_hub.AVATAR_TIMEOUT, team_hub.AVATAR_MAX_BYTES, team_hub.AVATAR_SIZES),
                         (3600, 60, 5, 512 * 1024, (48, 60, 100)))

    def test_the_cache_has_a_real_ceiling_and_drops_the_oldest_entries(self):
        """Plafonul nu este doar curățarea expirărilor: intrările proaspete trăiesc o oră, deci fără el dicționarul ar crește la nesfârșit."""
        self.hub.devices[device_id("ana")]["roblox_user_id"] = 0
        self.hub.avatars = {(user, 48): (self.now[0] + 3600, PNG) for user in range(1, team_hub.AVATAR_CACHE_ENTRIES + 1)}
        self.hub.devices[device_id("ana")]["roblox_user_id"] = 12345
        self.assertEqual(self.raw("/hub/avatar?user=12345")[0], 200)
        self.assertLessEqual(len(self.hub.avatars), team_hub.AVATAR_CACHE_ENTRIES)
        self.assertIn((12345, 48), self.hub.avatars)

    def test_default_fetcher_parses_the_thumbnail_api_without_the_network(self):
        calls = []

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def read(self, limit=None):
                return self.payload

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        def fake_urlopen(request, timeout=None):
            calls.append((request.full_url, timeout))
            if request.full_url.startswith(team_hub.AVATAR_API):
                return Response(json.dumps({"data": [{"targetId": 5, "state": "Completed", "imageUrl": "https://tr.rbxcdn.com/x/48/48/AvatarHeadshot/Png"}]}).encode())
            return Response(PNG)

        def opener(call):
            return type("Opener", (), {"open": staticmethod(call)})

        with patch.object(team_hub, "_OPENER", opener(fake_urlopen)):
            self.assertEqual(team_hub.fetch_avatar(5, 48), PNG)
            self.assertEqual(calls, [(team_hub.AVATAR_API + "?userIds=5&size=48x48&format=Png&isCircular=false", 5), ("https://tr.rbxcdn.com/x/48/48/AvatarHeadshot/Png", 5)])
        # Doar gazdele Roblox: orice altă țintă (sau http) este refuzată fără a doua cerere.
        for image in ("http://nesigur", "https://127.0.0.1/intern.png", "https://evil.example/x.png", "https://tr.rbxcdn.com.evil/x.png"):
            with self.subTest(image=image):
                with patch.object(team_hub, "_OPENER", opener(lambda request, timeout=None, url=image: Response(json.dumps({"data": [{"imageUrl": url}]}).encode()))):
                    self.assertIsNone(team_hub.fetch_avatar(5, 48))
        with patch.object(team_hub, "_OPENER", opener(lambda request, timeout=None: Response(b"{}"))):
            self.assertIsNone(team_hub.fetch_avatar(5, 48))
        # Redirectările nu sunt urmate: un 302 ar deveni o cerere a hub-ului către altă gazdă.
        self.assertIsNone(team_hub._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.example/x"))
        self.assertIs(Hub(TOKEN).avatar_fetcher, team_hub.fetch_avatar)


class ReleasesRouteTests(unittest.TestCase):
    """Canalul de actualizare servit de hub la GET /releases/<nume>, fără antete: doar fișiere simple din `releases/`."""

    def setUp(self):
        self.temp = temp_dir()
        self.app = Path(self.temp.name) / "app"
        self.releases = self.app / "releases"
        self.releases.mkdir(parents=True)
        self.files = {"manifest.json": b'{"version": "9.9.9", "files": {}}\n',
                      "studio-harness-hub-9.9.9-ubuntu.zip": make_zip({"scripts/team_hub.py": b"# hub\n"}, top="studio-harness-hub-9.9.9"),
                      "studio-harness-hub-9.9.9-ubuntu.zip.sha256": b"abc  studio-harness-hub-9.9.9-ubuntu.zip\n",
                      "NOTES.txt": "note cu diacritice ș\n".encode("utf-8"),
                      "secret.py": b"TOKEN = 'nu se serveste'\n", "a..b.zip": b"PK-nu"}
        for name, data in self.files.items():
            (self.releases / name).write_bytes(data)
        (self.app / "team_hub.py").write_bytes(b"# aplicatia, nu se serveste\n")
        self.hub = Hub(TOKEN, app_dir=self.app)
        self.server = HubServer("127.0.0.1", 0, self.hub)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def get(self, path, headers=None, method="GET"):
        try:
            with urlopen(Request(self.base + path, headers=headers or {}, method=method), timeout=3) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            return error.code, error.read(), error.headers

    def test_release_files_are_served_without_headers_with_the_right_types(self):
        cases = (("manifest.json", "application/json; charset=utf-8", "no-cache"),
                 ("studio-harness-hub-9.9.9-ubuntu.zip", "application/zip", "public, max-age=3600"),
                 ("studio-harness-hub-9.9.9-ubuntu.zip.sha256", "text/plain; charset=utf-8", "public, max-age=3600"),
                 ("NOTES.txt", "text/plain; charset=utf-8", "public, max-age=3600"))
        for name, content_type, cache in cases:
            with self.subTest(name=name):
                status, body, headers = self.get("/releases/" + name)
                self.assertEqual((status, body), (200, self.files[name]))
                self.assertEqual((headers["Content-Type"], headers["Cache-Control"], headers["X-Content-Type-Options"]), (content_type, cache, "nosniff"))
                self.assertEqual(headers["Content-Length"], str(len(self.files[name])))
        # Pe lostcube.pro hub-ul stă sub /roblox/harness/; rutele merg și cu prefix, pentru proxy-uri care nu îl scot.
        admin = {team_hub.ADMIN_HEADER: TOKEN}
        self.assertEqual(self.get("/roblox/harness/releases/manifest.json")[:2], (200, self.files["manifest.json"]))
        self.assertEqual(self.get("/roblox/harness/healthz")[0], 200)
        self.assertEqual(self.get("/roblox/harness/hub/status")[0], 401)
        self.assertEqual(self.get("/roblox/harness/hub/status", admin)[0], 200)
        self.assertEqual(self.get("/roblox/harness/panel")[0], self.get("/panel")[0])
        # Prefixul nu deschide altceva: /roblox/harnessx sau /roblox/harness/../ nu sunt rute (404, cu sau fără cod).
        for path in ("/roblox/harnessx/healthz", "/roblox/harness/..%2Fhealthz", "/roblox/harness/roblox/harness/healthz"):
            self.assertEqual(self.get(path)[0], 404, path)
            self.assertEqual(self.get(path, admin)[0], 404, path)
        # Rădăcina duce la panou (302, fără antete): relativ când proxy-ul a scos prefixul, absolut pentru /roblox/harness fără bară.
        for given, location in (("/", "panel"), ("/roblox/harness/", "panel"), ("/roblox/harness", "/roblox/harness/panel")):
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=3)
            try:
                connection.request("GET", given)
                response = connection.getresponse()
                self.assertEqual((response.status, response.getheader("Location"), response.read()), (302, location, b""), given)
            finally:
                connection.close()
        self.assertEqual(self.get("/", method="POST")[0], 404)
        for given, expected in (("/roblox/harness", "/"), ("/roblox/harness/", "/"), ("/roblox/harness/hub/status", "/hub/status"),
                                ("/hub/status", "/hub/status"), ("/roblox/harnessx", "/roblox/harnessx"), ("/", "/")):
            self.assertEqual(team_hub.strip_base(given), expected, given)
        # Antetele nu sunt cerute (dar nici nu strică); query-ul este ignorat; restul hub-ului rămâne protejat.
        self.assertEqual(self.get("/releases/manifest.json", admin)[0], 200)
        self.assertEqual(self.get("/releases/manifest.json?x=1")[:2], (200, self.files["manifest.json"]))
        self.assertEqual(self.get("/hub/status")[0], 401)
        # Updater-ul real citește manifestul de aici ca de pe orice canal loopback.
        with self.assertRaisesRegex(updater.UpdateError, "nu conține fișiere"):
            updater.fetch_manifest(self.base + "/releases/manifest.json", timeout=3)

    def test_panel_is_served_from_disk_with_the_csp_headers(self):
        self.assertEqual(self.get("/panel")[0], 404)
        (self.app / "panel").mkdir()
        (self.app / "panel" / "index.html").write_text("<!doctype html><title>Panou ș</title>", encoding="utf-8")
        status, body, headers = self.get("/panel")
        self.assertEqual((status, body.decode("utf-8"), headers["Content-Type"]), (200, "<!doctype html><title>Panou ș</title>", "text/html; charset=utf-8"))
        for name, value in team_hub.PANEL_HEADERS:
            self.assertEqual(headers[name], value)
        for directive in ("img-src 'self' data:", "connect-src 'self'", "frame-ancestors 'none'", "base-uri 'none'", "form-action 'none'"):
            self.assertIn(directive, headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(self.get("/panel/")[0], 200)
        self.assertEqual(self.get("/roblox/harness/panel")[0], 200)
        # Panoul real din repo se servește când hub-ul rulează din checkout.
        self.assertTrue(team_hub.panel_file(ROOT / "scripts").is_file())
        self.assertEqual(Hub(TOKEN).panel_file(), ROOT / "panel" / "index.html")

    def test_unknown_or_unsafe_names_are_404_and_only_get_without_origin_is_public(self):
        for path in ("/releases/", "/releases/missing.zip", "/releases/secret.py", "/releases/a..b.zip", "/releases/../team_hub.py",
                     "/releases/..%2Fteam_hub.py", "/releases/sub/manifest.json", "/releases/manifest.json/", "/releases/" + "x" * 121 + ".zip",
                     "/releases/manifest.json.", "/releases/manifest"):
            with self.subTest(path=path):
                status, body, _ = self.get(path)
                self.assertEqual((status, json.loads(body)), (404, {"ok": False, "error": "Rută inexistentă."}), path)
        self.assertEqual(self.get("/releases")[0], 404)
        self.assertEqual(self.get("/releases/manifest.json", method="POST")[0], 404)
        self.assertEqual(self.get("/releases/manifest.json", {"Origin": "https://example.com"})[0], 403)
        # Fără director releases/ lângă aplicație (sau lângă părintele ei) totul este 404.
        self.hub.app_dir = Path(self.temp.name) / "gol" / "app"
        self.assertEqual(self.get("/releases/manifest.json")[0], 404)

    def test_releases_dir_prefers_the_app_folder_then_its_parent(self):
        self.assertEqual(team_hub.releases_dir(self.app), self.releases)
        other = Path(self.temp.name) / "other" / "app"
        other.mkdir(parents=True)
        self.assertEqual(team_hub.releases_dir(other), other / "releases")
        (other.parent / "releases").mkdir()
        self.assertEqual(team_hub.releases_dir(other), other.parent / "releases")
        (other / "releases").mkdir()
        self.assertEqual(team_hub.releases_dir(other), other / "releases")
        # Implicit: lângă team_hub.py (hub găzduit) sau în rădăcina repo-ului (checkout).
        self.assertIn(team_hub.releases_dir(), (ROOT / "scripts" / "releases", ROOT / "releases"))


class RecordingThread(threading.Thread):
    """Fir care nu pornește: main() înregistrează firul de update fără să îl ruleze (ar dormi 30 s, apoi ar atinge canalul)."""

    instances = []

    def start(self):
        RecordingThread.instances.append(self)


@contextlib.contextmanager
def running_main(state, app, lines, *flags, environ=None, serve=None, shutdowns=None, port=None, real_threads=False, listen="127.0.0.1"):
    """Rulează main() cu serve_forever înlocuit (implicit: nu face nimic) și cu firele înregistrate, nu pornite (`real_threads` le lasă reale).

    Codul de admin vine din STUDIO_HARNESS_ADMIN_TOKEN (TOKEN) dacă `environ` nu îl suprascrie; `serve` primește serverul.
    `listen=None` omite cu totul `--listen`, ca să se vadă adresa implicită a hub-ului."""
    servers = []

    def serve_forever(server, poll_interval=0.5):
        servers.append(server)
        if serve:
            serve(server)

    address = ["--listen", listen] if listen is not None else []
    argv = ["team_hub.py", *address, "--port", str(port or free_port()), "--state-dir", str(state), *flags]
    env = {"STUDIO_HARNESS_ADMIN_TOKEN": TOKEN, "STUDIO_HARNESS_UPDATE_URL": "", "STUDIO_HARNESS_AUTO_UPDATE": "1", "STUDIO_HARNESS_OPEN_ENROLLMENT": ""}
    env.update(environ or {})
    RecordingThread.instances = []
    with patch.object(sys, "argv", argv), patch.dict(os.environ, env), patch.object(team_hub, "__file__", str(app / "team_hub.py")), \
            patch.object(team_hub, "log", lines.append), patch.object(team_hub.signal, "signal", lambda *_: None), \
            patch.object(HubServer, "serve_forever", serve_forever), \
            patch.object(HubServer, "shutdown", lambda server: shutdowns.append(server) if shutdowns is not None else None):
        # Firele sunt doar înregistrate cât rulează main(); în interiorul blocului (bucla de update, canalul local) firele sunt reale,
        # iar jurnalul și oprirea serverului rămân interceptate.
        with patch.object(team_hub.threading, "Thread", threading.Thread if real_threads else RecordingThread):
            code = team_hub.main()
        yield code, servers


class HubCliTests(unittest.TestCase):
    """main(): codul de admin (fișier / env / --show-admin-code), înrolarea, --approve-pending prin hub-ul în execuție sau prin fișier."""

    def setUp(self):
        self.temp = temp_dir()
        self.app = Path(self.temp.name) / "app"
        self.app.mkdir()
        (self.app / "team_hub.py").write_bytes(b"# hub\n")
        self.state = Path(self.temp.name) / "state"
        self.lines = []

    def tearDown(self):
        self.temp.cleanup()

    def run_main(self, *flags, **kwargs):
        with running_main(self.state, self.app, self.lines, "--no-auto-update", *flags, **kwargs) as (code, servers):
            pass
        return code, servers

    def test_admin_code_comes_from_the_file_or_the_environment_and_is_logged_only_on_request(self):
        code, servers = self.run_main(environ={"STUDIO_HARNESS_ADMIN_TOKEN": ""})
        self.assertEqual((code, len(servers)), (0, 1))
        path = self.state / "hub-admin-token"
        self.assertTrue(path.is_file())
        generated = path.read_text(encoding="utf-8").strip()
        self.assertGreaterEqual(len(generated), 16)
        self.assertEqual(servers[0].hub.admin_code, generated)
        self.assertTrue(any(line == "Codul de administrator: " + str(path) + " (pornește cu --show-admin-code ca să îl afișezi)" for line in self.lines), self.lines)
        self.assertTrue(any(line.startswith("Studio Harness hub 1.0.0 ascultă pe http://127.0.0.1:") for line in self.lines), self.lines)
        self.assertTrue(any(line.startswith("înrolare: approve") for line in self.lines), self.lines)
        self.assertNotIn(generated, "\n".join(self.lines))
        self.assertNotIn("team.json", "\n".join(self.lines))
        # Același fișier la a doua pornire; --show-admin-code îl afișează o dată.
        self.lines.clear()
        code, servers = self.run_main("--show-admin-code", environ={"STUDIO_HARNESS_ADMIN_TOKEN": ""})
        self.assertEqual((path.read_text(encoding="utf-8").strip(), servers[0].hub.admin_code), (generated, generated))
        self.assertTrue(any(line == "Cod de administrator (pentru panou și --approve-pending): " + generated for line in self.lines), self.lines)
        # Din env: fișierul nu se creează, log-ul spune sursa; un cod scurt este refuzat înainte de pornire.
        self.lines.clear()
        state = Path(self.temp.name) / "state-env"
        with running_main(state, self.app, self.lines, "--no-auto-update") as (code, servers):
            pass
        self.assertEqual((code, servers[0].hub.admin_code, (state / "hub-admin-token").exists()), (0, TOKEN, False))
        self.assertTrue(any(line == "Codul de administrator vine din STUDIO_HARNESS_ADMIN_TOKEN (pornește cu --show-admin-code ca să îl afișezi)" for line in self.lines))
        self.assertNotIn(TOKEN, "\n".join(self.lines))
        for bad in ("scurt", "cu spatiu 0123456789abcdef", "x" * 513):
            with self.subTest(bad=bad), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.run_main(environ={"STUDIO_HARNESS_ADMIN_TOKEN": bad})
        # Starea salvată la ieșire are versiunea 2 și nu conține codul.
        saved = json.loads((self.state / "hub-state.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["version"], saved["hub_version"], saved["enrollment"], saved["devices"]), (2, "1.0.0", "approve", {}))
        self.assertNotIn(generated, json.dumps(saved))

    def test_the_hub_listens_on_loopback_unless_the_network_is_asked_for_explicitly(self):
        """Fără `--listen`, hub-ul rămâne pe 127.0.0.1: pornit de pe un PC de developer (Start-Hub.cmd) nu ajunge în LAN pe HTTP simplu."""
        code, servers = self.run_main(listen=None)
        self.assertEqual((code, len(servers)), (0, 1))
        self.assertEqual(servers[0].server_address[0], "127.0.0.1")
        self.assertTrue(any(line.startswith("Studio Harness hub 1.0.0 ascultă pe http://127.0.0.1:") for line in self.lines), self.lines)
        self.assertFalse([line for line in self.lines if line.startswith("atenție: hub-ul ascultă în rețea")], self.lines)
        # Expunerea cerută explicit este acceptată, dar jurnalizată ca atare. Socketul rămâne pe loopback: testele nu ascultă în rețea.
        asked = []
        bind = HubServer.__init__

        def loopback(server, listen, port, hub, inherited_fd=None):
            asked.append(listen)
            bind(server, "127.0.0.1", port, hub, inherited_fd)

        self.lines.clear()
        with patch.object(HubServer, "__init__", loopback):
            code, servers = self.run_main(listen="0.0.0.0")
        self.assertEqual((code, asked, servers[0].server_address[0]), (0, ["0.0.0.0"], "127.0.0.1"))
        self.assertIn("atenție: hub-ul ascultă în rețea pe HTTP simplu (0.0.0.0); folosește-l doar în spatele unui proxy HTTPS", self.lines)
        # `localhost` și `::1` sunt tot loopback, deci fără avertisment.
        for address in ("localhost", "::1"):
            self.lines.clear()
            asked.clear()
            with patch.object(HubServer, "__init__", loopback):
                self.run_main(listen=address)
            self.assertEqual((asked, [line for line in self.lines if line.startswith("atenție: hub-ul ascultă")]), ([address], []))

    def test_a_short_admin_code_from_the_environment_is_flagged_without_being_shown(self):
        """`STUDIO_HARNESS_ADMIN_TOKEN` este singurul secret ghicibil al hub-ului: sub 32 de caractere primește un avertisment."""
        self.assertEqual(team_hub.RECOMMENDED_ADMIN_CODE, 32)
        warning = ("atenție: codul de administrator din STUDIO_HARNESS_ADMIN_TOKEN are sub 32 de caractere; "
                   "folosește unul generat aleatoriu")
        short = "cod-scurt-de-16c"
        code, servers = self.run_main(environ={"STUDIO_HARNESS_ADMIN_TOKEN": short})
        self.assertEqual((code, servers[0].hub.admin_code), (0, short))
        self.assertIn(warning, self.lines)
        self.assertNotIn(short, "\n".join(line for line in self.lines if line != warning))
        # Un cod lung nu este semnalat, iar cel generat în hub-admin-token nu trece niciodată prin această ramură.
        self.lines.clear()
        self.run_main(environ={"STUDIO_HARNESS_ADMIN_TOKEN": "x" * team_hub.RECOMMENDED_ADMIN_CODE})
        self.assertNotIn(warning, self.lines)
        self.lines.clear()
        self.run_main(environ={"STUDIO_HARNESS_ADMIN_TOKEN": ""})
        self.assertFalse([line for line in self.lines if line.startswith("atenție: codul de administrator")], self.lines)

    def test_approve_pending_refuses_to_edit_the_state_under_a_hub_that_answers_gibberish(self):
        """Un proxy intermediar sau un răspuns trunchiat nu înseamnă „hub-ul nu rulează”: starea nu se editează sub un proces care o suprascrie."""
        target = self.state / "hub-state.json"
        hub = Hub(TOKEN, logger=self.lines.append)
        hub.register(hub.actor(device_token("ana"), None, allow_unknown=True), {"machine": "pc-ana", "bridge_id": "b", "workspace": None})
        hub.save(target)
        before = target.read_bytes()

        class Body:
            def read(self, *_):
                return b"<html>proxy</html>"

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        self.lines.clear()
        with self.assertRaises(HubError) as error:
            team_hub.approve_pending(self.state, free_port(), TOKEN, request=lambda *_, **__: Body(), logger=self.lines.append)
        self.assertEqual((error.exception.status, str(error.exception)),
                         (502, "Hub-ul în execuție a răspuns neinteligibil; nu editez starea sub un hub activ."))
        self.assertEqual((target.read_bytes(), self.lines), (before, []))
        self.assertEqual(json.loads(before)["devices"][device_id("ana")]["status"], "pending")

    def test_open_enrollment_flag_and_environment(self):
        code, servers = self.run_main("--open-enrollment")
        self.assertEqual((code, servers[0].hub.enrollment), (0, "open"))
        self.assertTrue(any(line.startswith("înrolare: open (dispozitivele noi sunt aprobate automat)") for line in self.lines), self.lines)
        # Persistat: la repornire fără flag rămâne deschisă; env-ul o deschide și el.
        code, servers = self.run_main()
        self.assertEqual(servers[0].hub.enrollment, "open")
        (self.state / "hub-state.json").unlink()
        code, servers = self.run_main()
        self.assertEqual(servers[0].hub.enrollment, "approve")
        code, servers = self.run_main(environ={"STUDIO_HARNESS_OPEN_ENROLLMENT": "1"})
        self.assertEqual(servers[0].hub.enrollment, "open")

    def test_approve_pending_uses_the_running_hub_then_the_state_file(self):
        hub = Hub(TOKEN, logger=self.lines.append)
        server = HubServer("127.0.0.1", 0, hub)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            for name in ("ana", "dan"):
                hub.register(hub.actor(device_token(name), None, allow_unknown=True), {"machine": "pc-" + name, "bridge_id": "b", "workspace": None})
            hub.approve_device(hub.actor(None, TOKEN), {"device_id": device_id("dan")})
            hub.register(hub.actor(device_token("eva"), None, allow_unknown=True), {"machine": "pc-eva", "bridge_id": "b", "workspace": None})
            self.lines.clear()
            self.assertEqual(team_hub.approve_pending(self.state, port, TOKEN, logger=self.lines.append), 2)
            self.assertEqual({row["device_id"]: (row["status"], row["approved_by"]) for row in hub.admin_devices(hub.actor(None, TOKEN))["devices"]},
                             {device_id("ana"): ("approved", "admin"), device_id("dan"): ("approved", "admin"), device_id("eva"): ("approved", "admin")})
            self.assertIn("2 dispozitive aprobate prin hub-ul în execuție (port " + str(port) + ")", self.lines)
            self.assertEqual(team_hub.approve_pending(self.state, port, TOKEN, logger=self.lines.append), 0)
            # Cod greșit: hub-ul răspunde 401 → eroare, fără să atingă fișierul de stare.
            with self.assertRaises(HubError) as error:
                team_hub.approve_pending(self.state, port, "alt-cod-0123456789", logger=self.lines.append)
            self.assertEqual(error.exception.status, 401)
            self.assertFalse((self.state / "hub-state.json").exists())
            # main() cu --approve-pending folosește hub-ul în execuție de pe portul dat și iese cu 0.
            hub.register(hub.actor(device_token("nou"), None, allow_unknown=True), {"machine": "pc-nou", "bridge_id": "b", "workspace": None})
            with running_main(self.state, self.app, self.lines, "--approve-pending", "--no-auto-update", port=port, real_threads=True) as (code, servers):
                pass
            self.assertEqual((code, servers, hub.devices[device_id("nou")]["status"]), (0, [], "approved"))
            self.assertNotIn(TOKEN, "\n".join(self.lines))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
        # Hub oprit: se editează direct hub-state.json (approved_by: cli), atomic.
        hub.register(hub.actor(device_token("later"), None, allow_unknown=True), {"machine": "pc-later", "bridge_id": "b", "workspace": None})
        hub.save(self.state / "hub-state.json")

        def refused(request, timeout=None):
            raise URLError(ConnectionRefusedError("nimic nu ascultă"))

        self.lines.clear()
        self.assertEqual(team_hub.approve_pending(self.state, port, TOKEN, request=refused, logger=self.lines.append), 1)
        saved = json.loads((self.state / "hub-state.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["devices"][device_id("later")]["status"], saved["devices"][device_id("later")]["approved_by"]), ("approved", "cli"))
        self.assertGreater(saved["devices"][device_id("later")]["approved_at"], 0)
        self.assertEqual(sorted(path.name for path in self.state.iterdir()), ["hub-state.json"])
        self.assertTrue(any(line.startswith("1 dispozitive aprobate direct în hub-state.json") for line in self.lines), self.lines)
        loaded = Hub(TOKEN, logger=self.lines.append)
        self.assertTrue(loaded.load(self.state / "hub-state.json"))
        self.assertEqual(loaded.devices[device_id("later")]["status"], "approved")
        self.assertEqual(team_hub.approve_pending(self.state, port, TOKEN, request=refused, logger=self.lines.append), 0)
        # Fără fișier (sau cu versiune veche) nu este nimic de aprobat; main() iese tot cu 0.
        (self.state / "hub-state.json").write_text(json.dumps({"format": 1, "hub_id": "a" * 32, "members": {}}), encoding="utf-8")
        self.assertEqual(team_hub.approve_pending(self.state, port, TOKEN, request=refused, logger=self.lines.append), 0)
        self.assertTrue(any("nimic de aprobat" in line for line in self.lines), self.lines)
        # Cererea implicită a CLI-ului trece prin `_OPENER` (fără redirectări), deci aici se înlocuiește el, nu `urlopen`.
        with patch.object(team_hub, "_OPENER", type("Opener", (), {"open": staticmethod(refused)})):
            with running_main(self.state, self.app, self.lines, "--approve-pending", port=port) as (code, servers):
                pass
        self.assertEqual((code, servers), (0, []))


class HubAutoUpdateTests(unittest.TestCase):
    """main() cu serve_forever dezactivat — decizia de auto-update și bucla de update, cu directorul aplicației temporar."""

    PLACEHOLDER = "https://huggingface.co/spaces/OWNER/studio-harness/resolve/main/releases/manifest.json"

    def setUp(self):
        self.temp = temp_dir()
        self.app = Path(self.temp.name) / "app"
        self.app.mkdir()
        (self.app / "team_hub.py").write_bytes(b"# hub vechi\n")
        self.state = Path(self.temp.name) / "state"
        self.channel = LocalChannel().__enter__()
        self.lines = []
        self.shutdowns = []
        self.sleeps = []
        RecordingThread.instances = []

    def tearDown(self):
        self.channel.__exit__(None, None, None)
        self.temp.cleanup()

    def write_channel(self, url=None, auto=True, upstream=None):
        settings = {"manifest_url": url or self.channel.url("/manifest.json"), "auto": auto}
        if upstream:
            settings["upstream_manifest_url"] = upstream
        (self.app / "update-channel.json").write_text(json.dumps(settings), encoding="utf-8")

    def publish_hub(self, version="9.9.9", plugin=False):
        """Manifest în upstream cu pachetul `hub` (și opțional `plugin`) al versiunii date."""
        files = {}
        if plugin:
            files["plugin"] = self.channel.entry("/roblox-studio-harness-" + version + ".zip", make_zip({"README.md": b"nou\n"}, top="roblox-studio-harness-" + version))
        files["hub"] = self.channel.entry("/studio-harness-hub-" + version + "-ubuntu.zip",
                                          make_zip({"scripts/team_hub.py": b"# hub nou\n"}, top="studio-harness-hub-" + version))
        return self.channel.manifest(version, **files)

    def update_lines(self):
        """Liniile buclei de update, fără anunțul de pornire „actualizare automată: ...”."""
        return [line for line in self.lines if line.startswith(("canalul /releases", "actualizare")) and not line.startswith("actualizare automată")]

    @contextlib.contextmanager
    def running(self, *flags, url="", auto="1"):
        with running_main(self.state, self.app, self.lines, *flags, environ={"STUDIO_HARNESS_UPDATE_URL": url, "STUDIO_HARNESS_AUTO_UPDATE": auto},
                          shutdowns=self.shutdowns) as (code, _):
            yield code

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        if len(self.sleeps) > 3:
            raise TimeoutError("bucla de update nu s-a oprit")

    def update_threads(self):
        """Firul de update înregistrat de main(); firul de salvare a stării este mereu prezent și se ignoră aici."""
        return [thread for thread in RecordingThread.instances if thread.name == "studio-harness-hub-update"]

    def run_loop(self):
        self.assertEqual([thread.name for thread in self.update_threads()], ["studio-harness-hub-update"])
        with patch.object(team_hub.time, "sleep", self.sleep):
            self.update_threads()[0]._target()

    def update_line(self):
        return next(line for line in self.lines if line.startswith("actualizare automată"))

    def test_no_auto_update_flag_keeps_the_update_thread_off(self):
        self.write_channel()
        with self.running("--no-auto-update") as code:
            self.assertEqual(code, 0)
        self.assertEqual(self.update_threads(), [])
        # Singurul fir pornit este cel de salvare a stării (la 30 s).
        self.assertEqual([(thread.name, thread.daemon) for thread in RecordingThread.instances], [("studio-harness-hub-state", True)])
        self.assertEqual(self.update_line(), "actualizare automată: inactivă (canal neconfigurat sau dezactivat)")
        self.assertNotIn("studio-harness-hub-update", [thread.name for thread in threading.enumerate()])
        self.assertTrue(any("ascultă pe http://127.0.0.1:" in line for line in self.lines))
        self.assertEqual(self.channel.requests, [])
        self.assertNotIn(TOKEN, "\n".join(self.lines))

    def test_configured_channel_starts_the_hourly_update_thread(self):
        self.write_channel()
        with self.running() as code:
            self.assertEqual(code, 0)
        self.assertEqual([(thread.name, thread.daemon) for thread in RecordingThread.instances],
                         [("studio-harness-hub-state", True), ("studio-harness-hub-update", True)])
        self.assertEqual(self.update_line(), "actualizare automată: activă (verificare la fiecare oră)")
        self.assertEqual(self.channel.requests, [])

    def test_unconfigured_disabled_or_git_checkouts_keep_it_off(self):
        self.write_channel(self.PLACEHOLDER)
        with self.running():
            pass
        self.assertEqual(self.update_line(), "actualizare automată: inactivă (canal neconfigurat sau dezactivat)")
        self.lines.clear()
        self.write_channel(auto=False)
        with self.running():
            pass
        self.assertEqual(self.update_line(), "actualizare automată: inactivă (canal neconfigurat sau dezactivat)")
        self.lines.clear()
        self.write_channel()
        with self.running(auto="0"):
            pass
        self.assertEqual(self.update_line(), "actualizare automată: inactivă (canal neconfigurat sau dezactivat)")
        self.lines.clear()
        with self.running(url="http://example.com/manifest.json"):
            pass
        self.assertEqual(self.update_line(), "actualizare automată: inactivă (canal neconfigurat sau dezactivat)")
        self.lines.clear()
        (self.app / ".git").mkdir()
        with self.running():
            pass
        self.assertEqual(self.update_line(), "actualizare automată: inactivă")
        self.assertEqual(self.update_threads(), [])
        self.assertEqual(self.channel.requests, [])

    def test_update_loop_compares_against_the_hub_version_not_a_plugin_json(self):
        # Lângă hub-ul găzduit nu există plugin.json (current_version ar da 0.0.0): bucla transmite VERSION din team_hub.py.
        self.write_channel()
        self.channel.manifest(team_hub.VERSION, hub=self.channel.entry("/hub.zip", b"nu se descarca"))
        with self.running():
            with self.assertRaises(TimeoutError):
                self.run_loop()
        self.assertEqual(self.sleeps, [30, 3600, 3600, 3600])
        self.assertEqual(self.channel.requests, ["/manifest.json"] * 3)
        self.assertEqual((self.app / "team_hub.py").read_bytes(), b"# hub vechi\n")
        self.assertEqual(self.shutdowns, [])
        self.assertFalse(any("aplicat" in line for line in self.lines))

    def test_update_loop_logs_channel_errors_and_keeps_running(self):
        self.write_channel()
        with self.running():
            with self.assertRaises(TimeoutError):
                self.run_loop()
        self.assertEqual(self.sleeps, [30, 3600, 3600, 3600])
        self.assertEqual([line for line in self.lines if line.startswith("actualizare:")], ["actualizare: Canalul de actualizare a răspuns HTTP 404."] * 3)
        self.assertEqual(self.shutdowns, [])

    def test_update_loop_applies_the_hub_bundle_flat_and_closes_the_server_for_systemd(self):
        self.write_channel()
        channel = json.dumps({"manifest_url": self.channel.url("/manifest.json"), "auto": True}).encode("utf-8")
        bundle = make_zip({"scripts/team_hub.py": b"# hub nou\n", "scripts/updater.py": b"# updater nou\n", "deploy/install.sh": b"#!/bin/sh\n",
                           "update-channel.json": channel}, top="studio-harness-hub-9.9.9")
        self.channel.manifest("9.9.9", hub=self.channel.entry("/studio-harness-hub-9.9.9-ubuntu.zip", bundle))
        with self.running() as code:
            self.assertEqual(code, 0)
            self.run_loop()
        self.assertEqual(self.sleeps, [30])
        self.assertEqual(self.channel.requests, ["/manifest.json", "/studio-harness-hub-9.9.9-ubuntu.zip"])
        self.assertEqual(len(self.shutdowns), 1)
        self.assertIsInstance(self.shutdowns[0], HubServer)
        self.assertEqual((self.app / "team_hub.py").read_bytes(), b"# hub nou\n")
        self.assertEqual((self.app / "updater.py").read_bytes(), b"# updater nou\n")
        self.assertEqual((self.app / "update-channel.json").read_bytes(), channel)
        self.assertTrue((self.app / "deploy" / "install.sh").is_file())
        self.assertFalse((self.app / "scripts").exists())
        backups = list((self.state / "backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "team_hub.py").read_bytes(), b"# hub vechi\n")
        self.assertTrue(any(line.startswith("actualizare 9.9.9 aplicată") for line in self.lines), self.lines)
        self.assertNotIn(TOKEN, "\n".join(self.lines))
        # Fără upstream separat nu există nimic de oglindit.
        self.assertFalse((self.app / "releases").exists())

    def test_update_loop_reads_upstream_and_mirrors_the_release_into_releases_before_applying(self):
        # Canalul public (lostcube.pro, aici pe loopback) diferă de upstream (GitHub): hub-ul verifică upstream-ul,
        # oglindește pachetele în releases/ (de unde se actualizează developerii) și abia apoi se actualizează pe sine.
        self.write_channel(self.channel.url("/releases/manifest.json"), upstream=self.channel.url("/manifest.json"))
        self.channel.serve("/releases/manifest.json", json.dumps({"version": "0.1.0", "files": {}}).encode("utf-8"))
        manifest = self.publish_hub(plugin=True)
        releases = self.app / "releases"
        seen = []
        real_apply = updater.apply_bundle

        def apply_bundle(*args, **kwargs):
            seen.append(sorted(path.name for path in releases.iterdir()) if releases.is_dir() else None)
            return real_apply(*args, **kwargs)

        with self.running() as code, patch.object(updater, "apply_bundle", apply_bundle):
            self.assertEqual(code, 0)
            self.assertEqual(self.update_line(), "actualizare automată: activă (verificare la fiecare oră)")
            self.run_loop()
        self.assertEqual(self.sleeps, [30])
        self.assertEqual(self.channel.requests, ["/manifest.json", "/roblox-studio-harness-9.9.9.zip", "/studio-harness-hub-9.9.9-ubuntu.zip",
                                                 "/studio-harness-hub-9.9.9-ubuntu.zip"])
        # La aplicare canalul public era deja oglindit complet.
        names = ["manifest.json", "roblox-studio-harness-9.9.9.zip", "roblox-studio-harness-9.9.9.zip.sha256",
                 "studio-harness-hub-9.9.9-ubuntu.zip", "studio-harness-hub-9.9.9-ubuntu.zip.sha256"]
        self.assertEqual(seen, [names])
        self.assertEqual(sorted(path.name for path in releases.iterdir()), names)
        mirrored = json.loads((releases / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(mirrored["version"], "9.9.9")
        self.assertEqual({kind: entry["url"] for kind, entry in mirrored["files"].items()},
                         {"plugin": self.channel.url("/releases/roblox-studio-harness-9.9.9.zip"), "hub": self.channel.url("/releases/studio-harness-hub-9.9.9-ubuntu.zip")})
        for kind, name in (("plugin", "roblox-studio-harness-9.9.9.zip"), ("hub", "studio-harness-hub-9.9.9-ubuntu.zip")):
            with self.subTest(kind=kind):
                self.assertEqual((mirrored["files"][kind]["sha256"], mirrored["files"][kind]["size"]), (manifest["files"][kind]["sha256"], manifest["files"][kind]["size"]))
                self.assertEqual(updater.bundle_sha256(releases / name), manifest["files"][kind]["sha256"])
                self.assertEqual((releases / (name + ".sha256")).read_text(encoding="utf-8"), manifest["files"][kind]["sha256"] + "  " + name + "\n")
        self.assertEqual((self.app / "team_hub.py").read_bytes(), b"# hub nou\n")
        self.assertEqual(len(self.shutdowns), 1)
        lines = self.update_lines()
        self.assertEqual(lines[0], "canalul /releases oglindit pentru versiunea 9.9.9")
        self.assertTrue(lines[1].startswith("actualizare 9.9.9 aplicată"), lines)
        self.assertEqual(len(lines), 2)
        self.assertNotIn(TOKEN, "\n".join(self.lines))

    def test_update_loop_skips_mirroring_when_upstream_is_the_public_channel_or_a_placeholder(self):
        for upstream in (self.channel.url("/manifest.json"), self.PLACEHOLDER):
            with self.subTest(upstream=upstream):
                self.write_channel(upstream=upstream)
                self.publish_hub()
                (self.app / "team_hub.py").write_bytes(b"# hub vechi\n")
                self.lines.clear(), self.sleeps.clear(), self.shutdowns.clear(), self.channel.requests.clear()
                RecordingThread.instances = []
                with self.running():
                    self.run_loop()
                self.assertEqual(self.channel.requests, ["/manifest.json", "/studio-harness-hub-9.9.9-ubuntu.zip"])
                self.assertFalse((self.app / "releases").exists())
                self.assertEqual((self.app / "team_hub.py").read_bytes(), b"# hub nou\n")
                self.assertEqual(len(self.shutdowns), 1)
                self.assertEqual(self.update_lines()[0][:26], "actualizare 9.9.9 aplicată")
                self.assertFalse(any(line.startswith("canalul /releases") for line in self.lines))

    def test_mirror_failure_keeps_the_old_hub_and_the_loop_running(self):
        self.write_channel(self.channel.url("/releases/manifest.json"), upstream=self.channel.url("/manifest.json"))
        manifest = self.publish_hub()
        manifest["files"]["hub"]["sha256"] = "0" * 64
        self.channel.serve("/manifest.json", json.dumps(manifest).encode("utf-8"))
        with self.running():
            with self.assertRaises(TimeoutError):
                self.run_loop()
        self.assertEqual(self.sleeps, [30, 3600, 3600, 3600])
        self.assertEqual(self.channel.requests, ["/manifest.json", "/studio-harness-hub-9.9.9-ubuntu.zip"] * 3)
        self.assertEqual([line for line in self.lines if line.startswith("actualizare eșuată")],
                         ["actualizare eșuată: Pachetul descărcat nu corespunde sumei SHA256 din manifest; nu a fost aplicat."] * 3)
        self.assertEqual((self.app / "team_hub.py").read_bytes(), b"# hub vechi\n")
        self.assertEqual(self.shutdowns, [])
        # Nimic publicat pe jumătate: fără manifest.json și fără .part în releases/.
        self.assertEqual(sorted(path.name for path in (self.app / "releases").iterdir()), [])


class AliasTests(unittest.TestCase):
    def test_team_hub_alias_and_constants(self):
        self.assertIs(TeamHub, Hub)
        self.assertEqual(team_hub.VERSION, "1.0.0")
        self.assertEqual((team_hub.SESSION_RETENTION, team_hub.MAX_JOURNAL, team_hub.SYNC_JOURNAL, team_hub.WORKSPACE_JOURNAL, team_hub.ONLINE_SECONDS,
                          team_hub.OFFLINE_SECONDS, team_hub.MAX_SYNC_SESSIONS, team_hub.MAX_BODY, team_hub.MAX_PROJECT_NODES, team_hub.SAVE_INTERVAL),
                         (1800, 1000, 50, 200, 15, 60, 64, 4 * 1024 * 1024, 20000, 30))
        self.assertEqual((team_hub.DEVICE_HEADER, team_hub.ADMIN_HEADER, team_hub.ADMIN_ENV), ("X-Studio-Harness-Device", "X-Studio-Harness-Admin", "STUDIO_HARNESS_ADMIN_TOKEN"))
        for key, expected in (("game:1", True), ("place:22", True), ("local", True), ("game:0", False), ("game:x", False), ("Game:1", False),
                              ("game:" + str(2 ** 53), False), ("game:" + str(2 ** 53 - 1), True), ("", False), (None, False), (3, False), ("team", False)):
            self.assertEqual(team_hub.valid_workspace_key(key), expected, key)
        self.assertEqual(team_hub.meta_for_key("place:22"), {"key": "place:22", "game_id": 0, "place_id": 22, "name": "place:22", "creator_id": 0, "creator_type": "necunoscut"})
        self.assertEqual(team_hub.meta_for_key("local")["key"], "local")
        self.assertFalse(hasattr(team_hub, "STATE_FORMAT"))


if __name__ == "__main__":
    unittest.main()

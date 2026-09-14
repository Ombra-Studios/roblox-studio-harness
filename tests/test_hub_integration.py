"""Daemonul real contra hubului real, pe loopback (contract §3.4 și §6).

Restul suitei leagă daemonul de un hub fals (`tests/test_studio_bridge.py`, `tests/test_team_client.py`) sau hubul real de
cereri HTTP scrise de mână (`tests/test_deploy.py`, `tests/test_team_hub.py`). Aici sunt legate cele două implementări
adevărate — `studio_bridge.Bridge` plus `team_client.HubClient` contra lui `team_hub.Hub` — pentru că plafoanele hubului
(sesiuni per dispozitiv, jurnal per sync, dimensiunea unui eveniment, limitarea de rată a codului de administrator) sunt
respectate numai dacă daemonul, nemodificat, le întâlnește exact așa cum sunt scrise în contract.

Hubul ascultă pe 127.0.0.1 cu port 0 (niciodată 34871/34880), starea daemonului stă în %TEMP%\\studio-harness-1.0\\, nu se
atinge nicio rețea externă. Clientul hubului nu are fir propriu (`autostart=False`): fiecare ciclu este un `step()` explicit,
deci testele sunt deterministe, fără sleepuri.
"""

import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import team_hub  # noqa: E402
from studio_bridge import Bridge  # noqa: E402
from team_client import HubClient  # noqa: E402
from test_studio_bridge import (BALL, DEVICE_ID, DEVICE_TOKEN, IDENTITY, UI_TOKEN, FakeNative,  # noqa: E402
                                FakeProviders, temp_dir, wait_until)

ADMIN_CODE = "cod-de-administrator-pentru-teste-0123456789"
WRONG_CODE = "cod-de-administrator-gresit-0123456789"


def manual_hub_client(bridge, hub_url, device_token, machine, **extra):
    """`HubClient` real fără fir propriu: testul decide când se face fiecare ciclu."""
    return HubClient(bridge, hub_url, device_token, machine, autostart=False, **extra)


class DaemonAgainstTheRealHubTests(unittest.TestCase):
    def setUp(self):
        self.temp = temp_dir()
        self.lines = []
        self.hub = team_hub.Hub(ADMIN_CODE, logger=self.lines.append)
        self.server = team_hub.HubServer("127.0.0.1", 0, self.hub)
        self.url = "http://127.0.0.1:" + str(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.bridge = Bridge(FakeProviders(), Path(self.temp.name) / "runtime", FakeNative(), UI_TOKEN, developer="tester",
                             state_directory=Path(self.temp.name) / "state", hub_url=self.url, device_token=DEVICE_TOKEN,
                             hub_client_factory=manual_hub_client)

    def tearDown(self):
        self.bridge.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    # ----- ajutoare -----

    def step(self):
        self.bridge.hub.step()

    def approve(self):
        self.hub.approve_device(self.hub.actor(None, ADMIN_CODE), {"device_id": DEVICE_ID})

    def connect(self):
        """Înregistrare → aprobare → identitate: dispozitivul devine membru aprobat, cu workspaceul Ball."""
        self.step()
        self.assertEqual(self.bridge.hub_status()["status"], "pending")
        self.approve()
        self.step()
        self.bridge.set_identity(IDENTITY)
        self.step()
        self.assertEqual(self.bridge.hub_status()["status"], "approved")

    def own_sessions(self):
        with self.hub.lock:
            return [entry for entry in self.hub.sessions.values() if entry["device_id"] == DEVICE_ID]

    # ----- teste -----

    def test_the_daemon_registers_syncs_and_appears_in_the_hub_with_its_workspace(self):
        self.connect()
        with self.hub.lock:
            device = dict(self.hub.devices[DEVICE_ID])
        self.assertEqual((device["status"], device["roblox_name"], device["machine"], device["workspace"]),
                         ("approved", "ellob", self.bridge.machine, BALL["key"]))
        status = self.bridge.status()
        self.assertEqual([(row["roblox_name"], row["me"]) for row in status["members"]], [("ellob", True)])
        self.assertEqual([row["key"] for row in status["workspaces"]], [BALL["key"]])
        # Panoul vede același dispozitiv, cu workspaceul curent în `me` (contract §9.2).
        panel = self.hub.panel_data(self.hub.actor(DEVICE_TOKEN, None))
        self.assertEqual((panel["me"]["device_id"], panel["me"]["roblox_name"], panel["me"]["workspace"]),
                         (DEVICE_ID, "ellob", BALL["key"]))
        selected = panel["selected"]["workspace"]
        self.assertEqual((selected["key"], selected["name"]), (BALL["key"], "Ball"))
        self.assertEqual([(row["roblox_name"], row["device_id"]) for row in panel["selected"]["members"]], [("ellob", DEVICE_ID)])
        self.assertEqual([row["key"] for row in panel["workspaces"]], [BALL["key"]])
        # Nimic secret în jurnalul hubului.
        text = "\n".join(self.lines)
        self.assertNotIn(DEVICE_TOKEN, text)
        self.assertNotIn(ADMIN_CODE, text)

    def test_finished_studio_sessions_are_recycled_so_the_daemon_never_hits_the_session_cap(self):
        """Hubul reciclează doar sesiunile închise: dacă daemonul nu i-ar raporta starea finală a unei sesiuni Studio,
        plafonul `MAX_DEVICE_SESSIONS` s-ar umple cu sesiuni veșnic deschise, iar sincronizarea ar cădea definitiv cu 429."""
        self.connect()
        for index in range(team_hub.MAX_DEVICE_SESSIONS + 4):
            job = self.bridge.start_chat({"provider": "claude", "prompt": "cererea " + str(index), "studio_id": "studio-1"})
            self.step()  # hubul află sesiunea deschisă
            self.assertTrue(wait_until(lambda: job.state == "completed"), job.state)
            self.step()  # …și imediat starea finală, altfel îi rămâne deschisă pentru totdeauna
            self.assertIsNone(self.bridge.hub_status()["error"], self.bridge.hub_status())
        self.assertEqual(self.bridge.hub_status()["status"], "approved")
        mine = self.own_sessions()
        self.assertLessEqual(len(mine), team_hub.MAX_DEVICE_SESSIONS)
        self.assertEqual({entry["meta"]["state"] for entry in mine}, {"completed"})
        self.assertTrue(all(entry["closed_at"] is not None for entry in mine))
        self.assertNotIn(team_hub.SESSIONS_FULL, "\n".join(self.lines))

    def test_a_burst_of_journal_entries_reaches_the_hub_without_losing_any(self):
        """Hubul ia cel mult `MAX_SYNC_JOURNAL` intrări pe sync și le ignoră tăcut pe următoarele; clientul trimite exact
        atâtea și păstrează restul, altfel o coadă strânsă în timpul unei căderi a hubului s-ar pierde la revenire."""
        self.connect()
        total = team_hub.MAX_SYNC_JOURNAL * 2 + 5
        for index in range(total):
            self.bridge.hub.record({"time": 1000.0 + index, "job_id": "job-" + str(index), "provider": "claude", "tool": "multi_edit",
                                    "paths": ["ServerScriptService.Main"], "summary": "intrarea " + str(index), "workspace": BALL["key"]})
        for _ in range(3):
            self.step()
        with self.hub.lock:
            summaries = [row["summary"] for row in self.hub.journal]
        self.assertEqual(summaries, ["intrarea " + str(index) for index in range(total)])
        self.assertEqual(list(self.bridge.hub.pending_journal), [])
        self.assertEqual([row["seq"] for row in self.hub.journal], list(range(1, total + 1)))

    def test_a_long_answer_reaches_the_hub_as_text_not_as_the_too_large_note(self):
        """Daemonul taie textul în bucăți sub plafonul **serializat** al hubului: o bucată de dimensiune maximă care ar fi
        înlocuită cu nota „a fost omis” ar face ilizibil, pentru colegi, exact răspunsul lung."""
        self.connect()
        job = self.bridge.create_terminal_session({"provider": "claude", "cwd": "Ball"})
        answer = "ș" * 70000
        job.emit("text", answer)
        self.step()
        with self.hub.lock:
            events = [dict(event) for event in self.hub.sessions[job.id]["events"]]
        self.assertEqual("".join(event["text"] for event in events if event["type"] == "text"), answer)
        self.assertNotIn(team_hub.EVENT_TOO_LARGE, [event.get("text") for event in events])
        self.assertEqual([event["seq"] for event in events], list(range(1, len(events) + 1)))

    def test_the_admin_rate_limit_never_blocks_the_device(self):
        """Limitarea de rată contorizează doar codurile de administrator greșite: în spatele unui proxy toți clienții împart
        aceeași adresă, deci altfel un atacator ar opri sincronizarea tuturor daemonilor cu zece coduri greșite."""
        self.connect()
        for _ in range(team_hub.AUTH_FAIL_LIMIT * 2):
            self.step()
            self.assertIsNone(self.bridge.hub_status()["error"])
        for _ in range(team_hub.AUTH_FAIL_LIMIT):
            with self.assertRaises(team_hub.HubError):
                self.hub.actor(None, WRONG_CODE, client="127.0.0.1")
        with self.assertRaises(team_hub.HubError) as error:
            self.hub.actor(None, ADMIN_CODE, client="127.0.0.1")
        self.assertEqual((error.exception.status, str(error.exception)), (429, team_hub.TOO_MANY_ATTEMPTS))
        # Dispozitivul sincronizează mai departe, de la aceeași adresă.
        self.step()
        self.assertEqual((self.bridge.hub_status()["status"], self.bridge.hub_status()["error"]), ("approved", None))

    def test_the_session_meta_survives_the_length_caps_of_the_hub(self):
        """Câmpurile de meta sunt tăiate la `MAX_META_TEXT`, nu respinse: un `cwd` lung nu trebuie să oprească sincronizarea."""
        self.connect()
        job = self.bridge.create_terminal_session({"provider": "claude", "cwd": "C:\\" + "d" * 400})
        self.bridge.claims.claim(job.id, "ellob", ["Workspace.Map.Zone3"])
        self.step()
        with self.hub.lock:
            meta = dict(self.hub.sessions[job.id]["meta"])
        self.assertEqual(len(meta["cwd"]), team_hub.MAX_META_TEXT)
        self.assertEqual((meta["kind"], meta["provider"], meta["developer"], meta["workspace"]), ("terminal", "claude", "ellob", BALL["key"]))
        self.assertEqual(meta["claims"], ["Workspace.Map.Zone3"])
        self.assertIsNone(self.bridge.hub_status()["error"])


if __name__ == "__main__":
    unittest.main()

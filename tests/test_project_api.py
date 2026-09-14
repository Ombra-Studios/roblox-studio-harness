"""Harta proiectului (0.7 → 1.0) în daemon și în hub: chunk-uri cu workspace, /v1/project, hub_project pe workspace-ul jobului,
want_project per workspace, /hub/project, /hub/panel-data, /panel.

Fără rețea în afara loopback-ului (hub real pe 127.0.0.1, port 0), fără scriere în %LOCALAPPDATA%; panoul este un fișier fals într-un
director temporar; clienții hub ai daemon-urilor rulează fără fir (autostart=False) și sunt conduși pas cu pas din test."""

import json
import sys
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import studio_bridge
import team_hub
from project_map import GROUP_KEYS
from studio_bridge import Bridge, BridgeError, BridgeServer
from team_client import HubClient, HubError as ClientError
from team_hub import Hub, HubError, HubServer
from test_studio_bridge import BALL, IDENTITY, UI_TOKEN, FakeNative, FakeProviders, make_bridge, temp_dir
from test_team_hub import KART, TOKEN, USER_IDS, device_id, device_token, session_row

PANEL_HTML = b"<!doctype html><title>Panou de test</title><p>Studio Harness</p>\n"


def inventory(parts=3):
    """Un inventar mic, cu toate grupele reprezentate, plus `parts` părți în Workspace.Map."""
    nodes = [
        [0, -1, "Workspace", "Workspace", 0],
        [1, 0, "Model", "Map", 0],
        [2, -1, "Lighting", "Lighting", 0],
        [3, 2, "Atmosphere", "Atmosphere", 0],
        [4, -1, "ServerScriptService", "ServerScriptService", 0],
        [5, 4, "Script", "Main", 1],
        [6, -1, "StarterGui", "StarterGui", 0],
        [7, 6, "ScreenGui", "HUD", 0],
        [8, 7, "LocalScript", "Controller", 1],
        [9, -1, "ReplicatedStorage", "ReplicatedStorage", 0],
        [10, 9, "RemoteEvent", "Ping", 0],
        [11, 9, "ModuleScript", "Shared", 1],
        [12, 9, "Folder", "Config", 0],
        [13, 1, "WeldConstraint", "W", 0],
        [14, 0, "SpawnLocation", "Spawn", 0],
        [15, 0, "Sound", "Ambient", 0],
        [16, 0, "Camera", "Camera", 0],
    ]
    for index in range(parts):
        nodes.append([len(nodes), 1, "Part", "P" + str(index), 0])
    return nodes


def chunks(nodes, total):
    """Împarte inventarul în `total` chunk-uri consecutive (id-urile continuă de la un chunk la altul)."""
    size = -(-len(nodes) // total)
    return [nodes[index * size:(index + 1) * size] for index in range(total)]


def chunk_body(snapshot_id, index, total, nodes, **extra):
    body = {"snapshot_id": snapshot_id, "index": index, "total": total, "place_id": 12345, "place_name": "Locul de test",
            "studio_id": "studio-1", "truncated": False, "nodes": nodes}
    body.update(extra)
    return body


def text(result):
    return result["content"][0]["text"]


def project_payload(snapshot_id="snap-1", nodes=None, **extra):
    """Un proiect așa cum îl trimite daemon-ul la `want_project` (clasificat deja)."""
    nodes = inventory() if nodes is None else nodes
    value = {"snapshot_id": snapshot_id, "place_id": 1291603, "place_name": "Ball", "studio_id": "studio-1", "developer": "fals", "machine": "fals",
             "taken": 900.0, "count": len(nodes), "truncated": False, "nodes": nodes,
             "groups": {"assets": {"key": "assets", "label": "Asseturi", "count": 4, "by_class": {"Part": 3, "Model": 1}, "entries": []},
                        "other": {"key": "other", "label": "Altele", "count": len(nodes) - 4, "by_class": {}, "entries": []}}}
    value.update(extra)
    return value


class DaemonProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = temp_dir()
        self.native = FakeNative()
        self.bridge = make_bridge(self.temp.name, native=self.native)

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def send(self, nodes, snapshot_id="snap-1", total=3, order=None, **extra):
        parts = chunks(nodes, total)
        results = []
        for index in (order if order is not None else range(total)):
            results.append(self.bridge.project_chunk(chunk_body(snapshot_id, index, total, parts[index], **extra)))
        return results

    def test_chunks_in_random_order_assemble_once_all_have_arrived(self):
        nodes = inventory(parts=7)
        self.assertIsNone(self.bridge.status()["project"])
        self.assertEqual(self.bridge.project_view(), {"ok": True, "project": None})
        parts = chunks(nodes, 3)
        results = [self.bridge.project_chunk(chunk_body("snap-1", 2, 3, parts[2])), self.bridge.project_chunk(chunk_body("snap-1", 0, 3, parts[0]))]
        self.assertEqual(results[0], {"ok": True, "complete": False, "received": 1, "total": 3})
        self.assertEqual(results[1], {"ok": True, "complete": False, "received": 2, "total": 3})
        self.assertIsNone(self.bridge.project)
        self.assertIsNone(self.bridge.status()["project"])
        results.append(self.bridge.project_chunk(chunk_body("snap-1", 1, 3, parts[1])))
        self.assertEqual((results[2]["ok"], results[2]["complete"], results[2]["received"], results[2]["count"]), (True, True, 3, len(nodes)))
        self.assertEqual(results[2]["groups"]["assets"], 8)
        project = self.bridge.project
        self.assertEqual({key: project[key] for key in ("snapshot_id", "place_id", "place_name", "studio_id", "developer", "machine", "count", "truncated",
                                                        "game_id", "creator_id", "creator_type", "workspace")},
                         {"snapshot_id": "snap-1", "place_id": 12345, "place_name": "Locul de test", "studio_id": "studio-1", "developer": "tester",
                          "machine": self.bridge.machine, "count": len(nodes), "truncated": False,
                          "game_id": 0, "creator_id": 0, "creator_type": "necunoscut", "workspace": "place:12345"})
        self.assertIsInstance(project["taken"], float)
        # Nodurile sunt reasamblate în ordinea chunk-urilor, nu a sosirii.
        self.assertEqual(project["nodes"], nodes)
        self.assertEqual(tuple(project["groups"]), GROUP_KEYS)
        self.assertEqual(project["groups"]["scripts_server"]["entries"][0]["path"], "ServerScriptService.Main")
        self.assertEqual(self.bridge.project_chunks, {})
        self.assertEqual(self.bridge.status()["project"], {"snapshot_id": "snap-1", "count": len(nodes), "taken": project["taken"], "place_name": "Locul de test"})
        self.assertIs(self.bridge.project_view()["project"], project)
        self.assertEqual(self.bridge.project_digest(), "snap-1")
        # Un snapshot nou înlocuiește proiectul; un chunk retrimis pentru același index nu strică nimic.
        smaller = inventory(parts=1)
        self.send(smaller, snapshot_id="snap-2", total=2, order=[0, 0, 1])
        self.assertEqual((self.bridge.project["snapshot_id"], self.bridge.project["count"]), ("snap-2", len(smaller)))

    def test_project_workspace_follows_game_and_place_ids(self):
        nodes = inventory()
        self.bridge.project_chunk(chunk_body("g", 0, 1, nodes, game_id=987654, place_id=1291603, creator_id=555, creator_type="user"))
        project = self.bridge.project
        self.assertEqual((project["workspace"], project["game_id"], project["place_id"], project["creator_id"], project["creator_type"]),
                         ("game:987654", 987654, 1291603, 555, "User"))
        # Fără game_id, cheia vine din place_id; un place_id text (legacy) sau lipsă înseamnă „local”.
        self.bridge.project_chunk(chunk_body("p", 0, 1, nodes, game_id=0, place_id=77))
        self.assertEqual(self.bridge.project["workspace"], "place:77")
        self.bridge.project_chunk(chunk_body("t", 0, 1, nodes, place_id="abc"))
        self.assertEqual(self.bridge.project["workspace"], "local")
        self.bridge.project_chunk(chunk_body("n", 0, 1, nodes, place_id=None, game_id=None, creator_type=None))
        self.assertEqual((self.bridge.project["workspace"], self.bridge.project["creator_type"]), ("local", "necunoscut"))
        for extra in ({"game_id": -1}, {"game_id": "9"}, {"game_id": True}, {"creator_id": 1.5}, {"creator_type": 4}):
            with self.subTest(extra=extra), self.assertRaises(BridgeError) as error:
                self.bridge.project_chunk(chunk_body("bad", 0, 1, nodes, **extra))
            self.assertEqual(error.exception.status, 400)
        self.assertEqual(self.bridge.project["snapshot_id"], "n")

    def test_single_chunk_and_truncated_flag(self):
        nodes = inventory()
        result = self.bridge.project_chunk(chunk_body("s", 0, 1, nodes, truncated=True, place_id="abc", studio_id=None, place_name=None))
        self.assertTrue(result["complete"])
        project = self.bridge.project
        self.assertEqual((project["truncated"], project["place_id"], project["studio_id"], project["place_name"]), (True, "abc", None, ""))

    def test_incomplete_snapshot_expires_after_sixty_seconds(self):
        nodes = inventory()
        parts = chunks(nodes, 2)
        self.bridge.project_chunk(chunk_body("old", 0, 2, parts[0]))
        self.assertIn("old", self.bridge.project_chunks)
        self.bridge.project_chunks["old"]["started"] -= studio_bridge.PROJECT_CHUNK_TIMEOUT + 1
        # Orice chunk nou curăță snapshot-urile vechi neterminate.
        self.bridge.project_chunk(chunk_body("fresh", 0, 2, parts[0]))
        self.assertEqual(set(self.bridge.project_chunks), {"fresh"})
        # Al doilea chunk al snapshot-ului expirat începe de la zero; nu se asamblează cu jumătatea aruncată.
        result = self.bridge.project_chunk(chunk_body("old", 1, 2, parts[1]))
        self.assertEqual(result, {"ok": True, "complete": False, "received": 1, "total": 2})
        self.assertIsNone(self.bridge.project)
        self.assertEqual(studio_bridge.PROJECT_CHUNK_TIMEOUT, 60)

    def test_node_limit_across_chunks(self):
        self.assertEqual(studio_bridge.MAX_PROJECT_NODES, 20000)
        nodes = inventory(parts=10)
        with patch.object(studio_bridge, "MAX_PROJECT_NODES", 20):
            with self.assertRaises(BridgeError) as error:
                self.bridge.project_chunk(chunk_body("big", 0, 1, nodes))
            self.assertEqual(error.exception.status, 413)
            parts = chunks(nodes, 2)
            self.bridge.project_chunk(chunk_body("sum", 0, 2, parts[0]))
            with self.assertRaises(BridgeError) as error:
                self.bridge.project_chunk(chunk_body("sum", 1, 2, parts[1]))
            self.assertEqual(error.exception.status, 413)
            self.assertNotIn("sum", self.bridge.project_chunks)
        self.assertIsNone(self.bridge.project)

    def test_chunk_validations(self):
        nodes = inventory()
        invalid = [
            {"snapshot_id": ""}, {"snapshot_id": 3}, {"snapshot_id": "s" * 129}, {"index": -1}, {"index": 3}, {"index": True}, {"index": "0"},
            {"total": 0}, {"total": studio_bridge.MAX_PROJECT_CHUNKS + 1}, {"total": 1.5}, {"nodes": "x"}, {"nodes": [[0, -1, "Workspace", "Workspace"]]},
            {"nodes": [1]}, {"place_name": "n" * 201}, {"place_name": 5}, {"place_id": 1.5}, {"place_id": "p" * 65}, {"studio_id": 4},
            {"truncated": "da"},
        ]
        for extra in invalid:
            body = chunk_body("s", 0, 3, nodes)
            body.update(extra)
            with self.subTest(extra=extra), self.assertRaises(BridgeError) as error:
                self.bridge.project_chunk(body)
            self.assertEqual(error.exception.status, 400)
        self.assertEqual(self.bridge.project_chunks, {})
        # total diferit între chunk-urile aceluiași snapshot: 409 și snapshot-ul este aruncat.
        parts = chunks(nodes, 2)
        self.bridge.project_chunk(chunk_body("s", 0, 2, parts[0]))
        with self.assertRaises(BridgeError) as error:
            self.bridge.project_chunk(chunk_body("s", 1, 3, parts[1]))
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.bridge.project_chunks, {})
        # Inventar asamblat invalid (id-uri neconsecutive): 400, fără proiect.
        broken = [list(node) for node in nodes]
        broken[3][0] = 9
        with self.assertRaises(BridgeError) as error:
            self.bridge.project_chunk(chunk_body("bad", 0, 1, broken))
        self.assertEqual(error.exception.status, 400)
        self.assertIn("consecutive", str(error.exception))
        self.assertIsNone(self.bridge.project)
        self.assertEqual(self.bridge.project_chunks, {})

    def test_hub_project_and_hub_board_via_agent_call(self):
        job = self.bridge.create_terminal_session({"provider": "claude"})
        tools = {tool["name"]: tool for tool in self.bridge.agent_tools(job)}
        self.assertIn("hub_project", tools)
        self.assertEqual(tools["hub_project"]["inputSchema"]["properties"]["group"]["enum"], list(GROUP_KEYS))
        self.assertTrue(tools["hub_project"]["annotations"]["readOnlyHint"])
        missing = self.bridge.agent_call(job, "hub_project", {})
        self.assertTrue(missing["isError"])
        self.assertIn("pluginul Studio", text(missing))
        self.assertIsNone(json.loads(text(self.bridge.agent_call(job, "hub_board", {})))["project"])
        self.assertTrue(self.bridge.agent_call(job, "hub_project", {"group": "necunoscut"})["isError"])
        self.assertTrue(self.bridge.agent_call(job, "hub_project", {"group": 3})["isError"])
        nodes = inventory(parts=60)
        self.send(nodes)
        overview = json.loads(text(self.bridge.agent_call(job, "hub_project", {})))
        self.assertEqual((overview["place_name"], overview["count"], overview["truncated"], overview["snapshot_id"], overview["workspace"]),
                         ("Locul de test", len(nodes), False, "snap-1", "place:12345"))
        self.assertEqual(tuple(overview["groups"]), GROUP_KEYS)
        assets = overview["groups"]["assets"]
        self.assertEqual((assets["label"], assets["count"], assets["by_class"], len(assets["sample"])), ("Asseturi", 61, {"Model": 1, "Part": 60}, 50))
        self.assertEqual(assets["sample"][:2], ["Workspace.Map", "Workspace.Map.P0"])
        self.assertNotIn("entries", assets)
        self.assertEqual(overview["groups"]["networking"]["sample"], ["ReplicatedStorage.Ping"])
        full = json.loads(text(self.bridge.agent_call(job, "hub_project", {"group": "assets"})))
        self.assertEqual((full["place_name"], full["count"], full["workspace"], full["group"]["key"], full["group"]["count"], len(full["group"]["entries"])),
                         ("Locul de test", len(nodes), "place:12345", "assets", 61, 61))
        self.assertEqual(full["group"]["entries"][-1], {"id": len(nodes) - 1, "path": "Workspace.Map.P59", "className": "Part", "name": "P59"})
        empty = json.loads(text(self.bridge.agent_call(job, "hub_project", {"group": "physics"})))["group"]
        self.assertEqual((empty["label"], empty["count"], empty["entries"][0]["path"]), ("Fizică", 1, "Workspace.Map.W"))
        board = json.loads(text(self.bridge.agent_call(job, "hub_board", {})))
        self.assertEqual(board["project"], {"place_name": "Locul de test", "count": len(nodes),
                                            "groups": {**{key: 0 for key in GROUP_KEYS}, "graphics": 2, "assets": 61, "audio": 1, "ui": 1,
                                                       "scripts_server": 1, "scripts_client": 1, "scripts_shared": 1, "networking": 1,
                                                       "data": 1, "physics": 1, "gameplay": 1, "settings": 4, "other": 1}})
        # Toolurile de coordonare sunt citiri: nimic în jurnal, nicio aprobare.
        self.assertEqual(list(self.bridge.journal), [])
        self.assertIsNone(job.pending)
        # 1.0: proiectul local este al agentului doar dacă este al workspace-ului jobului; altul nu se poate aduce fără hub aprobat.
        self.bridge.set_identity(IDENTITY)
        self.assertEqual(job.workspace, "game:987654")
        other = self.bridge.agent_call(job, "hub_project", {})
        self.assertTrue(other["isError"])
        self.assertEqual(text(other), studio_bridge.NO_PROJECT)
        self.assertIsNone(json.loads(text(self.bridge.agent_call(job, "hub_board", {})))["project"])
        self.send(nodes, snapshot_id="snap-ball", game_id=987654, place_id=1291603)
        self.assertEqual(json.loads(text(self.bridge.agent_call(job, "hub_project", {})))["workspace"], "game:987654")
        self.assertEqual(json.loads(text(self.bridge.agent_call(job, "hub_board", {})))["workspace"], BALL)


class DaemonProjectHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = temp_dir()
        self.bridge = make_bridge(self.temp.name)
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

    def request(self, path, body=None, token=UI_TOKEN):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Studio-Harness-Token"] = token
        request = Request(self.base + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_project_routes_need_the_local_code_and_chunks_mark_the_plugin_connected(self):
        self.assertEqual(self.request("/v1/project"), (200, {"ok": True, "project": None}))
        self.assertEqual(self.request("/v1/project", token="wrong")[0], 401)
        self.assertEqual(self.request("/v1/project/chunks", chunk_body("s", 0, 1, inventory()), token=None)[0], 401)
        self.assertFalse(self.request("/v1/status")[1]["plugin_connected"])
        nodes = inventory(parts=4)
        parts = chunks(nodes, 2)
        status, body = self.request("/v1/project/chunks", chunk_body("s", 1, 2, parts[1], game_id=987654, creator_id=555, creator_type="User"))
        self.assertEqual((status, body), (200, {"ok": True, "complete": False, "received": 1, "total": 2}))
        self.assertTrue(self.request("/v1/status")[1]["plugin_connected"])
        status, body = self.request("/v1/project/chunks", chunk_body("s", 0, 2, parts[0], game_id=987654, creator_id=555, creator_type="User"))
        self.assertEqual((status, body["complete"], body["count"]), (200, True, len(nodes)))
        status, body = self.request("/v1/project")
        self.assertEqual((status, body["ok"], body["project"]["snapshot_id"], body["project"]["count"], len(body["project"]["nodes"])),
                         (200, True, "s", len(nodes), len(nodes)))
        self.assertEqual((body["project"]["workspace"], body["project"]["game_id"], body["project"]["creator_type"]), ("game:987654", 987654, "User"))
        self.assertEqual(body["project"]["groups"]["assets"]["count"], 5)
        summary = self.request("/v1/status")[1]["project"]
        self.assertEqual((summary["snapshot_id"], summary["count"], summary["place_name"]), ("s", len(nodes), "Locul de test"))
        self.assertIsInstance(summary["taken"], float)
        self.assertEqual(self.request("/v1/project/chunks", {"snapshot_id": "x", "index": 0, "total": 1, "nodes": "nu"})[0], 400)
        # GET pe ruta de chunk-uri și POST pe /v1/project nu există.
        self.assertEqual(self.request("/v1/project/chunks")[0], 404)
        self.assertEqual(self.request("/v1/project", {})[0], 404)


class HubProjectTests(unittest.TestCase):
    """Hub-ul 1.0 în proces: proiectul se atașează workspace-ului curent al dispozitivului și este cerut (`want_project`) per workspace."""

    def setUp(self):
        self.now = [1000.0]
        self.lines = []
        self.hub = Hub(TOKEN, clock=lambda: self.now[0], logger=self.lines.append, avatar_fetcher=lambda user, size: None)
        self.hub.enrollment = "open"

    def actor(self, name):
        return self.hub.actor(device_token(name), None, allow_unknown=True)

    def admin(self):
        return self.hub.actor(None, TOKEN)

    def register(self, name, workspace=BALL):
        response = self.hub.register(self.actor(name), {"roblox": {"user_id": USER_IDS.get(name, 1), "name": name}, "machine": "pc-" + name,
                                                        "bridge_id": "bridge-" + name, "version": "1.0.0", "workspace": workspace})
        self.assertEqual(response["status"], "approved")
        return response["device_id"]

    def sync(self, name, workspace=BALL, **body):
        return self.hub.sync(self.actor(name), {"workspace": workspace, **body})

    def brief(self, key="game:987654"):
        return next(row["project"] for row in self.hub.workspaces_summary(self.admin())["workspaces"] if row["key"] == key)

    def test_want_project_until_the_hub_has_the_announced_snapshot(self):
        ana, dan = self.register("ana"), self.register("dan")
        response = self.sync("ana")
        self.assertEqual((response["want_project"], self.brief()), (False, None))
        for digest in ("", "s" * 129, 4, None):
            with self.subTest(digest=digest):
                self.assertFalse(self.sync("ana", project_digest=digest)["want_project"])
        self.assertTrue(self.sync("ana", project_digest="snap-1")["want_project"])
        self.assertIsNone(self.hub.workspaces["game:987654"]["project"])
        response = self.sync("ana", project_digest="snap-1", project=project_payload())
        self.assertFalse(response["want_project"])
        self.assertEqual(self.brief(), {"count": len(inventory()), "digest": "snap-1", "groups": {"assets": 4, "other": len(inventory()) - 4}})
        stored = self.hub.project(self.actor("dan"), "game:987654")["project"]
        self.assertEqual((stored["developer"], stored["machine"], stored["reported_by"], stored["received"], stored["studio_id"], stored["taken"]),
                         ("ana", "pc-ana", ana, 1000.0, "studio-1", 900.0))
        self.assertEqual(stored["nodes"], inventory())
        self.assertTrue(any("proiect primit de la ana @ pc-ana (" in line and "pentru Ball (game:987654): " + str(len(inventory())) + " noduri" in line
                            for line in self.lines))
        self.assertFalse(self.sync("ana", project_digest="snap-1")["want_project"])
        # Raportorul care rescanează este întrebat imediat; alt dispozitiv doar după fereastra de reîmprospătare (300 s).
        self.assertTrue(self.sync("ana", project_digest="snap-2")["want_project"])
        self.assertFalse(self.sync("dan", project_digest="d-1")["want_project"])
        self.now[0] = 1000.0 + team_hub.PROJECT_REFRESH_SECONDS
        self.assertTrue(self.sync("dan", project_digest="d-1")["want_project"])
        self.assertEqual(team_hub.PROJECT_REFRESH_SECONDS, 300)
        # Fără taken valid hub-ul pune ora primirii; proiectul lui dan îl înlocuiește pe al anei în același workspace.
        self.sync("dan", project_digest="d-1", project=project_payload("d-1", taken="x", count=-3, place_name=None))
        stored = self.hub.project(self.actor("ana"), "game:987654")["project"]
        self.assertEqual((stored["reported_by"], stored["snapshot_id"], stored["taken"], stored["count"], stored["place_name"]),
                         (dan, "d-1", 1300.0, len(inventory()), ""))
        self.assertEqual(self.brief()["digest"], "d-1")
        # Proiectul se atașează workspace-ului curent al dispozitivului: eva lucrează în Kart, iar Ball rămâne neschimbat.
        self.register("eva", KART)
        self.assertTrue(self.sync("eva", KART, project_digest="k-1")["want_project"])
        self.sync("eva", KART, project_digest="k-1", project=project_payload("k-1", place_name="Kart"))
        self.assertEqual((self.brief("game:222")["digest"], self.brief()["digest"]), ("k-1", "d-1"))
        # Fără workspace curent (Studio închis) nu se cere nimic.
        self.assertFalse(self.sync("eva", None, project_digest="k-9")["want_project"])

    def test_invalid_projects_are_ignored_and_not_requested_again(self):
        self.register("ana")
        invalid = [
            "x", {"snapshot_id": "s"}, project_payload(nodes=[[0, -1, "Workspace", "Workspace"]]), project_payload(nodes="x"),
            project_payload(groups=[]), project_payload(groups={"assets": "x"}), project_payload(snapshot_id=""), project_payload(place_id=1.5),
            project_payload(place_name="n" * 201), project_payload(nodes=[[index, -1, "P", "P", 0] for index in range(team_hub.MAX_PROJECT_NODES + 1)]),
        ]
        for project in invalid:
            with self.subTest(project=str(project)[:60]):
                response = self.sync("ana", project_digest="snap-1", project=project)
                self.assertEqual((response["want_project"], self.brief()), (False, None))
        self.assertTrue(any("proiect invalid de la ana @ pc-ana (" in line and "pentru game:987654; ignorat" in line for line in self.lines))
        # Alt snapshot este cerut; un proiect valid șterge marcajul de respingere.
        self.assertTrue(self.sync("ana", project_digest="snap-2")["want_project"])
        self.assertFalse(self.sync("ana", project_digest="snap-2", project=project_payload("snap-2"))["want_project"])
        self.assertEqual(self.brief()["digest"], "snap-2")
        self.assertEqual(team_hub.MAX_PROJECT_NODES, 20000)

    def test_project_lookup_workspace_detail_and_panel_data(self):
        ana, dan = self.register("ana"), self.register("dan")
        for key, status in ((None, 400), ("", 400), ("game:1", 404), ("x", 404)):
            with self.subTest(key=key), self.assertRaises(HubError) as error:
                self.hub.project(self.actor("ana"), key)
            self.assertEqual(error.exception.status, status)
        self.assertEqual(self.hub.project(self.actor("ana"), "game:987654"), {"ok": True, "project": None})
        # Un sync real acceptă cel mult `MAX_SYNC_JOURNAL` (32) intrări; aici ridicăm plafonul ca să umplem jurnalul workspace-ului dintr-o singură cerere.
        with patch.object(team_hub, "MAX_SYNC_JOURNAL", team_hub.WORKSPACE_JOURNAL + 5):
            self.sync("ana", project_digest="snap-1", project=project_payload(),
                      sessions=[session_row("job-a", events=[{"seq": 1, "type": "text", "text": "x"}])],
                      journal=[{"tool": "multi_edit", "paths": ["Workspace.Map"], "summary": "s" + str(index)} for index in range(team_hub.WORKSPACE_JOURNAL + 5)])
        self.hub.claim(self.actor("ana"), {"job_id": "job-a", "paths": ["Workspace.Map"], "reason": "harta"})
        full = self.hub.project(self.actor("dan"), "game:987654")["project"]
        self.assertEqual((full["snapshot_id"], full["developer"], len(full["nodes"]), sorted(full["groups"])), ("snap-1", "ana", len(inventory()), ["assets", "other"]))
        self.now[0] = 1001.5
        detail = self.hub.workspace(self.actor("dan"), "game:987654")
        self.assertEqual(set(detail), {"ok", "workspace", "members", "sessions", "claims", "journal", "project"})
        self.assertEqual(detail["workspace"], {**BALL, "first_seen": 1000.0, "last_seen": 1000.0})
        self.assertEqual([(row["roblox_name"], row["online"]) for row in detail["members"]], [("ana", True), ("dan", True)])
        self.assertEqual([(row["job_id"], row["developer"], row["machine"], row["state"], row["workspace"]) for row in detail["sessions"]],
                         [("job-a", "ana", "pc-ana", "running", "game:987654")])
        self.assertNotIn("events", detail["sessions"][0])
        self.assertEqual([(row["path"], row["developer"], row["job_id"], row["workspace"]) for row in detail["claims"]], [("Workspace.Map", "ana", "job-a", "game:987654")])
        self.assertEqual(len(detail["journal"]), team_hub.WORKSPACE_JOURNAL)
        self.assertEqual((detail["journal"][0]["seq"], detail["journal"][-1]["seq"], detail["journal"][-1]["developer"], detail["journal"][-1]["workspace"]),
                         (6, 205, "ana", "game:987654"))
        self.assertEqual(detail["project"], {"digest": "snap-1", "snapshot_id": "snap-1", "count": len(inventory()), "truncated": False,
                                             "groups": {"assets": 4, "other": len(inventory()) - 4}, "reported_by": ana, "at": 1000.0})
        # panel-data: eu, hub, sumarul workspace-urilor, workspace-ul selectat (implicit cel curent); doar adminul vede pending_devices.
        data = self.hub.panel_data(self.actor("dan"))
        self.assertEqual(set(data), {"ok", "me", "hub", "workspaces", "selected"})
        self.assertEqual(data["me"], {"device_id": dan, "status": "approved", "roblox_user_id": USER_IDS["dan"], "roblox_name": "dan",
                                      "machine": "pc-dan", "workspace": "game:987654", "admin": False})
        self.assertEqual(data["hub"], {"hub_id": self.hub.hub_id, "version": "1.0.0", "enrollment": "open", "now": 1001.5})
        self.assertEqual([(row["key"], row["project"]["digest"], len(row["members_online"])) for row in data["workspaces"]], [("game:987654", "snap-1", 2)])
        self.assertEqual(data["selected"], {key: value for key, value in detail.items() if key != "ok"})
        self.assertIsNone(self.hub.panel_data(self.actor("dan"), "game:1")["selected"])
        self.hub.enrollment = "approve"
        self.assertEqual(self.hub.register(self.actor("eva"), {"roblox": None, "machine": "pc-eva", "bridge_id": "b", "version": "1.0.0", "workspace": None})["status"], "pending")
        data = self.hub.panel_data(self.admin(), "game:987654")
        self.assertEqual((data["me"]["admin"], data["me"]["status"], data["pending_devices"], data["selected"]["workspace"]["key"]), (True, "admin", 1, "game:987654"))
        self.assertNotIn(TOKEN, json.dumps(data))
        self.assertEqual(team_hub.WORKSPACE_JOURNAL, 200)


class HubPanelHttpTests(unittest.TestCase):
    """Rutele HTTP ale hub-ului pentru panou și proiect: /panel public, /hub/project și /hub/panel-data cu antet de dispozitiv aprobat sau admin."""

    def setUp(self):
        self.temp = temp_dir()
        self.app = Path(self.temp.name) / "app"
        (self.app / "panel").mkdir(parents=True)
        (self.app / "panel" / "index.html").write_bytes(PANEL_HTML)
        self.lines = []
        self.hub = Hub(TOKEN, logger=self.lines.append, app_dir=self.app, avatar_fetcher=lambda user, size: None)
        self.server = HubServer("127.0.0.1", 0, self.hub)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def request(self, path, body=None, headers=None, admin=TOKEN, device=None, method=None):
        all_headers = {"Content-Type": "application/json"}
        if admin is not None:
            all_headers["X-Studio-Harness-Admin"] = admin
        if device is not None:
            all_headers["X-Studio-Harness-Device"] = device_token(device)
        all_headers.update(headers or {})
        request = Request(self.base + path, data=None if body is None else json.dumps(body).encode(), headers=all_headers, method=method)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            return error.code, error.read(), error.headers

    def register(self, name="ana", approve=True):
        status, body, _ = self.request("/hub/register", {"roblox": {"user_id": USER_IDS[name], "name": name}, "machine": "pc-" + name,
                                                          "bridge_id": "b", "version": "1.0.0", "workspace": BALL}, admin=None, device=name)
        self.assertEqual(status, 202)
        if approve:
            self.assertEqual(self.request("/hub/admin/devices/approve", {"device_id": device_id(name)})[0], 200)
        return json.loads(body)["device_id"]

    def temp_path(self):
        return Path(self.temp.name)

    def test_panel_page_is_served_without_headers_with_security_headers(self):
        for path in ("/panel", "/panel/"):
            with self.subTest(path=path):
                status, body, headers = self.request(path, admin=None)
                self.assertEqual((status, body), (200, PANEL_HTML))
                self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
                self.assertEqual(headers["Content-Security-Policy"],
                                 "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                                 "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                self.assertEqual(headers["X-Frame-Options"], "DENY")
                self.assertEqual(headers["Referrer-Policy"], "no-referrer")
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(headers["Server"], "StudioHarnessHub/1.0")
                self.assertNotIn("Access-Control-Allow-Origin", headers)
        # Alte căi sub /panel nu sunt fișiere statice; POST nu servește pagina; rutele de membru cer antet (401 `unknown`).
        status, body, _ = self.request("/panel/index.html", admin=None)
        self.assertEqual((status, json.loads(body)), (404, {"ok": False, "error": "Rută inexistentă."}))
        self.assertEqual(self.request("/panel", {}, admin=None)[0], 404)
        self.assertEqual(self.request("/panel", {})[0], 404)
        self.assertEqual(self.request("/panel/../hub/status", admin=None)[0], 404)
        status, body, _ = self.request("/hub/status", admin=None)
        self.assertEqual((status, json.loads(body)["status"]), (401, "unknown"))
        # Prefixul public /roblox/harness este acceptat și eliminat.
        self.assertEqual(self.request("/roblox/harness/panel", admin=None)[:2], (200, PANEL_HTML))

    def test_panel_is_404_when_the_file_is_missing_and_found_in_the_repo_layout_too(self):
        (self.app / "panel" / "index.html").unlink()
        status, body, headers = self.request("/panel", admin=None)
        self.assertEqual((status, json.loads(body)), (404, {"ok": False, "error": "Panoul web nu este instalat: panel/index.html lipsește lângă team_hub.py."}))
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        # Checkout de repo: team_hub.py în scripts/, panoul în ../panel/index.html.
        (self.temp_path() / "panel").mkdir()
        (self.temp_path() / "panel" / "index.html").write_bytes(b"<p>din repo</p>")
        self.assertEqual(self.request("/panel", admin=None)[:2], (200, b"<p>din repo</p>"))
        self.assertEqual(team_hub.panel_file(self.app), self.temp_path() / "panel" / "index.html")
        # Fără app_dir explicit se caută lângă team_hub.py (fișierul real al modulului).
        with patch.object(team_hub, "__file__", str(self.app / "team_hub.py")):
            self.assertEqual(team_hub.panel_file(), self.temp_path() / "panel" / "index.html")
        self.assertIsNone(team_hub.panel_file(Path(self.temp.name) / "gol" / "app"))

    def test_same_origin_is_accepted_and_foreign_origins_are_refused(self):
        same = {"Origin": self.base}
        status, body, _ = self.request("/hub/status", headers=same)
        self.assertEqual((status, json.loads(body)["ok"]), (200, True))
        self.assertEqual(self.request("/panel", headers=same, admin=None)[0], 200)
        self.assertEqual(self.request("/healthz", headers={"Origin": self.base.upper()}, admin=None)[0], 200)
        for origin in ("https://example.com", "null", f"http://localhost:{self.port}", f"http://127.0.0.1:{self.port + 1}", "127.0.0.1:" + str(self.port), ""):
            with self.subTest(origin=origin):
                status, body, _ = self.request("/hub/status", headers={"Origin": origin})
                expected = 200 if origin == "" else 403
                self.assertEqual(status, expected, body)
                if expected == 403:
                    self.assertEqual(json.loads(body), {"ok": False, "error": "Accesul din pagini web nu este permis."})
                    self.assertEqual(self.request("/panel", headers={"Origin": origin}, admin=None)[0], 403)
        self.assertEqual(self.request("/hub/status", method="OPTIONS")[0], 403)

    def test_project_and_panel_data_routes_need_an_approved_device_or_the_admin_code(self):
        pending = self.register("dan", approve=False)
        for path in ("/hub/project?key=game:987654", "/hub/panel-data", "/hub/workspaces", "/hub/workspace?key=game:987654"):
            with self.subTest(path=path):
                status, body, _ = self.request(path, admin=None)
                self.assertEqual((status, json.loads(body)["status"]), (401, "unknown"))
                self.assertEqual(self.request(path, admin="alt-cod-0123456789")[0], 401)
                status, body, _ = self.request(path, admin=None, device="dan")
                self.assertEqual((status, json.loads(body)["status"], json.loads(body)["device_id"]), (403, "pending", pending))
                self.assertEqual(self.request(path, {}, method="POST")[0], 404)
        ana = self.register("ana")
        self.assertEqual(json.loads(self.request("/hub/project?key=game:987654", admin=None, device="ana")[1]), {"ok": True, "project": None})
        self.assertEqual(self.request("/hub/project?key=game:1")[0], 404)
        self.assertEqual(self.request("/hub/project")[0], 400)
        nodes = inventory()
        project = {"snapshot_id": "snap-1", "place_id": 1291603, "place_name": "Ball", "studio_id": None, "taken": 5.0, "count": len(nodes), "truncated": False,
                   "nodes": nodes, "groups": {"assets": {"key": "assets", "label": "Asseturi", "count": 4, "by_class": {"Part": 4}, "entries": []}}}
        status, body, _ = self.request("/hub/sync", {"workspace": BALL, "project_digest": "snap-1"}, admin=None, device="ana")
        self.assertEqual((status, json.loads(body)["want_project"]), (200, True))
        status, body, _ = self.request("/hub/sync", {"workspace": BALL, "project_digest": "snap-1", "project": project}, admin=None, device="ana")
        self.assertEqual((status, json.loads(body)["want_project"]), (200, False))
        full = json.loads(self.request("/hub/project?key=game:987654")[1])["project"]
        self.assertEqual((full["snapshot_id"], full["developer"], full["machine"], full["nodes"], full["count"], full["reported_by"]),
                         ("snap-1", "ana", "pc-ana", nodes, len(nodes), ana))
        rows = json.loads(self.request("/hub/workspaces", admin=None, device="ana")[1])["workspaces"]
        self.assertEqual([(row["key"], row["project"], [member["roblox_name"] for member in row["members_online"]]) for row in rows],
                         [("game:987654", {"count": len(nodes), "digest": "snap-1", "groups": {"assets": 4}}, ["ana"])])
        data = json.loads(self.request("/hub/panel-data?workspace=game:987654", admin=None, device="ana")[1])
        self.assertEqual((data["ok"], data["hub"]["version"], data["me"]["roblox_name"], data["selected"]["project"]["digest"]), (True, "1.0.0", "ana", "snap-1"))
        self.assertNotIn("pending_devices", data)
        data = json.loads(self.request("/hub/panel-data")[1])
        self.assertEqual((data["me"]["admin"], data["pending_devices"], data["selected"]), (True, 1, None))
        self.assertNotIn(TOKEN, json.dumps(data))
        self.assertNotIn(device_token("ana"), json.dumps(data))


class HubNative(FakeNative):
    def list_tools(self):
        return super().list_tools() + [{"name": "get_studio_state", "description": "Test", "inputSchema": {
            "type": "object", "properties": {"studio_id": {"type": "string"}}}}]


class HubProjectIntegrationTests(unittest.TestCase):
    """Hub real pe port efemer și două daemon-uri (clienți hub fără fir, conduși din test): proiectul anei ajunge în hub la cerere și la dan,
    în același workspace, prin hub_project."""

    def setUp(self):
        self.temp = temp_dir()
        self.lines = []
        self.hub = Hub(TOKEN, logger=self.lines.append, avatar_fetcher=lambda user, size: None)
        self.hub.enrollment = "open"
        self.server = HubServer("127.0.0.1", 0, self.hub)
        self.hub_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.bridges = []

    def tearDown(self):
        for bridge in self.bridges:
            bridge.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    @staticmethod
    def factory(bridge, hub_url, token, machine, **extra):
        return HubClient(bridge, hub_url, token, machine, autostart=False, **extra)

    def make_bridge(self, name):
        base = Path(self.temp.name) / name
        bridge = Bridge(FakeProviders(), base / "runtime", HubNative(), UI_TOKEN, developer=name, state_directory=base / "state",
                        hub_url=self.hub_url, device_token=device_token(name), hub_client_factory=self.factory)
        self.bridges.append(bridge)
        bridge.set_identity({**IDENTITY, "user_id": USER_IDS[name], "name": name})
        bridge.hub.register()
        self.assertEqual(bridge.hub_status()["status"], "approved")
        return bridge

    def test_project_is_sent_once_on_request_and_shared_within_the_workspace(self):
        ana, dan = self.make_bridge("ana"), self.make_bridge("dan")
        job_a, job_b = ana.create_terminal_session({"provider": "claude"}), dan.create_terminal_session({"provider": "codex"})
        self.assertEqual((job_a.workspace, job_b.workspace, ana.developer, dan.developer), ("game:987654", "game:987654", "ana", "dan"))
        response = ana.hub.sync_once()
        self.assertEqual((response["want_project"], ana.hub.want_project, response["workspace"]), (False, False, "game:987654"))
        self.assertEqual([row["roblox_name"] for row in response["members"]], ["ana", "dan"])
        woken = []
        ana.hub.wake = lambda: woken.append(True)
        nodes = inventory(parts=5)
        for index, part in enumerate(chunks(nodes, 2)):
            ana.project_chunk(chunk_body("snap-1", index, 2, part, place_name="Locul lui ana", game_id=987654, place_id=1291603))
        self.assertEqual((woken, ana.project["workspace"]), ([True], "game:987654"))
        # Primul sync anunță snapshot-ul; hub-ul îl cere, iar clientul îl trimite la sync-ul următor (o singură dată).
        seen = []
        original = ana.hub._request

        def spy(method, path, body=None, timeout=10):
            if path == "/hub/sync":
                seen.append((body.get("project_digest"), "project" in body, timeout))
            return original(method, path, body, timeout)

        ana.hub._request = spy
        response = ana.hub.sync_once()
        self.assertEqual((response["want_project"], ana.hub.want_project), (True, True))
        self.assertEqual(seen, [("snap-1", False, 15)])
        self.assertIsNone(self.hub.workspaces["game:987654"]["project"])
        response = ana.hub.sync_once()
        self.assertEqual((response["want_project"], ana.hub.want_project), (False, False))
        self.assertEqual(seen[-1], ("snap-1", True, 30))
        stored = self.hub.workspaces["game:987654"]["project"]
        self.assertEqual((stored["nodes"], stored["developer"], stored["reported_by"], stored["place_name"]), (nodes, "ana", device_id("ana"), "Locul lui ana"))
        self.assertEqual(response["workspaces"][0]["project"], {"count": len(nodes), "digest": "snap-1", "groups": {**{key: 0 for key in GROUP_KEYS}, "graphics": 2, "assets": 6, "audio": 1, "ui": 1,
                                                                                                               "scripts_server": 1, "scripts_client": 1, "scripts_shared": 1, "networking": 1,
                                                                                                               "data": 1, "physics": 1, "gameplay": 1, "settings": 4, "other": 1}})
        ana.hub.sync_once()
        self.assertEqual(seen[-1], ("snap-1", False, 15))
        # dan nu are proiect local: hub_project și hub_board aduc proiectul workspace-ului din hub, o singură dată per digest.
        dan.hub.sync_once()
        overview = json.loads(text(dan.agent_call(job_b, "hub_project", {})))
        self.assertEqual((overview["place_name"], overview["developer"], overview["count"], overview["snapshot_id"], overview["workspace"]),
                         ("Locul lui ana", "ana", len(nodes), "snap-1", "game:987654"))
        self.assertEqual(overview["groups"]["assets"]["sample"][:2], ["Workspace.Map", "Workspace.Map.P0"])
        self.assertEqual(set(dan.hub.project_cache), {"game:987654"})
        cached = dan.hub.project_cache["game:987654"]
        full = json.loads(text(dan.agent_call(job_b, "hub_project", {"group": "assets"})))
        self.assertEqual((full["group"]["count"], len(full["group"]["entries"]), full["workspace"]), (6, 6, "game:987654"))
        self.assertIs(dan.hub.project_cache["game:987654"], cached)
        board = json.loads(text(dan.agent_call(job_b, "hub_board", {})))
        self.assertEqual((board["project"]["place_name"], board["project"]["count"], board["project"]["groups"]["assets"]), ("Locul lui ana", len(nodes), 6))
        self.assertEqual((board["hub"]["status"], board["hub"]["notice"], board["workspace"]["key"]), ("approved", None, "game:987654"))
        self.assertEqual({row["job_id"] for row in board["sessions"]}, {job_a.id, job_b.id})
        self.assertIsNone(dan.status()["project"])
        self.assertEqual(dan.project_view(), {"ok": True, "project": None})
        # Proiectul propriu are prioritate doar dacă este al workspace-ului jobului: un alt joc scanat local nu îl înlocuiește pe cel din hub.
        own = inventory(parts=1)
        dan.project_chunk(chunk_body("d-alt", 0, 1, own, place_name="Alt joc al lui dan", game_id=555))
        self.assertEqual(json.loads(text(dan.agent_call(job_b, "hub_project", {})))["place_name"], "Locul lui ana")
        dan.project_chunk(chunk_body("d-1", 0, 1, own, place_name="Locul lui dan", game_id=987654, place_id=1291603))
        self.assertEqual(json.loads(text(dan.agent_call(job_b, "hub_project", {})))["place_name"], "Locul lui dan")
        # Snapshot-ul nou al anei este cerut din nou de hub; dan îl aduce din nou când digest-ul din oglindă se schimbă.
        ana.project_chunk(chunk_body("snap-2", 0, 1, own, place_name="Locul lui ana v2", game_id=987654, place_id=1291603))
        self.assertTrue(ana.hub.sync_once()["want_project"])
        self.assertFalse(ana.hub.sync_once()["want_project"])
        self.assertFalse(dan.hub.sync_once()["want_project"])
        dan.project = None
        self.assertEqual(json.loads(text(dan.agent_call(job_b, "hub_project", {})))["place_name"], "Locul lui ana v2")
        self.assertEqual(json.loads(text(ana.agent_call(job_a, "hub_project", {})))["place_name"], "Locul lui ana v2")
        # Hub oprit: primul apel (touch-ul dinaintea toolului) descoperă căderea și trece în offline, dar dan păstrează proiectul adus
        # cât timp digest-ul din oglindă nu se schimbă; fără el, refuz explicit.
        self.server.shutdown()
        self.server.server_close()
        stale = json.loads(text(dan.agent_call(job_b, "hub_project", {})))
        self.assertEqual((stale["snapshot_id"], stale["place_name"]), ("snap-2", "Locul lui ana v2"))
        self.assertEqual(dan.hub_status()["status"], "offline")
        self.assertIn("stare: offline", json.loads(text(dan.agent_call(job_b, "hub_board", {})))["hub"]["notice"])
        dan.hub.project_cache = {}
        missing = dan.agent_call(job_b, "hub_project", {})
        self.assertTrue(missing["isError"])
        self.assertEqual(text(missing), studio_bridge.NO_PROJECT)
        with self.assertRaises(ClientError):
            dan.hub.sync_once()
        self.assertEqual(dan.hub_status()["status"], "offline")


if __name__ == "__main__":
    unittest.main()

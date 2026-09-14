"""Modelul de claims: normalizare, conflicte, literale Luau și tabela cu ceas injectat. Fără rețea, fără Studio."""

import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import claims
from claims import ClaimTable, conflicts, display, luau_literals, normalize, related, within


class NormalizeTests(unittest.TestCase):
    def test_game_prefix_workspace_alias_and_separators(self):
        for text, expected in (
            ("game.Workspace.Map.Zone3", ("Workspace", "Map", "Zone3")),
            ("workspace.Map.Zone3", ("Workspace", "Map", "Zone3")),
            ("WORKSPACE", ("Workspace",)),
            ("Workspace/Map/Zone3", ("Workspace", "Map", "Zone3")),
            ("game/ServerScriptService/Main", ("ServerScriptService", "Main")),
            ("  Lighting . Atmosphere  ", ("Lighting", "Atmosphere")),
            ("ReplicatedStorage", ("ReplicatedStorage",)),
        ):
            with self.subTest(text=text):
                self.assertEqual(normalize(text), expected)

    def test_special_claims(self):
        self.assertEqual(normalize("@play:studio-1"), ("@play", "studio-1"))
        self.assertEqual(normalize("@play"), ("@play",))
        self.assertEqual(normalize("@all"), ("@all",))
        self.assertEqual(display(("@play", "studio-1")), "@play:studio-1")
        self.assertEqual(display(("Workspace", "Map")), "Workspace.Map")

    def test_rejections(self):
        for value in ("", "   ", "game", "game.", "Workspace..Map", ".Workspace", "Workspace.", "@other", "@run:x",
                      "Workspace.Ma\x00p", "Workspace\tMap", "x" * 513, ".".join(["A"] * 65), 42, None, ["Workspace"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize(value)


class RelationTests(unittest.TestCase):
    def test_conflicts_on_ancestor_equal_and_descendant(self):
        zone = ("Workspace", "Map", "Zone3")
        self.assertTrue(conflicts(zone, ("Workspace", "Map")))
        self.assertTrue(conflicts(("Workspace", "Map"), zone))
        self.assertTrue(conflicts(zone, zone))
        self.assertTrue(conflicts(("Workspace",), zone))
        self.assertFalse(conflicts(zone, ("Workspace", "Map", "Zone4")))
        self.assertFalse(conflicts(zone, ("ServerScriptService", "Main")))
        self.assertFalse(conflicts(("Workspace", "Map", "Zone3", "Part"), ("Workspace", "Map", "Zone30")))

    def test_special_conflicts(self):
        self.assertTrue(conflicts(("@all",), ("Workspace",)))
        self.assertTrue(conflicts(("@play", "a"), ("@all",)))
        self.assertTrue(conflicts(("@play", "a"), ("@play", "a")))
        self.assertFalse(conflicts(("@play", "a"), ("@play", "b")))
        self.assertFalse(conflicts(("@play", "a"), ("Workspace",)))
        self.assertFalse(conflicts(("Workspace",), ("@play", "a")))

    def test_within_requires_descendant_or_equal(self):
        claim = ("Workspace", "Map")
        self.assertTrue(within(claim, claim))
        self.assertTrue(within(("Workspace", "Map", "Zone3"), claim))
        self.assertFalse(within(("Workspace",), claim))
        self.assertFalse(within(("Workspace", "Mapa"), claim))
        self.assertTrue(within(("Lighting",), ("@all",)))
        self.assertTrue(within(("@play", "a"), ("@all",)))
        self.assertTrue(within(("@play", "a"), ("@play", "a")))
        self.assertFalse(within(("@play", "a"), ("@play", "b")))
        self.assertFalse(within(("Workspace",), ("@play", "a")))
        self.assertFalse(within(("@play", "a"), ("Workspace",)))

    def test_related_also_accepts_ancestors_of_the_claim(self):
        claim = ("Workspace", "Map", "Zone3")
        self.assertTrue(related(("Workspace",), claim))
        self.assertTrue(related(("Workspace", "Map"), claim))
        self.assertTrue(related(claim, claim))
        self.assertTrue(related(("Workspace", "Map", "Zone3", "Part"), claim))
        self.assertFalse(related(("Workspace", "Map", "Zone4"), claim))
        self.assertFalse(related(("Lighting",), claim))
        self.assertTrue(related(("Lighting",), ("@all",)))
        self.assertFalse(related(("@play", "a"), ("Workspace",)))
        self.assertFalse(related(("Workspace",), ("@play", "a")))


class LuauLiteralTests(unittest.TestCase):
    def test_extracts_game_workspace_and_get_service_chains(self):
        code = '''
            local zone = workspace.Map.Zone4
            local light = game:GetService("Lighting").ClockTime
            local main = game.ServerScriptService.Main
            game:GetService('Lighting').ClockTime = 12
            local ws = game . Workspace . Baseplate
        '''
        self.assertEqual(luau_literals(code), [
            ("Workspace", "Map", "Zone4"),
            ("ServerScriptService", "Main"),
            ("Workspace", "Baseplate"),
            ("Lighting", "ClockTime"),
        ])

    def test_unchecked_services_and_non_paths_are_ignored(self):
        code = '''
            local run = game:GetService("RunService").Heartbeat
            local http = game.HttpService
            local ws = workspace
            local mine = myworkspace.Foo
            local text = "game.Workspace" -- literalul din text este tot o cale
            local players = game:GetService("Players")
        '''
        self.assertEqual(luau_literals(code), [("Workspace",), ("Players",)])
        self.assertEqual(luau_literals(""), [])
        self.assertEqual(luau_literals("print(1)"), [])

    def test_known_bypasses_are_documented_as_best_effort_not_as_enforcement(self):
        """Contract §7: detecția previne greșeli, nu ocoliri. Formele de mai jos ating subarbori nerevendicați și nu sunt văzute.

        Testul fixează limita ca pe o decizie, nu ca pe un accident: dacă cineva o extinde, contractul și docstring-ul trebuie
        actualizate în aceeași schimbare; dacă cineva ar prezenta mecanismul ca pe o barieră de securitate, aici se vede că nu este."""
        held = (("Workspace", "Other"),)

        def refused(code):
            """Exact filtrul din `studio_bridge._enforce_claims`: literalele care nu sunt rudă cu niciun claim al jobului."""
            return [display(path) for path in luau_literals(code) if not any(related(path, claim) for claim in held)]

        self.assertEqual(refused("game.Workspace.Map.Zone3.Transparency = 1"), ["Workspace.Map.Zone3.Transparency"])
        for code in ('game.Workspace["Map"].Zone3.Transparency = 1',
                     'game:FindFirstChild("Workspace").Map.Zone3:Destroy()',
                     'local s = game\ns.Workspace.Map.Zone3.Anchored = true',
                     'game:GetService("Work" .. "space").Map.Zone3:Destroy()',
                     'local root = workspace\nroot.Map.Zone3.Name = "x"'):
            with self.subTest(code=code.splitlines()[0]):
                self.assertEqual(refused(code), [])
        # Limita este scrisă și în cod, unde o citește cine s-ar baza pe ea.
        self.assertIn("nu o barieră de securitate", luau_literals.__doc__)

    def test_duplicates_are_reported_once(self):
        code = "workspace.Map.Zone4.Transparency = 1\nworkspace.Map.Zone4.Anchored = true"
        self.assertEqual(luau_literals(code), [("Workspace", "Map", "Zone4", "Transparency"), ("Workspace", "Map", "Zone4", "Anchored")])
        code = "game.Workspace.Map\nworkspace.Map"
        self.assertEqual(luau_literals(code), [("Workspace", "Map")])


class ClaimTableTests(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.table = ClaimTable(idle_seconds=10, clock=lambda: self.now[0])

    def paths(self, job_id):
        return sorted(display(claim.path) for claim in self.table.held_by(job_id))

    def test_claim_is_all_or_nothing_and_names_the_holder(self):
        added, found = self.table.claim("job-a", "ana", ["Workspace.Map.Zone3"], "zona 3")
        self.assertEqual(found, [])
        self.assertEqual([display(claim.path) for claim in added], ["Workspace.Map.Zone3"])
        added, found = self.table.claim("job-b", "dan", ["Workspace.Map.Zone4", "game.Workspace.Map"])
        self.assertEqual(added, [])
        self.assertEqual(found, [{"path": "Workspace.Map", "holder": "job-a", "developer": "ana",
                                  "held_path": "Workspace.Map.Zone3", "since": 1000.0}])
        # Nici Zone4, calea fără conflict, nu a fost revendicată.
        self.assertEqual(self.paths("job-b"), [])
        self.assertEqual(self.paths("job-a"), ["Workspace.Map.Zone3"])

    def test_same_job_may_reclaim_and_overlap_itself(self):
        self.table.claim("job-a", "ana", ["Workspace.Map"])
        added, found = self.table.claim("job-a", "ana", ["Workspace.Map", "Workspace.Map.Zone3"])
        self.assertEqual(found, [])
        self.assertEqual([display(claim.path) for claim in added], ["Workspace.Map", "Workspace.Map.Zone3"])
        self.assertEqual(self.paths("job-a"), ["Workspace.Map", "Workspace.Map.Zone3"])

    def test_invalid_or_empty_paths_claim_nothing(self):
        with self.assertRaises(ValueError):
            self.table.claim("job-a", "ana", [])
        with self.assertRaises(ValueError):
            self.table.claim("job-a", "ana", ["Workspace.Map", "@bogus"])
        self.assertEqual(self.table.snapshot(), [])

    def test_special_claims_conflict_rules(self):
        self.table.claim("job-a", "ana", ["@play:studio-1"])
        _, found = self.table.claim("job-b", "dan", ["@play:studio-1"])
        self.assertEqual(found[0]["holder"], "job-a")
        self.assertEqual(self.table.claim("job-b", "dan", ["@play:studio-2", "Workspace"])[1], [])
        _, found = self.table.claim("job-c", "ema", ["@all"])
        self.assertEqual({item["held_path"] for item in found}, {"@play:studio-1", "@play:studio-2", "Workspace"})

    def test_release_partial_and_total(self):
        self.table.claim("job-a", "ana", ["Workspace.Map.Zone3", "Workspace.Map.Zone4", "Lighting"])
        self.table.claim("job-b", "dan", ["ServerStorage"])
        released = self.table.release("job-a", ["workspace.Map.Zone3", "Workspace.Absent"])
        self.assertEqual([display(claim.path) for claim in released], ["Workspace.Map.Zone3"])
        self.assertEqual(self.paths("job-a"), ["Lighting", "Workspace.Map.Zone4"])
        released = self.table.release("job-a")
        self.assertEqual(sorted(display(claim.path) for claim in released), ["Lighting", "Workspace.Map.Zone4"])
        self.assertEqual(self.paths("job-a"), [])
        self.assertEqual(self.paths("job-b"), ["ServerStorage"])
        self.assertEqual(self.table.release("job-absent"), [])
        with self.assertRaises(ValueError):
            self.table.release("job-b", ["@nope"])

    def test_touch_and_expiry_use_injected_clock(self):
        self.table.claim("job-a", "ana", ["Workspace.Map.Zone3"])
        self.now[0] = 1009.0
        self.assertEqual(self.paths("job-a"), ["Workspace.Map.Zone3"])
        self.table.touch("job-a")
        self.now[0] = 1018.0
        self.assertEqual(self.paths("job-a"), ["Workspace.Map.Zone3"])
        self.assertEqual(self.table.snapshot()[0]["expires"], 1019.0)
        self.now[0] = 1019.5
        expired = self.table.expire()
        self.assertEqual([display(claim.path) for claim in expired], ["Workspace.Map.Zone3"])
        self.assertEqual(self.paths("job-a"), [])
        self.assertEqual(self.table.expire(), [])

    def test_expired_claim_no_longer_blocks_others(self):
        self.table.claim("job-a", "ana", ["Workspace.Map"])
        self.now[0] = 1011.0
        self.assertIsNone(self.table.holder(("Workspace", "Map", "Zone3")))
        added, found = self.table.claim("job-b", "dan", ["Workspace.Map.Zone3"])
        self.assertEqual(found, [])
        self.assertEqual(len(added), 1)

    def test_covers_related_and_holder(self):
        self.table.claim("job-a", "ana", ["Workspace.Map.Zone3"])
        self.assertTrue(self.table.covers("job-a", ("Workspace", "Map", "Zone3", "Part")))
        self.assertFalse(self.table.covers("job-a", ("Workspace", "Map")))
        self.assertTrue(self.table.related_to("job-a", ("Workspace", "Map")))
        self.assertFalse(self.table.related_to("job-a", ("Workspace", "Map", "Zone4")))
        self.assertFalse(self.table.covers("job-b", ("Workspace", "Map", "Zone3")))
        holder = self.table.holder(("Workspace",), exclude_job="job-b")
        self.assertEqual((holder.job_id, holder.developer), ("job-a", "ana"))
        self.assertIsNone(self.table.holder(("Workspace",), exclude_job="job-a"))
        self.assertIsNone(self.table.holder(("Lighting",)))

    def test_wait_free_times_out_while_held(self):
        self.table.claim("job-a", "ana", ["Workspace.Map"])
        started = time.monotonic()
        self.assertFalse(self.table.wait_free(("Workspace", "Map", "Zone3"), 0.1))
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(self.table.wait_free(("Lighting",), 0))
        cancelled = threading.Event()
        cancelled.set()
        self.assertFalse(self.table.wait_free(("Workspace", "Map"), 5, cancelled))

    def test_wait_free_wakes_when_released_from_another_thread(self):
        self.table.claim("job-a", "ana", ["Workspace.Map"])
        results = []
        waiter = threading.Thread(target=lambda: results.append(self.table.wait_free(("Workspace", "Map", "Zone3"), 5)))
        waiter.start()
        time.sleep(0.05)
        self.assertEqual(results, [])
        self.table.release("job-a")
        waiter.join(2)
        self.assertFalse(waiter.is_alive())
        self.assertEqual(results, [True])

    def test_snapshot_is_sorted_and_describes_claims(self):
        self.table.claim("job-b", "dan", ["Lighting"], "l" * 250)
        self.now[0] = 1001.0
        self.table.claim("job-a", "ana", ["@play:studio-1", "Workspace.Map.Zone3"], "zona")
        snapshot = self.table.snapshot()
        self.assertEqual([item["path"] for item in snapshot], ["Lighting", "@play:studio-1", "Workspace.Map.Zone3"])
        self.assertEqual(snapshot[0], {"path": "Lighting", "job_id": "job-b", "developer": "dan", "since": 1000.0,
                                       "reason": "l" * 200, "expires": 1010.0})
        self.assertEqual(snapshot[1]["expires"], 1011.0)
        self.assertEqual(claims.CLAIM_IDLE_SECONDS, 600)
        self.assertEqual(ClaimTable().idle_seconds, 600)


if __name__ == "__main__":
    unittest.main()

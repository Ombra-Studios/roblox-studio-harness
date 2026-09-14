"""Concurență deterministă pentru coada Mod S; numai Events și provideri falși. Fără hub (`hub_url=None`), fără rețea, fără %LOCALAPPDATA%."""

import copy
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import studio_bridge
from studio_bridge import Bridge, BridgeError, Job


class RunRecord:
    def __init__(self, arguments, hold_cleanup=False):
        self.arguments = arguments
        self.allow_return = threading.Event()
        self.cleanup_started = threading.Event()
        self.allow_close = threading.Event()
        self.closed = threading.Event()
        if not hold_cleanup:
            self.allow_close.set()


class ControlledProviders:
    def __init__(self):
        self.condition = threading.Condition()
        self.runs = []
        self.attempted = []
        self.active = 0
        self.maximum_active = 0
        self.closed = False
        self.hold_first_cleanup = False
        self.available = True
        self.status_barrier = None

    def status(self):
        # 0.4: enqueue-ul citește doar `available`; nu există verificare de cont.
        if self.status_barrier is not None:
            self.status_barrier.wait(timeout=3)
        return {name: {"available": self.available} for name in ("claude", "codex")}

    def run(self, **arguments):
        with self.condition:
            self.attempted.append(arguments["job_id"])
            if self.closed:
                raise RuntimeError("Providerul fals este închis.")
            record = RunRecord(arguments, self.hold_first_cleanup and not self.runs)
            self.runs.append(record)
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            self.condition.notify_all()
        try:
            if not record.allow_return.wait(timeout=5):
                raise RuntimeError("Testul nu a eliberat providerul fals.")
            arguments["emit"]("text", "Răspuns simulat pentru " + arguments["job_id"])
            return "native-" + arguments["job_id"]
        finally:
            record.cleanup_started.set()
            record.allow_close.wait(timeout=5)
            with self.condition:
                self.active -= 1
                record.closed.set()
                self.condition.notify_all()

    def wait_runs(self, count):
        with self.condition:
            if not self.condition.wait_for(lambda: len(self.runs) >= count, timeout=3):
                raise AssertionError("Nu a început numărul așteptat de joburi simulate.")
            return self.runs[count - 1]

    def close(self):
        with self.condition:
            self.closed = True
            for record in self.runs:
                record.allow_return.set()
                record.allow_close.set()
            self.condition.notify_all()


class ControlledNative:
    def __init__(self):
        self.verified = []
        self.verify_threads = []
        self.verification_entered = threading.Event()
        self.allow_verification = threading.Event()
        self.allow_verification.set()
        self.closed = False
        self.calls = []

    def verify_studio(self, studio_id):
        self.verified.append(studio_id)
        self.verify_threads.append(threading.get_ident())
        self.verification_entered.set()
        if not self.allow_verification.wait(timeout=5):
            raise RuntimeError("Testul nu a eliberat verificarea falsă.")
        if studio_id == "missing":
            raise BridgeError("Scena nu mai este conectată.", 409)

    def list_tools(self):
        return [{"name": "inspect_instance", "inputSchema": {"type": "object", "properties": {}}, "description": "Test"},
                {"name": "execute_luau", "inputSchema": {"type": "object", "properties": {}}, "description": "Test"}]

    def call(self, name, arguments, studio_id):
        self.calls.append((name, copy.deepcopy(arguments), studio_id))
        return {"content": [], "isError": False}

    def close(self):
        self.closed = True
        self.allow_verification.set()


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        self.providers = ControlledProviders()
        self.native = ControlledNative()
        self.bridge = Bridge(self.providers, Path(self.temp.name) / "runtime", self.native, "queue-ui-token-0123456789",
                             developer="tester", state_directory=Path(self.temp.name) / "state", hub_url=None)

    def tearDown(self):
        self.providers.status_barrier = None
        self.bridge.close()
        self.assertFalse(self.bridge.dispatcher.is_alive())
        self.temp.cleanup()

    def submit(self, text="test", **extra):
        return self.bridge.start_chat({"provider": "claude", "prompt": text, "studio_id": "studio-1", **extra})

    def wait_state(self, job, state):
        with job.condition:
            self.assertTrue(job.condition.wait_for(lambda: job.state == state, timeout=3), (job.id, job.state))

    def wait_idle(self):
        with self.bridge.queue_condition:
            self.assertTrue(self.bridge.queue_condition.wait_for(lambda: self.bridge.active_job_id is None, timeout=3))

    def luau(self, job, code):
        """Apel modificator valid în 0.4: claim pe subarbore și parametrul scope."""
        _, conflicts = self.bridge.claims.claim(job.id, job.developer, ["Workspace.Map.Zone3"])
        self.assertEqual(conflicts, [])
        return self.bridge.agent_call(job, "execute_luau", {"code": code, "scope": ["Workspace.Map.Zone3"]})

    def test_fifo_across_remote_sessions_and_single_inference(self):
        first = self.submit("first")
        first_run = self.providers.wait_runs(1)
        second = self.submit("second", provider="codex")
        third = self.submit("third", studio_id="studio-2")
        self.assertEqual(second.state, "queued")
        self.assertEqual(third.state, "queued")
        self.assertEqual(self.bridge.status()["queued_count"], 2)
        self.assertEqual(self.bridge.status()["active_job_id"], first.id)
        first_run.allow_return.set()
        second_run = self.providers.wait_runs(2)
        self.assertTrue(first_run.closed.is_set())
        self.assertEqual(second_run.arguments["job_id"], second.id)
        second_run.allow_return.set()
        third_run = self.providers.wait_runs(3)
        self.assertTrue(second_run.closed.is_set())
        self.assertEqual(third_run.arguments["job_id"], third.id)
        third_run.allow_return.set()
        self.wait_state(third, "completed")
        self.wait_idle()
        self.assertEqual(self.providers.attempted, [first.id, second.id, third.id])
        self.assertEqual(self.providers.maximum_active, 1)
        self.assertEqual(self.native.verify_threads, [self.bridge.dispatcher.ident] * 3)

    def test_enqueue_does_not_wait_for_native_verification_lock(self):
        self.native.allow_verification.clear()
        first = self.submit("first")
        self.assertTrue(self.native.verification_entered.wait(timeout=2))
        submitted = threading.Event()
        results, errors = [], []

        def enqueue():
            try:
                results.append(self.submit("second"))
            except Exception as error:
                errors.append(error)
            finally:
                submitted.set()

        thread = threading.Thread(target=enqueue)
        thread.start()
        self.assertTrue(submitted.wait(timeout=1))
        thread.join(timeout=1)
        self.assertEqual(errors, [])
        self.assertEqual(results[0].state, "queued")
        self.assertEqual(self.providers.runs, [])
        self.assertEqual(self.native.verified, [first.studio_id])
        self.native.allow_verification.set()

    def test_missing_scene_fails_before_cost_and_dispatcher_continues(self):
        missing = self.submit("missing", studio_id="missing")
        self.wait_state(missing, "failed")
        self.assertEqual(self.providers.attempted, [])
        good = self.submit("good")
        record = self.providers.wait_runs(1)
        self.assertEqual(record.arguments["job_id"], good.id)
        self.assertEqual(self.native.verified, ["missing", "studio-1"])

    def test_one_nonterminal_job_per_session(self):
        first = self.submit()
        self.providers.wait_runs(1)
        with self.assertRaises(BridgeError) as error:
            self.submit("another", session_id=first.session_id)
        self.assertEqual(error.exception.status, 409)
        queued = self.submit("queued")
        with self.assertRaises(BridgeError) as error:
            self.submit("again", session_id=queued.session_id)
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(len(self.bridge.jobs), 2)

    def test_session_provider_and_scene_are_immutable(self):
        first = self.submit()
        self.providers.wait_runs(1)
        for changes in ({"provider": "codex"}, {"studio_id": "other"}):
            with self.subTest(changes=changes), self.assertRaises(BridgeError) as error:
                self.submit("other", session_id=first.session_id, **changes)
            self.assertEqual(error.exception.status, 409)

    def test_cancelled_queued_job_never_starts_provider_or_verification(self):
        first = self.submit("first")
        first_run = self.providers.wait_runs(1)
        second = self.submit("second", studio_id="studio-2")
        third = self.submit("third", studio_id="studio-3")
        self.bridge.cancel_job(second.id)
        self.assertEqual(second.state, "cancelled")
        self.assertEqual(self.bridge.status()["queued_count"], 1)
        first_run.allow_return.set()
        third_run = self.providers.wait_runs(2)
        self.assertEqual(third_run.arguments["job_id"], third.id)
        self.assertEqual(self.providers.attempted, [first.id, third.id])
        self.assertEqual(self.native.verified, ["studio-1", "studio-3"])

    def test_active_cancel_does_not_release_dispatcher_before_process_closes(self):
        self.providers.hold_first_cleanup = True
        first = self.submit("first")
        first_run = self.providers.wait_runs(1)
        self.bridge.cancel_job(first.id)
        self.assertEqual(first.state, "cancelled")
        second = self.submit("second", session_id=first.session_id)
        first_run.allow_return.set()
        self.assertTrue(first_run.cleanup_started.wait(timeout=2))
        self.assertFalse(first_run.closed.is_set())
        self.assertEqual(self.bridge.status()["active_job_id"], first.id)
        self.assertEqual(self.providers.attempted, [first.id])
        self.assertEqual(second.state, "queued")
        first_run.allow_close.set()
        second_run = self.providers.wait_runs(2)
        self.assertTrue(first_run.closed.is_set())
        self.assertEqual(second_run.arguments["native_session_id"], "native-" + first.id)
        self.assertEqual(self.providers.maximum_active, 1)

    def test_remote_session_resumes_exact_native_id(self):
        first = self.submit("first")
        self.providers.wait_runs(1).allow_return.set()
        self.wait_state(first, "completed")
        resumed = self.submit("continue", session_id=first.session_id)
        record = self.providers.wait_runs(2)
        self.assertEqual(record.arguments["native_session_id"], "native-" + first.id)
        self.assertEqual(resumed.session_id, first.session_id)

    def test_queue_limit_32_is_enforced_without_eviction(self):
        first = self.submit("active")
        self.providers.wait_runs(1)
        queued = [self.submit(str(index)) for index in range(31)]
        with self.assertRaises(BridgeError) as error:
            self.submit("overflow")
        self.assertEqual(error.exception.status, 429)
        self.assertEqual(self.bridge.status()["queued_count"], 31)
        self.assertIs(self.bridge.jobs[first.id], first)
        self.assertTrue(all(job.state == "queued" for job in queued))
        self.assertEqual(len(self.bridge.jobs), 32)

    def test_same_request_id_returns_same_job_even_if_availability_changes(self):
        payload = {"provider": "claude", "prompt": "same", "studio_id": "studio-1", "client_request_id": "same-id"}
        job = self.bridge.start_chat(payload)
        self.providers.wait_runs(1)
        self.providers.available = False
        self.assertIs(self.bridge.start_chat(dict(reversed(list(payload.items())))), job)
        self.assertEqual(self.providers.attempted, [job.id])
        self.assertEqual(len(self.bridge.jobs), 1)

    def test_same_request_id_different_payload_is_conflict(self):
        job = self.submit("first", client_request_id="same-id")
        self.providers.wait_runs(1)
        with self.assertRaises(BridgeError) as error:
            self.submit("changed", client_request_id="same-id")
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.providers.attempted, [job.id])

    def test_concurrent_idempotent_submissions_create_one_job(self):
        self.providers.status_barrier = threading.Barrier(2)
        results, errors = [], []

        def enqueue():
            try:
                results.append(self.submit("same", client_request_id="concurrent-id"))
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=enqueue) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.providers.status_barrier = None
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertIs(results[0], results[1])
        self.providers.wait_runs(1)
        self.assertEqual(len(self.bridge.jobs), 1)
        self.assertEqual(self.providers.attempted, [results[0].id])

    def test_pruned_idempotency_receipt_returns_410_without_reexecution(self):
        first = self.submit("active", client_request_id="active-id")
        self.providers.wait_runs(1)
        with patch.object(studio_bridge, "MAX_JOB_HISTORY", 3):
            old = self.submit("old", client_request_id="old-id")
            self.bridge.cancel_job(old.id)
            for index in range(5):
                job = self.submit(str(index), client_request_id="new-" + str(index))
                self.bridge.cancel_job(job.id)
            self.assertEqual(len(self.bridge.jobs), 3)
            self.assertIs(self.bridge.jobs[first.id], first)
            self.assertNotIn(old.id, self.bridge.jobs)
            with self.assertRaises(BridgeError) as error:
                self.submit("old", client_request_id="old-id")
            self.assertEqual(error.exception.status, 410)
            self.assertIn("Nu a fost reexecutată", str(error.exception))
            with self.assertRaises(BridgeError) as changed:
                self.submit("different", client_request_id="old-id")
            self.assertEqual(changed.exception.status, 409)
            self.assertEqual(self.providers.attempted, [first.id])
            self.assertEqual(list(self.bridge.queue), [])

    def test_receipt_limit_rejects_new_requests_but_accepts_known_retries(self):
        with patch.object(studio_bridge, "MAX_REQUEST_RECEIPTS", 3):
            first = self.submit("first", client_request_id="first-id")
            self.providers.wait_runs(1)
            second = self.submit("second", client_request_id="second-id")
            third = self.submit("third", client_request_id="third-id")
            for extras in ({"client_request_id": "fourth-id"}, {}):
                with self.subTest(extras=extras), self.assertRaises(BridgeError) as error:
                    self.submit("new", **extras)
                self.assertEqual(error.exception.status, 429)
                self.assertIn("Repornește", str(error.exception))
            self.assertIs(self.submit("first", client_request_id="first-id"), first)
            self.assertIs(self.submit("second", client_request_id="second-id"), second)
            self.assertIs(self.submit("third", client_request_id="third-id"), third)
            self.assertEqual(len(self.bridge.request_receipts), 3)
            self.assertEqual(self.providers.attempted, [first.id])

    def test_proxy_rejects_queued_and_terminal_jobs_before_native_access(self):
        first = self.submit("first")
        self.providers.wait_runs(1)
        queued = self.submit("queued")
        with self.assertRaises(BridgeError) as error:
            self.bridge.authenticate_job(queued.id, queued.token)
        self.assertEqual(error.exception.status, 409)
        with self.assertRaises(BridgeError):
            self.bridge.agent_tools(queued)
        with self.assertRaises(BridgeError):
            self.bridge.agent_call(queued, "inspect_instance", {"path": "Workspace"})
        self.assertEqual(self.native.calls, [])
        self.assertIs(self.bridge.authenticate_job(first.id, first.token), first)
        self.bridge.cancel_job(queued.id)
        with self.assertRaises(BridgeError):
            self.bridge.authenticate_job(queued.id, queued.token)

    def test_cancel_other_window_does_not_resolve_active_approval(self):
        first = self.submit("first")
        self.providers.wait_runs(1)
        queued = self.submit("queued")
        results = []
        thread = threading.Thread(target=lambda: results.append(self.luau(first, "return 1")))
        thread.start()
        with first.condition:
            self.assertTrue(first.condition.wait_for(lambda: first.pending is not None, timeout=2))
            approval_id = first.pending["id"]
        self.bridge.cancel_job(queued.id)
        self.assertEqual(first.pending["id"], approval_id)
        self.assertEqual(first.state, "waiting_approval")
        with self.assertRaises(BridgeError):
            queued.approve(approval_id, True)
        poll = self.bridge.poll_jobs({"jobs": [{"job_id": first.id}, {"job_id": queued.id}]})
        self.assertTrue(any(event["type"] == "approval" for event in poll["jobs"][0]["events"]))
        self.assertFalse(any(event["type"] == "approval" for event in poll["jobs"][1]["events"]))
        first.approve(approval_id, False)
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(results[0]["isError"])
        self.assertEqual(self.native.calls, [])

    def test_poll_results_are_copies_not_mutable_shared_state(self):
        first = self.submit("first")
        self.providers.wait_runs(1)
        first.emit("approval", "test", approval_id="example", arguments={"nested": {"value": 1}})
        results = self.bridge.poll_jobs({"jobs": [{"job_id": first.id}]})
        approval = next(event for event in results["jobs"][0]["events"] if event["type"] == "approval")
        approval["arguments"]["nested"]["value"] = 9
        original = next(event for event in first.events if event["type"] == "approval")
        self.assertEqual(original["arguments"]["nested"]["value"], 1)

    def test_shutdown_cancels_queue_and_never_starts_queued_providers(self):
        first = self.submit("first")
        first_run = self.providers.wait_runs(1)
        queued = self.submit("queued")
        self.bridge.close()
        self.assertFalse(self.bridge.dispatcher.is_alive())
        self.assertTrue(first_run.closed.is_set())
        self.assertEqual(self.providers.attempted, [first.id])
        self.assertEqual(first.state, "cancelled")
        self.assertEqual(queued.state, "cancelled")
        self.assertTrue(self.providers.closed)
        self.assertTrue(self.native.closed)
        with self.assertRaises(BridgeError) as error:
            self.submit("after close")
        self.assertEqual(error.exception.status, 503)

    def test_shutdown_during_verification_does_not_start_inference(self):
        self.native.allow_verification.clear()
        job = self.submit("first")
        self.assertTrue(self.native.verification_entered.wait(timeout=2))
        self.bridge.close()
        self.assertFalse(self.bridge.dispatcher.is_alive())
        self.assertEqual(job.state, "cancelled")
        self.assertEqual(self.providers.attempted, [])

    def test_pending_native_call_finishes_before_next_inference(self):
        first = self.submit("first")
        first_run = self.providers.wait_runs(1)
        second = self.submit("second")
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []

        def held_call(name, arguments, studio_id):
            entered.set()
            if not release.wait(timeout=3):
                raise RuntimeError("Apelul fals nu a fost eliberat.")
            return {"content": [], "isError": False}

        def call():
            try:
                results.append(self.bridge.agent_call(first, "inspect_instance", {"path": "Workspace"}))
            except Exception as error:
                errors.append(error)

        self.native.call = held_call
        thread = threading.Thread(target=call)
        thread.start()
        self.assertTrue(entered.wait(timeout=2))
        first_run.allow_return.set()
        self.wait_state(first, "completed")
        self.wait_state(second, "running")
        self.assertTrue(first_run.closed.is_set())
        self.assertEqual(self.providers.attempted, [first.id])
        release.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertFalse(results[0]["isError"])
        self.assertEqual(self.providers.wait_runs(2).arguments["job_id"], second.id)

    def test_active_cancel_resolves_only_its_approval_once(self):
        first = self.submit("first")
        self.providers.wait_runs(1)
        queued = self.submit("queued")
        results = []
        thread = threading.Thread(target=lambda: results.append(self.luau(first, "return 1")))
        thread.start()
        with first.condition:
            self.assertTrue(first.condition.wait_for(lambda: first.pending is not None, timeout=2))
            approval_id = first.pending["id"]
        self.bridge.cancel_job(first.id)
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        resolutions = [event for event in first.events if event.get("approval_active") is False]
        self.assertEqual(len(resolutions), 1)
        self.assertEqual(resolutions[0]["approval_id"], approval_id)
        self.assertEqual(queued.state, "queued")
        self.assertFalse(any(event.get("approval_id") == approval_id for event in queued.events))
        self.assertTrue(results[0]["isError"])
        # Oprirea eliberează claims-urile jobului anulat, nu pe ale celorlalte.
        self.assertEqual(self.bridge.claims.held_by(first.id), [])

    def test_changed_payload_is_409_even_if_new_fields_are_invalid(self):
        first = self.submit("original", client_request_id="same-id")
        self.providers.wait_runs(1)
        with self.assertRaises(BridgeError) as error:
            self.submit("", client_request_id="same-id")
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.providers.attempted, [first.id])

    def test_runtime_error_does_not_expose_pairing_or_job_tokens(self):
        def fail(**arguments):
            raise RuntimeError("Provider fals: " + self.bridge.token + " " + arguments["job_token"])
        self.providers.run = fail
        job = self.submit("fail")
        self.wait_state(job, "failed")
        text = " ".join(event.get("text", "") for event in job.events)
        self.assertNotIn(self.bridge.token, text)
        self.assertNotIn(job.token, text)
        self.assertIn("[redactat]", text)

    def test_production_limits_match_contract(self):
        self.assertEqual(studio_bridge.MAX_NONTERMINAL_JOBS, 32)
        self.assertEqual(studio_bridge.MAX_JOB_HISTORY, 64)
        self.assertEqual(studio_bridge.MAX_REQUEST_RECEIPTS, 4096)
        self.assertEqual(studio_bridge.MAX_POLL_JOBS, 16)

    def test_bridge_identity_changes_only_on_new_bridge(self):
        initial = self.bridge.status()["bridge_id"]
        self.assertEqual(self.bridge.status()["bridge_id"], initial)
        another = Bridge(ControlledProviders(), Path(self.temp.name) / "other", ControlledNative(),
                         developer="tester", state_directory=Path(self.temp.name) / "other-state", hub_url=None)
        try:
            # Fără token explicit, codul local și tokenul de dispozitiv sunt create în directorul de stare dat, nu în %LOCALAPPDATA%.
            self.assertTrue((Path(self.temp.name) / "other-state" / "local-token").is_file())
            self.assertTrue((Path(self.temp.name) / "other-state" / "device-token").is_file())
            self.assertNotEqual(another.token, self.bridge.token)
            self.assertNotEqual(another.status()["bridge_id"], initial)
            self.assertNotEqual(another.status()["hub"]["device_id"], self.bridge.status()["hub"]["device_id"])
        finally:
            another.close()

    def test_jobs_carry_the_workspace_known_at_creation_or_the_first_identity(self):
        first = self.submit("first")
        self.providers.wait_runs(1)
        self.assertIsNone(first.workspace)
        self.bridge.set_identity({"user_id": 1, "name": "ana", "place_id": 5, "game_id": 9, "place_name": "Joc"})
        self.assertEqual(first.workspace, "game:9")
        second = self.submit("second", studio_id="studio-2")
        self.assertEqual((second.workspace, second.developer, second.state), ("game:9", "ana", "queued"))
        # Rândurile trimise hub-ului poartă cheia jobului (nu se atinge MCP-ul nativ: ControlledNative nu listează instanțe).
        rows = {row["job_id"]: row for row in self.bridge.hub_payload({})}
        self.assertEqual((rows[first.id]["workspace"], rows[second.id]["workspace"], rows[second.id]["state"]), ("game:9", "game:9", "queued"))


class AutoApproveTests(unittest.TestCase):
    """1.0: setarea de aprobare („ask”, „edits”, „all”) din plugin, păstrată în config.json.

    Clasă de sine stătătoare: moștenirea din `QueueTests` ar fi rulat a doua oară toată suita cozii."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        self.providers = ControlledProviders()
        self.native = ControlledNative()
        self.bridge = Bridge(self.providers, Path(self.temp.name) / "runtime", self.native, "approve-ui-token-0123456789",
                             developer="tester", state_directory=Path(self.temp.name) / "state", hub_url=None)

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def submit(self, text="test", **extra):
        return self.bridge.start_chat({"provider": "claude", "prompt": text, "studio_id": "studio-1", **extra})

    def luau(self, job, code):
        _, conflicts = self.bridge.claims.claim(job.id, job.developer, ["Workspace.Map.Zone3"])
        self.assertEqual(conflicts, [])
        return self.bridge.agent_call(job, "execute_luau", {"code": code, "scope": ["Workspace.Map.Zone3"]})

    def generate(self, job):
        """Un tool de generare: consumă credite Roblox, deci rămâne în afara lui „edits”."""
        tools = self.native.list_tools() + [{"name": "generate_material", "inputSchema": {"type": "object", "properties": {}},
                                             "description": "Generare"}]
        self.native.list_tools = lambda: tools
        return self.bridge.agent_call(job, "generate_material", {"prompt": "piatră", "path": "Workspace.Map.Zone3"})

    def test_default_asks_every_time(self):
        self.assertEqual(self.bridge.auto_approve, "ask")
        self.assertFalse(self.bridge.approves_itself("execute_luau"))
        self.assertFalse(self.bridge.approves_itself("generate_material"))
        self.assertEqual(self.bridge.status()["settings"], {"auto_approve": "ask"})

    def test_edits_pass_alone_but_generation_still_asks(self):
        self.assertEqual(self.bridge.set_auto_approve("edits"), "edits")
        self.assertTrue(self.bridge.approves_itself("execute_luau"))
        self.assertFalse(self.bridge.approves_itself("generate_material"), "generarea consumă credite: rămâne la om")
        job = self.submit("editare")
        self.providers.wait_runs(1)
        result = self.luau(job, "return 1")
        self.assertFalse(result["isError"], result)
        self.assertIsNone(job.pending, "nimeni nu a fost întrebat")
        self.assertEqual([call[0] for call in self.native.calls], ["execute_luau"])
        # Aprobarea automată se vede în fluxul sesiunii: nu se execută nimic pe tăcute.
        self.assertTrue(any(event["type"] == "status" and "Aprobat automat" in event["text"] for event in job.events))

    def test_all_covers_generation_too(self):
        self.bridge.set_auto_approve("all")
        self.assertTrue(self.bridge.approves_itself("generate_material"))
        job = self.submit("generare")
        self.providers.wait_runs(1)
        _, conflicts = self.bridge.claims.claim(job.id, job.developer, ["Workspace.Map.Zone3"])
        self.assertEqual(conflicts, [])
        self.assertFalse(self.generate(job)["isError"])
        self.assertIsNone(job.pending)

    def test_the_setting_is_persisted_and_validated(self):
        state = Path(self.temp.name) / "state"
        self.bridge.set_auto_approve("all")
        self.assertEqual(json.loads((state / "config.json").read_text(encoding="utf-8"))["auto_approve"], "all")
        # Revenirea la implicit scoate cheia din fișier, ca să nu rămână o setare moartă acolo.
        self.bridge.set_auto_approve("ask")
        self.assertNotIn("auto_approve", json.loads((state / "config.json").read_text(encoding="utf-8")))
        for bad in ("da", "", None, 1, True, ["all"]):
            with self.subTest(bad=bad), self.assertRaisesRegex(BridgeError, "auto_approve"):
                self.bridge.set_auto_approve(bad)
        self.assertEqual(self.bridge.auto_approve, "ask")

    def test_a_restarted_daemon_keeps_the_setting(self):
        self.bridge.set_auto_approve("edits")
        another = Bridge(ControlledProviders(), Path(self.temp.name) / "runtime2", ControlledNative(), "alt-token-0123456789",
                         developer="tester", state_directory=Path(self.temp.name) / "state", hub_url=None)
        try:
            self.assertEqual(another.auto_approve, "edits")
            self.assertEqual(another.status()["settings"], {"auto_approve": "edits"})
        finally:
            another.close()


if __name__ == "__main__":
    unittest.main()

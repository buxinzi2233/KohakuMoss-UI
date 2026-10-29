"""P1 concurrency/process regressions; no models or real training."""

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_gbc_drafts import DEFAULTS

from kohakumoss_ui.contract import gbc_storage_files, launch, state
from kohakumoss_ui.contract.gbc import GbcService
from kohakumoss_ui.contract.gbc_lease import Lease
from kohakumoss_ui.contract.gbc_process import group_alive
from kohakumoss_ui.contract.gbc_schema import GbcError
from kohakumoss_ui.contract.root import TrainerRoot
from kohakumoss_ui.contract.training_drafts import DraftError, resolve_for_launch


class ObservedRLock:
    """Real RLock with a deterministic nonblocking-attempt observation."""
    def __init__(self):
        self.inner = threading.RLock()
        self.attempted = threading.Event()
        self.reader_acquired_immediately = None

    def __enter__(self):
        if threading.current_thread().name == "p1-reader":
            acquired = self.inner.acquire(blocking=False)
            self.reader_acquired_immediately = acquired
            self.attempted.set()
            if not acquired:
                self.inner.acquire()
        else:
            self.inner.acquire()
        return self

    def __exit__(self, *_):
        self.inner.release()


class DraftLockTests(unittest.TestCase):
    def scenario(self, operation, fail_launch=False, registered=True):
        with tempfile.TemporaryDirectory(prefix="gbc-p1-lock-") as tmp:
            base = Path(tmp)
            root = base / "trainer"
            (root / "configs/moss").mkdir(parents=True)
            (root / "configs/moss/base.py").write_text('"""scripts/train/moss.py"""\nSTEPS=1\n')
            dataset = base / "dataset"
            shard = dataset / "shards/shard_00000"
            shard.mkdir(parents=True)
            (dataset / "dataset.json").write_text(json.dumps({"format": "kohakugbc-dataset", "version": 1,
                                                            "shards": [{"name": "shard_00000"}]}))
            for name in ("manifest.parquet", "images.zip", "graphs.db", "meta.db"):
                (shard / name).write_bytes(b"HASH/CHECKER FIXTURE ONLY; not native data")
            service = GbcService(TrainerRoot(root), state=base / "state")
            coordination = ObservedRLock()
            service.lock = coordination
            service._load()
            manifest, identities = gbc_storage_files.snapshot(dataset)
            service._register_dataset("dataset_fixture", dataset, manifest,
                {"complete": True, "validatedSamples": 1, "shards": [{"name": "shard_00000"}]}, identities)

            def checker_fixture(_root, _python, payload, **_kwargs):
                if payload["op"] == "load":
                    return {"values": copy.deepcopy(DEFAULTS), "defaults": copy.deepcopy(DEFAULTS),
                            "runtime": {"fixture": True}}
                self.assertEqual(payload["op"], "check")
                return {"ok": True, "exitCode": 0, "output": "CHECKER FIXTURE ONLY",
                        "effectiveHash": "sha256:" + hashlib.sha256(Path(payload["path"]).read_bytes()).hexdigest(),
                        "runtime": {"fixture": True}}

            threads = []
            release_launch = threading.Event()
            try:
                with patch("kohakumoss_ui.contract.training_drafts.worker", side_effect=checker_fixture):
                    _, created = service.post("training-drafts", {"apiVersion": "gbc-ui-v1", "requestId": "create",
                        "datasetId": "dataset_fixture", "datasetRevision": 1, "shards": ["shard_00000"],
                        "baseConfigId": "configs/moss/base.py", "captionOptions": {"template": "gbc",
                        "formats": [{"weight": 1, "sections": ["graph"]}]}, "confirmExecution": True})
                    ref = created["data"]["draft"]["draftRef"]
                    service.post("training-drafts/check", {"apiVersion": "gbc-ui-v1", "requestId": "check",
                                                          "draftRef": ref, "confirmExecution": True})
                    draft = service.drafts
                    self.assertIs(draft._coordination_lock, service.lock)
                    original_sources = draft._sources
                    inside = threading.Event()
                    errors = []
                    results = []

                    def paused_sources():
                        if threading.current_thread().name == "p1-launch":
                            inside.set()
                            if not release_launch.wait(3):
                                raise RuntimeError("test schedule deadline")
                            if fail_launch:
                                raise DraftError("SOURCE_CHANGED", "Injected freshness failure")
                        return original_sources()

                    def run_launch():
                        try:
                            result = resolve_for_launch(service.root, ref) if registered else draft.resolve_launch(ref)
                            results.append(("launch", result))
                        except DraftError as exc:
                            errors.append(exc.code)

                    def run_reader():
                        try:
                            if operation == "get":
                                results.append(service.get("training-drafts", {"draftId": ref["draftId"]}))
                            else:
                                results.append(service.post("training-drafts/check", {"apiVersion": "gbc-ui-v1",
                                    "requestId": "concurrent-check", "draftRef": ref, "confirmExecution": True}))
                        except (GbcError, OSError, ValueError, KeyError) as exc:
                            errors.append(str(exc))

                    with patch.object(draft, "_sources", side_effect=paused_sources):
                        a = threading.Thread(target=run_launch, name="p1-launch", daemon=True)
                        b = threading.Thread(target=run_reader, name="p1-reader", daemon=True)
                        threads = [a, b]
                        a.start()
                        self.assertTrue(inside.wait(3))
                        b.start()
                        self.assertTrue(coordination.attempted.wait(3))
                        # Old AB/BA order deterministically acquired this lock here.
                        self.assertFalse(coordination.reader_acquired_immediately)
                        release_launch.set()
                        for thread in threads:
                            thread.join(3)
                        self.assertFalse(any(thread.is_alive() for thread in threads))
                    self.assertEqual(errors, ["SOURCE_CHANGED"] if fail_launch else [])
                    self.assertEqual(len(results), 1 if fail_launch else 2)
                    self.assertEqual(service.get("settings")[0], 200)
                    service.db["jobs"]["job_terminal"] = {"jobId": "job_terminal", "status": "done", "revision": 1}
                    self.assertEqual(service.post("jobs/stop", {"apiVersion": "gbc-ui-v1", "requestId": "stop",
                        "jobId": "job_terminal", "expectedRevision": 1, "force": False})[0], 200)
            finally:
                release_launch.set()
                for thread in threads:
                    thread.join(3)
                # A regression must fail boundedly, not hang unittest cleanup.
                if not any(thread.is_alive() for thread in threads):
                    service.close()

    def test_registered_launch_and_http_get_have_one_lock_order(self):
        self.scenario("get")

    def test_direct_launch_failure_and_http_check_release_both_locks(self):
        self.scenario("check", fail_launch=True, registered=False)


@unittest.skipUnless(sys.platform == "linux", "Linux identity/group guarantees")
class TrainingProcessSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gbc-p1-process-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"KOHAKUMOSS_UI_STATE": str(self.base / "state")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.root = TrainerRoot(self.base / "trainer")
        (self.root.root / "configs").mkdir(parents=True)
        (self.root.root / "configs/test.py").write_text("RUN_NAME='fixture'\n")

    def test_bind_failure_reaps_own_real_sleep_before_releasing_lease(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        try:
            with (patch.object(launch, "_start_unlocked", return_value=({"fixture": True}, process)),
                  patch.object(Lease, "bind", side_effect=OSError("injected bind/fsync failure")),
                  self.assertRaises(OSError)):
                launch.start(self.root, "configs/test.py")
            self.assertIsNotNone(process.poll())
            self.assertFalse(group_alive(process.pid))
            second = Lease("after-confirmed-exit")
            second.release()
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)

    def test_bind_failure_with_kill_failure_retains_recovery_fence(self):
        process = Mock(pid=424242)
        process.poll.return_value = None
        identity = {"boot": "boot", "start": "start", "group": process.pid}
        with (patch.object(launch, "_start_unlocked", return_value=({}, process)),
              patch.object(state, "process_identity", return_value=identity),
              patch.object(Lease, "bind", side_effect=OSError("bind failure")),
              patch.object(launch.os, "killpg", side_effect=PermissionError("cleanup refused")) as signal,
              self.assertRaises(OSError)):
            launch.start(self.root, "configs/test.py")
        signal.assert_called_once_with(process.pid, launch.signal.SIGKILL)
        process.wait.assert_not_called()
        self.assertIsNone(process.poll())
        with self.assertRaises(GbcError) as blocked:
            Lease("must-not-start")
        self.assertEqual(blocked.exception.code, "RECOVERY_REQUIRED")

    def test_bind_failure_with_wait_timeout_retains_recovery_fence(self):
        process = Mock(pid=424242)
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("fixture", 5)
        identity = {"boot": "boot", "start": "start", "group": process.pid}
        with (patch.object(launch, "_start_unlocked", return_value=({}, process)),
              patch.object(state, "process_identity", return_value=identity),
              patch.object(Lease, "bind", side_effect=OSError("bind failure")),
              patch.object(launch.os, "killpg"), self.assertRaises(OSError)):
            launch.start(self.root, "configs/test.py")
        process.wait.assert_called_once_with(timeout=5)
        with self.assertRaises(GbcError) as blocked:
            Lease("must-not-start")
        self.assertEqual(blocked.exception.code, "RECOVERY_REQUIRED")

    def test_unreturned_spawn_cleanup_failure_retains_fence(self):
        with (patch.object(launch, "_start_unlocked", side_effect=launch._UnconfirmedSpawn("fixture cleanup failed")),
              self.assertRaises(launch._UnconfirmedSpawn)):
            launch.start(self.root, "configs/test.py")
        with self.assertRaises(GbcError) as blocked:
            Lease("must-not-start")
        self.assertEqual(blocked.exception.code, "RECOVERY_REQUIRED")

    def test_pre_spawn_failure_releases_unused_lease(self):
        with (patch.object(launch, "_start_unlocked", side_effect=FileNotFoundError("not spawned")),
              self.assertRaises(FileNotFoundError)):
            launch.start(self.root, "configs/test.py")
        second = Lease("allowed-after-no-spawn")
        second.release()

    def test_stop_refuses_missing_start_boot_or_group_identity(self):
        saved = {"boot": "old-boot", "start": "123", "group": 424242}
        cases = [None, {**saved, "start": "124"}, {**saved, "boot": "new-boot"}, {**saved, "group": 313131}]
        for current in cases:
            with self.subTest(current=current):
                item = {"pid": 424242, "alive": True, "identity": saved if current else None}
                with (patch.object(state, "status", return_value=item),
                      patch.object(state, "process_identity", return_value=current),
                      patch.object(launch.os, "killpg") as group_signal,
                      patch.object(launch.os, "kill") as pid_signal,
                      patch.object(launch.os, "getpgid") as current_group):
                    result = launch.stop("stale", force=True)
                self.assertFalse(result["stopped"])
                self.assertEqual(result["code"], "RECOVERY_REQUIRED")
                group_signal.assert_not_called()
                pid_signal.assert_not_called()
                current_group.assert_not_called()

    def test_status_does_not_call_reused_pid_a_live_managed_run(self):
        saved = {"boot": "boot", "start": "123", "group": 424242}
        with (patch.object(state, "entry", return_value={"pid": 424242, "identity": saved}),
              patch.object(state, "pid_alive", return_value=True),
              patch.object(state, "process_identity", return_value={**saved, "start": "456"})):
            result = state.status("reused")
        self.assertFalse(result["alive"])
        self.assertTrue(result["recoveryRequired"])
        self.assertFalse(result["identityVerified"])

    def test_register_persists_identity_and_own_sleep_is_stoppable(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        try:
            state.register("own-fixture", process.pid, "configs/test.py", ["SLEEP FIXTURE"], "fixture.log")
            recorded = state.entry("own-fixture")["identity"]
            self.assertEqual(set(recorded), {"boot", "start", "group"})
            self.assertEqual(recorded["group"], process.pid)
            self.assertTrue(state.status("own-fixture")["identityVerified"])
            result = launch.stop("own-fixture")
            self.assertTrue(result["stopped"])
            process.wait(timeout=3)
            self.assertFalse(group_alive(process.pid))
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()

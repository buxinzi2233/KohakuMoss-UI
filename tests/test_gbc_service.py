"""Service tests use a named fake CLI and frozen dummy images, not generation."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_gbc_process import fake_cli

from kohakumoss_ui.contract import launch
from kohakumoss_ui.contract.gbc import GbcService
from kohakumoss_ui.contract.gbc_paths import read_file
from kohakumoss_ui.contract.gbc_process import ManagedProcess
from kohakumoss_ui.contract.gbc_schema import GbcError
from kohakumoss_ui.contract.root import TrainerRoot


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = TrainerRoot(self.base / "trainer")
        self.root.root.mkdir()
        (self.root.root / "configs").mkdir()
        (self.root.root / "configs" / "x.py").write_text("RUN_NAME='test'\n")
        self.env = patch.dict(os.environ, {"KOHAKUMOSS_UI_STATE": str(self.base / "state")})
        self.env.start()
        self.service = GbcService(self.root)
        self.count = 0
        self.source = self.base / "input"
        self.source.mkdir()
        self.output = self.base / "output"
        self.output.mkdir()
        trace = self.source / "trace"
        trace.mkdir()
        (trace / "image.png").write_bytes(b"dummy fixture image")
        (trace / "meta.json").write_text(json.dumps({"arm": "text", "image_root": "."}))
        (trace / "jobs.jsonl").write_text(json.dumps({"key": "test", "image": "image.png", "inputs": {}}) + "\n")
        for name in ("vlm", "detect", "embed", "same"):
            (trace / (name + ".jsonl")).write_text("")
        result = self.post("settings", expectedRevision=0,
            roots=[{"path": str(self.source), "purpose": "input", "confirmTrust": True},
                   {"path": str(self.output), "purpose": "output", "confirmTrust": True}],
            runtimes=[{"python": str(Path(sys.executable).resolve()), "role": "generator", "confirmExecute": True}])
        self.settings = result[1]["data"]["settings"]

    def tearDown(self):
        self.service.close()
        self.env.stop()
        self.tmp.cleanup()

    def post(self, route, **args):
        self.count += 1
        return self.service.post(route, {"apiVersion": "gbc-ui-v1", "requestId": "req_" + str(self.count), **args})

    def task(self):
        result = self.post("tasks", settingsRevision=1, inputRootId=self.settings["roots"][0]["rootId"],
                           relativePath="trace", outputRootId=self.settings["roots"][1]["rootId"],
                           runtimeId=self.settings["runtimes"][0]["runtimeId"], mode="replay")
        self.assertEqual(result[0], 200, result)
        return result[1]["data"]["task"]

    def finish(self, job_id):
        proc = self.service.processes[job_id]
        proc.thread.join(5)
        self.assertFalse(proc.thread.is_alive())
        return self.service.get("jobs", {"jobId": job_id})[1]["data"]["job"]

    @staticmethod
    def fake_process(*args, **kwargs):
        cwd = Path(args[3])
        fake_cli(cwd, "import sys\nprint('FAKE CLI ONLY',flush=True)\nsys.exit(0)\n")
        return ManagedProcess(*args, **kwargs)

    def test_check_start_exit_zero_missing_is_partial(self):
        task = self.task()
        with patch("kohakumoss_ui.contract.gbc.ManagedProcess", side_effect=self.fake_process):
            result = self.post("tasks/check", taskId=task["taskId"], expectedRevision=1, confirmExecution=True)
            self.assertEqual(result[0], 200, result)
            checked = self.finish(result[1]["data"]["job"]["jobId"])
            self.assertEqual(checked["status"], "done")
            task = self.service.get("tasks", {"taskId": task["taskId"]})[1]["data"]["task"]
            result = self.post("jobs/start", taskId=task["taskId"], expectedRevision=task["revision"],
                               manifestHash=task["inputManifestHash"], validationId=task["validationId"], confirmRun=True)
            self.assertEqual(result[0], 200, result)
            ended = self.finish(result[1]["data"]["job"]["jobId"])
            self.assertEqual(ended["status"], "partial")
            self.assertEqual(ended["exitCode"], 0)
            self.assertIsNone(ended["resultArtifactId"])
            self.assertNotIn("_output", ended)

    def test_frozen_input_and_injection(self):
        task = self.task()
        private = self.service.db["tasks"][task["taskId"]]
        self.assertNotIn("_staging", task)
        # Mutating original input cannot alter the owned frozen task.
        (self.source / "trace" / "image.png").write_bytes(b"changed source")
        self.service._verify_task(private)
        frozen = Path(private["_records"][0]["image"])
        frozen.write_bytes(b"tampered")
        result = self.post("tasks/check", taskId=task["taskId"], expectedRevision=1, confirmExecution=True)
        self.assertEqual(result[1]["error"]["code"], "SOURCE_CHANGED")
        result = self.post("tasks", settingsRevision=1, inputRootId=self.settings["roots"][0]["rootId"],
                           relativePath="../trainer", outputRootId=self.settings["roots"][1]["rootId"],
                           runtimeId=self.settings["runtimes"][0]["runtimeId"], mode="replay")
        self.assertEqual(result[1]["error"]["code"], "UNSAFE_PATH")

    def test_hardlinks_and_special_files(self):
        path = self.base / "file"
        path.write_bytes(b"x")
        os.link(path, self.base / "hard")
        with self.assertRaises(GbcError):
            read_file(path)
        os.mkfifo(self.base / "fifo")
        with self.assertRaises(GbcError):
            read_file(self.base / "fifo")

    def test_restart_marks_active_and_does_not_signal_pid(self):
        self.service._load()
        self.service.db["jobs"]["job_stale"] = {"jobId": "job_stale", "revision": 1,
            "status": "running", "kind": "check", "_pid": os.getpid(), "_identity": {"start": "wrong"}}
        self.service._save()
        self.service.close()
        self.service = GbcService(self.root)
        with patch("os.killpg") as signal:
            result = self.service.get("jobs", {"jobId": "job_stale"})
            self.assertEqual(result[1]["data"]["job"]["status"], "recovery_required")
            signal.assert_not_called()

    def test_generation_blocks_manual_and_queue_shared_start(self):
        task = self.task()
        def slow(*args, **kwargs):
            fake_cli(Path(args[3]), "import time\nprint('fake',flush=True)\ntime.sleep(60)\n")
            return ManagedProcess(*args, **kwargs)
        with patch("kohakumoss_ui.contract.gbc.ManagedProcess", side_effect=slow):
            result = self.post("tasks/check", taskId=task["taskId"], expectedRevision=1, confirmExecution=True)
        self.assertEqual(result[0], 200, result)
        with self.assertRaises(GbcError) as conflict:
            launch.start(self.root, "configs/x.py", {"RUN_NAME": "no-spawn"})
        self.assertEqual(conflict.exception.code, "BUSY")

    def test_native_export_import_and_preview_bridge(self):
        # Real CPU data worker, native writer/reader, no model or training run.
        from test_gbc_data import PYTHON, graph, png
        if not PYTHON.is_file():
            self.skipTest("existing data runtime unavailable")
        result = self.post("settings", expectedRevision=1,
                           runtimes=[{"python": str(PYTHON), "role": "data", "confirmExecute": True}])
        self.assertEqual(result[0], 200, result)
        runtime_id = result[1]["data"]["settings"]["runtimes"][-1]["runtimeId"]
        images = self.service.store.workdir("native_images")
        image = images / "sample.png"
        image.write_bytes(png())
        graphs = images / "graphs.jsonl"
        graphs.write_text(json.dumps(graph(image)) + "\n")
        self.service.db["artifacts"]["artifact_fixture"] = {
            "artifactId": "artifact_fixture", "revision": 1, "_graphs": str(graphs),
            "_imageRoot": str(images), "_records": [{"key": "fixture", "image": str(image),
            "meta": {"sourceKey": "fixture", "general": ["blue"]}}]}
        result = self.post("exports", artifactId="artifact_fixture",
                           outputRootId=self.settings["roots"][1]["rootId"],
                           dataRuntimeId=runtime_id, confirm=True)
        self.assertEqual(result[0], 200, result)
        exported = result[1]["data"]["dataset"]
        self.assertEqual(exported["validatedSamples"], 1)
        self.assertNotIn("_path", exported)
        preview = self.service.get("graphs", {"artifactId": "artifact_fixture"})[1]["data"]
        self.assertEqual(preview["items"][0]["sampleId"], "fixture")
        self.assertNotIn("img_path", preview["items"][0])
        resolved = self.service.resolve_dataset(exported["datasetId"])
        registered = self.post("settings", expectedRevision=2, roots=[{
            "path": resolved["path"], "purpose": "input", "confirmTrust": True}])
        source_id = registered[1]["data"]["settings"]["roots"][-1]["rootId"]
        result = self.post("imports", sourceRootId=source_id, relativePath=".",
                           outputRootId=self.settings["roots"][1]["rootId"],
                           dataRuntimeId=runtime_id, confirm=True)
        self.assertEqual(result[0], 200, result)
        imported = result[1]["data"]["dataset"]
        self.assertEqual(imported["manifestHash"], exported["manifestHash"])
        page = self.service.get("datasets/page", {"datasetId": imported["datasetId"],
                                "dataRuntimeId": runtime_id})[1]["data"]
        self.assertEqual(page["items"][0]["meta"]["general"], ["blue"])
        self.assertEqual(page["items"][0]["graph"]["orientation"], "stored-image")

    def test_output_overlap_and_new_directory(self):
        registered = self.post("settings", expectedRevision=1, roots=[{
            "path": str(self.source), "purpose": "output", "confirmTrust": True}])
        root_id = registered[1]["data"]["settings"]["roots"][-1]["rootId"]
        with self.assertRaises(GbcError) as overlap:
            self.service._output(root_id, "job_example")
        self.assertEqual(overlap.exception.code, "UNSAFE_PATH")
        existing = self.output / "job_existing"
        existing.mkdir()
        with self.assertRaises(GbcError) as reused:
            self.service._output(self.settings["roots"][1]["rootId"], "job_existing")
        self.assertEqual(reused.exception.code, "SOURCE_CHANGED")

    def test_corrupt_state_does_not_reset_registry(self):
        self.service.store.write({})
        self.service.close()
        self.service = GbcService(self.root)
        for _ in range(2):
            with self.assertRaises(GbcError) as error:
                self.service.get("settings")
            self.assertEqual(error.exception.code, "RECOVERY_REQUIRED")
        self.assertEqual(self.service.store.read(), {})

    def test_request_receipt_survives_restart(self):
        body = {"apiVersion": "gbc-ui-v1", "requestId": "durable", "expectedRevision": 1}
        first = self.service.post("settings", body)
        self.service.close()
        self.service = GbcService(self.root)
        self.assertEqual(first, self.service.post("settings", body))

    def test_observed_output_is_not_validation(self):
        from kohakumoss_ui.contract.gbc_observe import observe
        path = self.base / "observe"
        path.mkdir()
        record = {"key": "sample", "image": "/frozen/image.png", "meta": {"sourceKey": "original"}}
        good = json.dumps({"img_path": record["image"], "vertices": []}) + "\n"
        (path / "graphs.jsonl").write_text(good + good + '{"partial":')
        counts = observe(path, [record], terminal=True)
        self.assertEqual(counts["ok"], 1)
        self.assertEqual(counts["duplicateRecords"], 1)
        self.assertEqual(counts["invalidRecords"], 1)
        self.assertIsNone(counts["validated"])

    def test_start_without_check_is_rejected(self):
        task = self.task()
        result = self.post("jobs/start", taskId=task["taskId"], expectedRevision=1,
                           manifestHash=task["inputManifestHash"], validationId=None, confirmRun=True)
        self.assertEqual(result[1]["error"]["code"], "VERSION_CONFLICT")

    def test_old_path_guard_not_widened(self):
        outside = self.base / "outside.py"
        outside.write_text("raise RuntimeError('must never execute')")
        with self.assertRaises(ValueError):
            launch.start(self.root, str(outside))


if __name__ == "__main__":
    unittest.main()

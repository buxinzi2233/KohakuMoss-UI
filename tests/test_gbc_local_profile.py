"""Local profile construction/guards only; dummy weights NEVER loaded by a model."""

import ast
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kohakumoss_ui.contract.gbc import GbcService
from kohakumoss_ui.contract.gbc_profiles import local_config, model_snapshot
from kohakumoss_ui.contract.gbc_schema import GbcError
from kohakumoss_ui.contract.root import TrainerRoot


class LocalProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="local-profile-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"KOHAKUMOSS_UI_STATE": str(self.base / "state")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.source = self.base / "input"
        self.output = self.base / "output"
        self.models = self.base / "models"
        for path in (self.source, self.output, self.models):
            path.mkdir()
        (self.source / "image.png").write_bytes(b"fixture image; never decoded by model")
        for slot, model_type in {"vlm": "qwen3_5", "detect": "owlv2", "embed": "gemma3_text"}.items():
            path = self.models / slot
            path.mkdir()
            (path / "config.json").write_text(json.dumps({"model_type": model_type}))
            (path / "model.safetensors").write_bytes(b"construction fixture only; not valid weights")
        (self.models / "embed" / "modules.json").write_text(json.dumps([
            {"idx": 0, "name": "0", "path": "", "type": "sentence_transformers.models.Transformer"}]))
        self.service = GbcService(TrainerRoot(self.base / "trainer"))
        self.addCleanup(self.service.close)
        self.counter = 0
        response = self.post("settings", expectedRevision=0, roots=[
            {"path": str(path), "purpose": purpose, "confirmTrust": True}
            for path, purpose in ((self.source, "input"), (self.output, "output"), (self.models, "model"))],
            runtimes=[{"python": str(Path(sys.executable).resolve()), "role": "generator", "confirmExecute": True}])
        settings = response[1]["data"]["settings"]
        self.args = {"settingsRevision": 1, "inputRootId": settings["roots"][0]["rootId"],
                     "relativePath": ".", "outputRootId": settings["roots"][1]["rootId"],
                     "runtimeId": settings["runtimes"][0]["runtimeId"], "mode": "qwen35-local-v1",
                     "models": {slot: {"rootId": settings["roots"][2]["rootId"], "relativePath": slot}
                                for slot in ("vlm", "detect", "embed")}}

    def post(self, route, **payload):
        self.counter += 1
        return self.service.post(route, {"apiVersion": "gbc-ui-v1", "requestId": "local-" + str(self.counter), **payload})

    def create(self, **changes):
        status, response = self.post("tasks", **{**self.args, **changes})
        self.assertEqual(status, 200, response)
        return response["data"]["task"]

    def test_folder_freeze_config_literals_and_models_pinned(self):
        task = self.create()
        private = self.service.db["tasks"][task["taskId"]]
        values = local_config(private, self.output / "fresh")
        self.assertEqual(values["engines"]["vlm"]["entry"], "qwen35")
        self.assertEqual(values["engines"]["detect"]["entry"], "torch")
        self.assertEqual(values["engines"]["embed"]["devices"], ["cpu"])
        self.assertEqual(values["source"]["kind"], "jsonl")
        for value in values.values():
            self.assertEqual(ast.literal_eval(ascii(value)), value)
        (self.source / "image.png").write_bytes(b"changed source")
        self.service._verify_task(private)
        (self.models / "vlm" / "model.safetensors").write_bytes(b"changed weights")
        with self.assertRaises(GbcError) as changed:
            self.service._verify_task(private)
        self.assertEqual(changed.exception.code, "SOURCE_CHANGED")
        self.assertFalse(task["inferenceVerified"])

    def test_jsonl_frozen_mapping_and_escape_rejected(self):
        path = self.source / "rows.jsonl"
        path.write_text(json.dumps({"image": "image.png", "key": "sample ' \\ test",
                                   "captions": {"detail": "literal __import__('os') text"},
                                   "tags": {"general": {"blue": 0.9}}}) + "\n")
        task = self.create(sourceKind="jsonl", relativePath="rows.jsonl")
        frozen = self.service.db["tasks"][task["taskId"]]
        row = json.loads(Path(frozen["_source"]).read_text())
        self.assertEqual(row["image"], frozen["_records"][0]["image"])
        self.assertTrue(Path(row["image"]).is_file())
        path.write_text(json.dumps({"image": "../outside.png"}) + "\n")
        status, body = self.post("tasks", **{**self.args, "sourceKind": "jsonl", "relativePath": "rows.jsonl"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "UNSAFE_PATH")

    def test_custom_factories_pickle_links_and_redirects_refused(self):
        cases = [({"model_type": "gemma3_text", "auto_map": {"AutoModel": "evil.run"}}, "config.json"),
                 ([{"idx": 0, "name": "0", "path": "", "type": "os.system"}], "modules.json"),
                 ({"activation_function": "os.system"}, "dense-config.json"),
                 ({"tokenizer_name_or_path": "/etc"}, "redirect.json")]
        for value, filename in cases:
            with self.subTest(filename=filename):
                target = self.models / "embed" / filename
                original = target.read_bytes() if target.exists() else None
                target.write_text(json.dumps(value))
                with self.assertRaises(GbcError):
                    model_snapshot(self.models / "embed", "embed")
                if original is None:
                    target.unlink()
                else:
                    target.write_bytes(original)
        pickle = self.models / "detect" / "pytorch_model.bin"
        pickle.write_bytes(b"pickle blocked")
        with self.assertRaises(GbcError):
            model_snapshot(self.models / "detect", "detect")
        pickle.unlink()
        linked = self.models / "detect" / "linked.json"
        linked.symlink_to(self.models / "vlm" / "config.json")
        with self.assertRaises(GbcError):
            model_snapshot(self.models / "detect", "detect")

    def test_missing_weights_and_unknown_profile(self):
        (self.models / "vlm" / "model.safetensors").unlink()
        status, body = self.post("tasks", **self.args)
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "DEPENDENCY_MISSING")
        status, body = self.post("tasks", **{**self.args, "mode": "arbitrary.engine.factory"})
        self.assertEqual(body["error"]["code"], "UNSUPPORTED_CAPABILITY")

    def test_real_missing_generator_dependency_no_model_load(self):
        task = self.create()
        result = self.post("tasks/check", taskId=task["taskId"], expectedRevision=1, confirmExecution=True)
        self.assertEqual(result[0], 200, result)
        job_id = result[1]["data"]["job"]["jobId"]
        self.service.processes[job_id].thread.join(10)
        job = self.service.get("jobs", {"jobId": job_id})[1]["data"]["job"]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["code"], "DEPENDENCY_MISSING")
        self.assertIsNone(self.service.db["tasks"][task["taskId"]]["validationId"])
        self.assertFalse(self.service.db["tasks"][task["taskId"]]["runtimeAvailable"])
        self.assertNotIn("torch", sys.modules)


if __name__ == "__main__":
    unittest.main()

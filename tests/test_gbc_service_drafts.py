"""Real HTTP/data/draft bridge. Successful checker alone is a named test fixture."""

import hashlib
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from test_gbc_data import PYTHON, graph, png

from kohakumoss_ui.contract import launch
from kohakumoss_ui.contract.root import TrainerRoot
from kohakumoss_ui.server.http import make_server


class DraftHttpBridgeTests(unittest.TestCase):
    def test_pack_draft_real_failure_preview_and_same_hash_fixture(self):
        trainer = Path(__file__).resolve().parents[2] / "KohakuMossTrainer"
        if not PYTHON.is_file():
            self.skipTest("installed trainer/data environment required")
        with tempfile.TemporaryDirectory(prefix="gbc-draft-http-") as tmp, patch.dict(
                os.environ, {"KOHAKUMOSS_UI_STATE": str(Path(tmp) / "state")}):
            base = Path(tmp)
            output = base / "output"
            output.mkdir()
            server = make_server(TrainerRoot(trainer), "127.0.0.1", 0, "draft-test")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            index = 0

            def call(route, payload=None):
                nonlocal index
                index += 1
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=320)
                headers = {"X-UI-Token": "draft-test"}
                body = None
                if payload is not None:
                    body = json.dumps({"apiVersion": "gbc-ui-v1", "requestId": "draft-http-" + str(index), **payload})
                    headers["Content-Type"] = "application/json"
                connection.request("POST" if payload is not None else "GET", "/api/gbc/v1/" + route, body, headers)
                reply = connection.getresponse()
                status, result = reply.status, json.loads(reply.read())
                connection.close()
                self.assertEqual(status, 200, result)
                return result["data"]

            try:
                settings = call("settings", {"expectedRevision": 0,
                    "roots": [{"path": str(output), "purpose": "output", "confirmTrust": True}],
                    "runtimes": [{"python": str(PYTHON), "role": "data", "confirmExecute": True}]})["settings"]
                service = server.RequestHandlerClass.api.gbc
                images = service.store.workdir("draft_bridge_images")
                image = images / "image.png"
                image.write_bytes(png())
                graphs = images / "graphs.jsonl"
                graphs.write_text(json.dumps(graph(image)) + "\n")
                service.db["artifacts"]["artifact_bridge"] = {"artifactId": "artifact_bridge", "revision": 1,
                    "_graphs": str(graphs), "_imageRoot": str(images),
                    "_records": [{"key": "sample", "image": str(image), "meta": {"sourceKey": "sample"}}]}
                dataset = call("exports", {"artifactId": "artifact_bridge", "confirm": True,
                    "outputRootId": settings["roots"][0]["rootId"],
                    "dataRuntimeId": settings["runtimes"][0]["runtimeId"]})["dataset"]
                self.assertEqual(dataset["validation"], {"state": "valid", "complete": True})
                draft = call("training-drafts", {"datasetId": dataset["datasetId"], "datasetRevision": 1,
                    "shards": [dataset["shards"][0]["name"]], "baseConfigId": "configs/moss/_base/base_lora.py",
                    "captionOptions": {"template": "gbc", "formats": [{"weight": 1, "sections": ["graph"]}]},
                    "knobPatch": {"RUN_NAME": "gbc-never-train", "TARGET_AREA": 32 * 32,
                                  "RESOLUTION_MODE": "square_crop", "SAMPLE_EVERY": 0},
                    "confirmExecution": True})["draft"]
                ref = draft["draftRef"]
                retrieved = call("training-drafts?draftId=" + ref["draftId"] + "&revision=1")["draft"]
                self.assertEqual(retrieved["draftRef"], ref)
                self.assertNotIn("datasetSnapshot", retrieved)
                self.assertNotIn("path", retrieved["values"]["DATASETS"][0])
                checked = call("training-drafts/check", {"draftRef": ref, "confirmExecution": True})["check"]
                self.assertFalse(checked["ok"])
                self.assertEqual(checked["exitCode"], 1)
                self.assertEqual(checked["effectiveHash"], ref["effectiveHash"])
                with patch("kohakumoss_ui.contract.launch._start_unlocked") as spawn:
                    with self.assertRaises(Exception) as blocked:
                        launch.start(TrainerRoot(trainer), draft_ref=ref)
                    self.assertEqual(blocked.exception.code, "VALIDATION_FAILED")
                    spawn.assert_not_called()
                preview = call("captions/preview", {"draftRef": ref,
                    "sampleId": dataset["shards"][0]["name"] + "/sample", "seed": 7})["preview"]
                self.assertIn("<gbc", preview["text"])
                self.assertFalse(preview["budget"]["hardTokenLimit"])
                self.assertIn("crop", preview["geometry"])
                self.assertTrue(call("capabilities")["features"]["trainingDrafts"])
                self.assertTrue(call("capabilities")["features"]["captionPreview"])

                # Named successful-check fixture: no trainer/model process spawned.
                actual = service.drafts.get(ref["draftId"])
                def successful_checker_fixture(root, python, payload, **kwargs):
                    self.assertEqual(payload["op"], "check")
                    data = Path(payload["path"]).read_bytes()
                    return {"ok": True, "exitCode": 0, "output": "TEST CHECKER FIXTURE ONLY",
                            "effectiveHash": "sha256:" + hashlib.sha256(data).hexdigest(),
                            "runtime": actual["runtime"]}
                with patch("kohakumoss_ui.contract.training_drafts.worker", side_effect=successful_checker_fixture):
                    success = call("training-drafts/check", {"draftRef": ref, "confirmExecution": True})["check"]
                    self.assertTrue(success["ok"])
                captured = []
                class ExitedFixture:
                    pid = 99999999
                    def wait(self):
                        return 0
                def fake_spawn(root, config, overrides, name):
                    captured.append(config)
                    self.assertIsNone(overrides)
                    self.assertEqual("sha256:" + hashlib.sha256(Path(config).read_bytes()).hexdigest(), ref["effectiveHash"])
                    argv = launch.command(root, config, {})
                    self.assertEqual(argv[argv.index("--config") + 1], config)
                    self.assertNotIn("--set", argv)
                    return {"fixture": True}, ExitedFixture()
                with patch("kohakumoss_ui.contract.launch._start_unlocked", side_effect=fake_spawn):
                    self.assertTrue(launch.start(TrainerRoot(trainer), draft_ref=ref)["fixture"])
                self.assertEqual(len(captured), 1)
                self.assertNotIn("torch", sys.modules)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()

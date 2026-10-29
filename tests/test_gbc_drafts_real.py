"""Opt-in model-free interoperability through real trainer subprocesses.

KOHAKUMOSS_DRAFT_REAL=1 enables the installed sibling trainer environment.
No trainer code or installed packages are imported in this UI test process.
"""

import copy
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kohakumoss_ui.contract.training_drafts import DraftService
from kohakumoss_ui.contract.training_drafts_io import digest, worker
from kohakumoss_ui.contract.training_drafts_schema import DraftError

ROOT = Path(__file__).resolve().parents[2] / "KohakuMossTrainer"
PYTHON = ROOT / ".venv/bin/python"
ENABLED = os.environ.get("KOHAKUMOSS_DRAFT_REAL") == "1" and PYTHON.is_file()


@unittest.skipUnless(ENABLED, "set KOHAKUMOSS_DRAFT_REAL=1 for real installed trainer")
class RealDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="gbc-draft-real-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.base = Path(cls.temp.name)
        cls.dataset = cls.base / "dataset"
        code = "import runpy,sys; from pathlib import Path; f=runpy.run_path(sys.argv[1]); f['write_dataset'](Path(sys.argv[2]),n=4,per_shard=2,w=64,h=32)"
        result = subprocess.run(
            [
                str(PYTHON),
                "-I",
                "-B",
                "-c",
                code,
                str(ROOT / "tests/_util/gbc.py"),
                str(cls.dataset),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            shell=False,
            check=False,
        )
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.record = {
            "datasetId": "dataset-real",
            "revision": 1,
            "manifestHash": digest((cls.dataset / "dataset.json").read_bytes()),
            "path": str(cls.dataset),
            "shards": ["shard_00000", "shard_00001"],
            "validation": {"state": "valid", "complete": True},
        }
        cls.service = DraftService(
            ROOT,
            cls.base / "drafts",
            dataset_resolver=lambda _: cls.record,
            trusted_configs=["configs/moss/_base/base_lora.py"],
            python=PYTHON,
        )
        cls.addClassCleanup(cls.service.close)
        cls.payload = {
            "datasetId": "dataset-real",
            "datasetRevision": 1,
            "shards": ["shard_00000", "shard_00001"],
            "baseConfigId": "configs/moss/_base/base_lora.py",
            "captionOptions": {
                "template": "gbc",
                "formats": [{"weight": 1, "sections": ["graph"]}],
                "graph": {"keep": 1, "tag_drop": 0},
                "prefix": "prefix \u4e2d\u6587 \U0001f40b " * 30,
            },
            "knobPatch": {
                "RUN_NAME": "real model-free check",
                "TARGET_AREA": 32 * 32,
                "RESOLUTION_MODE": "square_crop",
                "SAMPLE_EVERY": 0,
            },
        }
        cls.draft = cls.service.create(cls.payload)

    def test_real_loader_full_effective_roundtrip(self):
        ref = self.draft["draftRef"]
        path = self.service.store.path / (ref["draftId"] + "-1.py")
        result = worker(
            ROOT, PYTHON, {"op": "load", "root": str(ROOT), "path": str(path)}
        )
        self.assertEqual(result["values"], self.draft["values"])
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("kohakumoss", sys.modules)
        self.assertNotIn("kohakuengine", sys.modules)

    def test_real_check_includes_full_override_and_failure_blocks_launch(self):
        # Baseline weights are intentionally absent. Real checker must report it,
        # while not complaining about the selected, real GBC dataset/caption.
        result = self.service.check(self.draft["draftRef"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["exitCode"], 1)
        self.assertIn("does not exist", result["output"])
        self.assertNotIn("my_dataset", result["output"])
        self.assertNotIn("gbc template", result["output"])
        self.assertEqual(
            result["effectiveHash"], self.draft["draftRef"]["effectiveHash"]
        )
        with self.assertRaises(DraftError):
            self.service.resolve_launch(self.draft["draftRef"])

    def test_real_caption_geometry_reproducible_and_target_gated(self):
        ref = self.draft["draftRef"]
        preview = self.service.preview(
            ref,
            sample_id="shard_00000/100",
            seed=7,
            variant=2,
            target={"width": 32, "height": 32},
        )
        again = self.service.preview(
            ref, sample_id="shard_00000/100", seed=7, variant=2
        )
        self.assertEqual(preview, again)
        self.assertIn(
            "<gbc bbox=yxyx space=abs_centralized canvas=32x32>", preview["text"]
        )
        self.assertEqual(preview["geometry"]["crop"]["ltrb"], [16, 0, 48, 32])
        mapped = {
            r["nodeId"]: r["normalizedLtrb"] for r in preview["geometry"]["mappedBoxes"]
        }
        self.assertEqual(mapped["w"], [0.0, 0.2, 0.5, 0.9])
        self.assertIsNone(mapped["t"])
        self.assertEqual(preview["budget"]["hardTokenLimit"], False)
        self.assertEqual(preview["chosenFormat"], ["graph"])
        self.assertTrue(
            preview["text"].startswith(self.payload["captionOptions"]["prefix"])
        )
        with self.assertRaises(DraftError):
            self.service.preview(
                ref, sample_id="100", target={"width": 64, "height": 32}
            )
        with self.assertRaises(DraftError):
            self.service.preview(ref, sample_id="shard_99999/100")
        self.assertFalse(any(self.dataset.rglob("cache")))

    def test_real_weighted_sources_meta_and_fit(self):
        payload = copy.deepcopy(self.payload)
        payload["captionOptions"] = {
            "template": "gbc",
            "formats": [{"weight": 1, "sections": ["artist", "tags", "quality"]}],
            "meta": {
                "artist": {"field": "artist"},
                "quality": {"field": "rating", "map": {"general": "safe"}},
            },
            "tags": {
                "sources": [
                    {"from": "meta:general", "weight": 2},
                    {"from": "graph:root", "weight": 1},
                ],
                "shuffle": False,
                "drop": 0,
            },
            "fit": {"margin": 0.5, "fallback": ["quality"]},
        }
        payload["knobPatch"]["MAX_TEXT_TOKENS"] = 13
        draft = self.service.create(payload)
        result = self.service.preview(draft["draftRef"], sample_id="100", seed=3)
        self.assertIn(result["chosenTagSource"], ("meta:general", "graph:root"))
        self.assertEqual(result["text"], "safe")
        self.assertFalse(result["budget"]["withinCharacterEstimate"])
        self.assertFalse(result["budget"]["hardTokenLimit"])
        self.assertEqual(result["budget"]["maxChars"], 1)
        self.assertNotEqual(
            draft["draftRef"]["effectiveHash"], self.draft["draftRef"]["effectiveHash"]
        )


if __name__ == "__main__":
    unittest.main()

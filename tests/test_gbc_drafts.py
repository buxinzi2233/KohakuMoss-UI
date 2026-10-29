"""Draft contract tests: no trainer imports, models, GPU, or network."""

import ast
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kohakumoss_ui.contract.training_drafts import DraftService, render_effective
from kohakumoss_ui.contract.training_drafts_io import digest
from kohakumoss_ui.contract.training_drafts_schema import (
    DraftError,
    canonical,
    caption,
    strict_json,
)

CAPTION = {
    "template": "gbc",
    "formats": [{"weight": 1, "sections": ["graph", "quality"]}],
    "graph": {"keep": 1.0},
    "meta": {"quality": {"field": "quality"}},
    "prefix": "long caption \u4e2d\u6587 \U0001f40b \\ ' \" " * 40,
}
DEFAULTS = {
    "CROP": "center",
    "FLIP": False,
    "RESOLUTION_MODE": "resize",
    "DATASETS": [],
    "CAPTION": {},
    "RUN_NAME": "",
    "LR": 0.001,
    "STEPS": 1,
    "PARALLEL": "none",
    "OPTIMIZER": "adamw",
    "DIM": 32,
    "TEXT_ENCODE": {},
    "SAMPLE_PROMPTS": [],
}


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "trainer"
        (self.root / "configs/moss").mkdir(parents=True)
        self.config = self.root / "configs/moss/base.py"
        self.config.write_text('"""scripts/train/moss.py"""\nSTEPS=1\n')
        self.dataset = self.base / "data-\u4e2d\u6587"
        (self.dataset / "shards/shard_00000").mkdir(parents=True)
        manifest = {
            "format": "kohakugbc-dataset",
            "version": 1,
            "shards": [{"name": "shard_00000"}],
        }
        (self.dataset / "dataset.json").write_bytes(canonical(manifest))
        for name in ("manifest.parquet", "images.zip", "graphs.db", "meta.db"):
            (self.dataset / "shards/shard_00000" / name).write_bytes(
                b"unit fixture only"
            )
        self.record = {
            "datasetId": "dataset-test",
            "revision": 1,
            "manifestHash": digest(b"registration"),
            "path": str(self.dataset),
            "shards": ["shard_00000"],
            "validation": {"state": "valid", "complete": True},
        }
        self.service = DraftService(
            self.root,
            self.base / "drafts",
            dataset_resolver=lambda _: self.record,
            trusted_configs=["configs/moss/base.py"],
            python=sys.executable,
        )
        self.addCleanup(self.service.close)
        self.payload = {
            "datasetId": "dataset-test",
            "datasetRevision": 1,
            "shards": ["shard_00000"],
            "baseConfigId": "configs/moss/base.py",
            "captionOptions": copy.deepcopy(CAPTION),
            "knobPatch": {"RUN_NAME": "draft ' \\ \u4e2d\u6587", "LR": 0.002},
        }
        self.calls = []
        self.mock = patch(
            "kohakumoss_ui.contract.training_drafts.worker",
            side_effect=self.fake_worker,
        )
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def fake_worker(self, root, python, payload, **kwargs):
        self.calls.append(payload)
        if payload["op"] == "load":
            return {
                "values": copy.deepcopy(DEFAULTS),
                "defaults": copy.deepcopy(DEFAULTS),
                "runtime": {"test": True},
            }
        if payload["op"] == "check":
            return {
                "ok": True,
                "exitCode": 0,
                "output": "checked effective file",
                "runtime": {"test": True},
                "effectiveHash": digest(Path(payload["path"]).read_bytes()),
            }
        self.fail("Unexpected worker call")

    def test_typed_roundtrip_full_fields_and_checked_file(self):
        draft = self.service.create(self.payload)
        ref = draft["draftRef"]
        self.assertEqual(self.service.get(ref["draftId"]), draft)
        self.assertEqual(draft["values"]["DATASETS"][0]["caption"], CAPTION)
        self.assertGreater(
            len(draft["values"]["DATASETS"][0]["caption"]["prefix"]), 200
        )
        with self.assertRaises(DraftError):
            self.service.resolve_launch(ref)
        checked = self.service.check(ref)
        resolved = self.service.resolve_launch(ref)
        self.assertTrue(checked["ok"])
        self.assertEqual(resolved["config"], self.calls[-1]["path"])
        self.assertEqual(resolved["overrides"], {})
        self.assertEqual(
            digest(Path(resolved["config"]).read_bytes()), ref["effectiveHash"]
        )
        tree = ast.parse(Path(resolved["config"]).read_text())
        self.assertTrue(all(isinstance(n, (ast.Assign, ast.Expr)) for n in tree.body))
        values = {
            n.targets[0].id: ast.literal_eval(n.value)
            for n in tree.body
            if isinstance(n, ast.Assign)
        }
        self.assertEqual(values, draft["values"])
        for p in (self.base / "drafts").iterdir():
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.base / "drafts").stat().st_mode & 0o777, 0o700)

    def test_multiple_entries_preserve_per_entry_caption_and_weights(self):
        entries = [
            {
                "source": "gbc",
                "path": str(self.dataset),
                "shards": ["shard_00000"],
                "caption": copy.deepcopy(CAPTION),
                "weight": 0.5,
                "loss_weight": 2.0,
            }
            for _ in range(2)
        ]
        entries[1]["caption"]["prefix"] = "second" * 90
        self.payload["knobPatch"]["DATASETS"] = entries
        draft = self.service.create(self.payload)
        self.assertEqual(
            self.service.get(draft["draftRef"]["draftId"])["values"]["DATASETS"],
            entries,
        )

    def test_geometry_conflicts_are_not_overwritten(self):
        for key, value in (
            ("CROP", "random"),
            ("FLIP", True),
            ("RESOLUTION_MODE", "shifted_square"),
        ):
            with self.subTest(key=key):
                payload = copy.deepcopy(self.payload)
                payload["knobPatch"][key] = value
                with self.assertRaises(DraftError):
                    self.service.create(payload)

    def test_empty_unknown_duplicate_shards(self):
        for shards in (
            [],
            ["missing"],
            ["shard_00000", "shard_00000"],
            ["../shard_00000"],
        ):
            with self.subTest(shards=shards), self.assertRaises(DraftError):
                self.service.create({**self.payload, "shards": shards})

    def test_unvalidated_registration_rejected(self):
        self.record["validation"]["state"] = "unchecked"
        with self.assertRaises(DraftError):
            self.service.create(self.payload)

    def test_path_and_import_injection_rejected(self):
        for path in (
            "os.system",
            "/tmp/evil.py",
            "configs/../evil.py",
            "configs/moss/not-allowlisted.py",
        ):
            with self.subTest(path=path), self.assertRaises(DraftError):
                self.service.create({**self.payload, "baseConfigId": path})
        for knob in (
            {"OPTIMIZER": "os.system"},
            {"PARALLEL": "os.system"},
            {"UNKNOWN": 1},
            {"STEPS": True},
            {"TEXT_ENCODE": {"factory": "os.system"}},
            {
                "DATASETS": [
                    {
                        "source": "os.system",
                        "path": str(self.dataset),
                        "shards": ["shard_00000"],
                        "caption": CAPTION,
                    }
                ]
            },
        ):
            with self.subTest(knob=knob), self.assertRaises(DraftError):
                self.service.create({**self.payload, "knobPatch": knob})
        for cap in (
            {**CAPTION, "meta_formatter": "os.system"},
            {**CAPTION, "metadata": {}},
            {**CAPTION, "formats": []},
            {**CAPTION, "graph": {"arbitrary": 1}},
            {**CAPTION, "tags": {"sources": [{"from": "os.system"}]}},
            {**CAPTION, "fit": {"keep_step": -0.1}},
            {**CAPTION, "underscores": "False"},
        ):
            with self.subTest(cap=cap), self.assertRaises(DraftError):
                caption(cap)

    def test_changed_effective_file_rejected(self):
        ref = self.service.create(self.payload)["draftRef"]
        self.service.check(ref)
        path = Path(self.service.resolve_launch(ref)["config"])
        path.write_text(path.read_text() + "STEPS=999\n")
        with self.assertRaises(DraftError):
            self.service.resolve_launch(ref)

    def test_base_dependency_and_dataset_content_invalidate(self):
        for source in (self.config, self.dataset / "shards/shard_00000/meta.db"):
            with self.subTest(source=source):
                ref = self.service.create(self.payload)["draftRef"]
                self.service.check(ref)
                source.write_bytes(source.read_bytes() + b"# changed\n")
                with self.assertRaises(DraftError):
                    self.service.resolve_launch(ref)
                self.assertEqual(
                    self.service.get(ref["draftId"])["validation"]["status"], "stale"
                )

    def test_revision_hash_and_registration_revision_mismatch(self):
        ref = self.service.create(self.payload)["draftRef"]
        self.service.check(ref)
        for bad in ({**ref, "effectiveHash": digest(b"wrong")}, {**ref, "revision": 2}):
            with self.assertRaises(DraftError):
                self.service.resolve_launch(bad)
        self.record["revision"] = 2
        with self.assertRaises(DraftError):
            self.service.resolve_launch(ref)

    def test_symlink_hardlink_and_directory_swap_rejected(self):
        ref = self.service.create(self.payload)["draftRef"]
        self.service.check(ref)
        path = Path(self.service.resolve_launch(ref)["config"])
        saved = path.read_bytes()
        path.unlink()
        other = self.base / "other.py"
        other.write_bytes(saved)
        other.chmod(0o600)
        path.symlink_to(other)
        with self.assertRaises((OSError, DraftError)):
            self.service.resolve_launch(ref)
        path.unlink()
        os.link(other, path)
        with self.assertRaises(DraftError):
            self.service.resolve_launch(ref)
        (self.base / "drafts").rename(self.base / "moved")
        (self.base / "drafts").mkdir(mode=0o700)
        with self.assertRaises(DraftError):
            self.service.get(ref["draftId"])

    def test_launch_hook_and_explicit_revision(self):
        from kohakumoss_ui.contract.training_drafts import (
            register_launch_service,
            resolve_for_launch,
        )

        with self.assertRaises(DraftError):
            resolve_for_launch(self.service.root, {})
        register_launch_service(self.service)
        ref = self.service.create(self.payload)["draftRef"]
        for revision in (0, False, "1"):
            with self.assertRaises(DraftError):
                self.service.get(ref["draftId"], revision)
        self.service.check(ref)
        self.assertEqual(
            resolve_for_launch(self.service.root, ref),
            Path(self.service.resolve_launch(ref)["config"]),
        )

    def test_mutation_during_check_never_saves_valid_receipt(self):
        ref = self.service.create(self.payload)["draftRef"]
        old = self.fake_worker

        def mutate(root, python, payload, **kwargs):
            result = old(root, python, payload, **kwargs)
            if payload["op"] == "check":
                self.config.write_text(self.config.read_text() + "# mutation\n")
            return result

        with (
            patch("kohakumoss_ui.contract.training_drafts.worker", side_effect=mutate),
            self.assertRaises(DraftError),
        ):
            self.service.check(ref)
        self.assertNotEqual(
            self.service.get(ref["draftId"])["validation"]["status"], "valid"
        )

    def test_worker_timeout_and_output_bound(self):
        import subprocess

        from kohakumoss_ui.contract.training_drafts_io import worker

        real_popen = subprocess.Popen

        def sleeper(argv, **kwargs):
            return real_popen(
                [sys.executable, "-I", "-c", "import time;time.sleep(30)"], **kwargs
            )

        with patch(
            "kohakumoss_ui.contract.training_drafts_io.subprocess.Popen",
            side_effect=sleeper,
        ):
            with self.assertRaises(DraftError) as error:
                worker(self.root, sys.executable, {"op": "load"}, timeout=0.05)
            self.assertEqual(error.exception.code, "WORKER_TIMEOUT")

        def flood(argv, **kwargs):
            return real_popen(
                [sys.executable, "-I", "-c", "import os;os.write(1,b'x'*5000000)"],
                **kwargs,
            )

        with patch(
            "kohakumoss_ui.contract.training_drafts_io.subprocess.Popen",
            side_effect=flood,
        ):
            with self.assertRaises(DraftError) as error:
                worker(self.root, sys.executable, {"op": "load"}, timeout=5)
            self.assertEqual(error.exception.code, "LIMIT_EXCEEDED")

    def test_strict_json_and_literal_safety(self):
        for raw in ('{"x":1,"x":2}', '{"x":NaN}'):
            with self.assertRaises(DraftError):
                strict_json(raw)
        malicious = "'; __import__('os').system('bad'); #"
        with self.assertRaises(DraftError):
            render_effective(
                {"CAPTION": {"template": "gbc", "meta_formatter": "os.system"}}
            )
        with self.assertRaises(DraftError):
            render_effective({"PARALLEL": "os.system"})
        tree = ast.parse(render_effective({"RUN_NAME": malicious}))
        self.assertEqual(ast.literal_eval(tree.body[1].value), malicious)
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("kohakumoss", sys.modules)
        self.assertNotIn("kohakuengine", sys.modules)


if __name__ == "__main__":
    unittest.main()

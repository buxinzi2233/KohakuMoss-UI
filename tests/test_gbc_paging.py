"""Dataset streaming and metadata-only paging regressions; no giant native sample."""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kohakumoss_ui.contract import gbc_storage_files as files
from kohakumoss_ui.contract.gbc import GbcService
from kohakumoss_ui.contract.gbc_schema import GbcError, digest
from kohakumoss_ui.contract.root import TrainerRoot


class DatasetPagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gbc-paging-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "dataset"
        self.root.mkdir()
        (self.root / "shards" / "shard_00000").mkdir(parents=True)
        self.file = self.root / "shards" / "shard_00000" / "images.zip"
        self.file.write_bytes(b"test fixture, not a native zip")
        (self.root / "dataset.json").write_bytes(b'{"fixture":true}')
        self.service = GbcService(TrainerRoot(self.base / "trainer"), state=self.base / "state")
        self.addCleanup(self.service.close)
        self.service._load()

    def register_fixture(self, large=False):
        if large:
            with self.file.open("wb") as sink:
                sink.truncate(257 * 1024 * 1024 + 17)
        identities = files.inventory(self.root)
        record = {"datasetId": "dataset_fixture", "revision": 1, "manifestHash": digest("fixture"),
                  "_path": str(self.root), "_identities": identities}
        self.service.db["datasets"]["dataset_fixture"] = record
        return record

    def page(self):
        return self.service.get("datasets/page", {"datasetId": "dataset_fixture",
            "dataRuntimeId": "runtime_fixture", "offset": "7", "limit": "2"})

    def test_page_no_content_reads_walk_or_hash_even_for_large_sparse_shard(self):
        self.register_fixture(large=True)
        expected = {"items": [{"key": "selected"}], "nextOffset": 8}
        with (patch.object(self.service, "_data", return_value=expected) as worker,
              patch.object(self.service, "_tree_manifest", side_effect=AssertionError("generation hash used")),
              patch.object(files, "snapshot", side_effect=AssertionError("full dataset hash used")),
              patch("os.walk", side_effect=AssertionError("page rediscovered the tree")),
              patch("os.read", side_effect=AssertionError("service read dataset content")),
              patch("kohakumoss_ui.contract.gbc.read_file", side_effect=AssertionError("whole file read"))):
            status, response = self.page()
        self.assertEqual(status, 200)
        self.assertEqual(response["data"], expected)
        worker.assert_called_once_with("runtime_fixture", {"action": "page", "path": str(self.root), "offset": 7, "limit": 2})

    def test_page_detects_content_change_even_with_restored_mtime(self):
        self.register_fixture()
        info = self.file.stat()
        with self.file.open("r+b") as stream:
            stream.write(b"EDIT")
        os.utime(self.file, ns=(info.st_atime_ns, info.st_mtime_ns))
        with patch.object(self.service, "_data") as worker:
            with self.assertRaises(GbcError) as error:
                self.page()
            self.assertEqual(error.exception.code, "SOURCE_CHANGED")
            worker.assert_not_called()

    def test_page_checks_again_after_worker_and_detects_added_file(self):
        self.register_fixture()
        def changing_worker(*_):
            (self.file.parent / "extra-file").write_bytes(b"changed tree")
            return {"items": []}
        with (patch.object(self.service, "_data", side_effect=changing_worker),
              self.assertRaises(GbcError) as error):
            self.page()
        self.assertEqual(error.exception.code, "SOURCE_CHANGED")

    def test_page_never_silently_baselines_legacy_registration(self):
        self.register_fixture().pop("_identities")
        with patch.object(self.service, "_data") as worker:
            with self.assertRaises(GbcError) as error:
                self.page()
            self.assertEqual(error.exception.code, "SOURCE_CHANGED")
            worker.assert_not_called()

    def test_sparse_hash_over_256_mib_is_streamed(self):
        size = 257 * 1024 * 1024 + 17
        with self.file.open("wb") as sink:
            sink.truncate(size)
        original = os.read
        requests = []
        def tracked(fd, amount):
            requests.append(amount)
            return original(fd, amount)
        with (patch("os.read", side_effect=tracked),
              patch("kohakumoss_ui.contract.gbc_paths.read_file", side_effect=AssertionError("whole file read")),
              patch.object(Path, "read_bytes", side_effect=AssertionError("whole file read"))):
            manifest, identities = files.snapshot(self.root)
        row = next(row for row in manifest if row["path"].endswith("images.zip"))
        self.assertEqual(row["bytes"], size)
        self.assertGreater(len(requests), 257)
        self.assertEqual(max(requests), files.CHUNK)
        self.assertGreater(files.MAX_FILE, size)
        files.guard(self.root, identities)

    def test_copy_streamed_exclusive_and_independent_limit(self):
        raw = b"abcd" * (files.CHUNK // 4)
        with self.file.open("wb") as sink:
            for _ in range(3):
                sink.write(raw)
        target = self.base / "copy"
        original = os.read
        requests = []
        def tracked(fd, amount):
            requests.append(amount)
            return original(fd, amount)
        with (patch("os.read", side_effect=tracked),
              patch("kohakumoss_ui.contract.gbc_paths.LIMIT", 16),
              patch("kohakumoss_ui.contract.gbc_paths.read_file", side_effect=AssertionError("whole file read"))):
            manifest, identities = files.copy_dataset(self.root, target)
        self.assertTrue(all(amount <= files.CHUNK for amount in requests))
        row = next(row for row in manifest if row["path"].endswith("images.zip"))
        self.assertEqual(row["hash"], "sha256:" + hashlib.sha256(raw * 3).hexdigest())
        files.guard(target, identities)
        self.assertEqual((target / "shards/shard_00000/images.zip").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            files.copy_dataset(self.root, target)

    def test_copy_rejects_hardlink_symlink_and_special_file(self):
        linked = self.root / "bad"
        for kind in ("hard", "symlink", "fifo"):
            with self.subTest(kind=kind):
                if kind == "hard":
                    os.link(self.file, linked)
                elif kind == "symlink":
                    linked.symlink_to(self.file)
                else:
                    os.mkfifo(linked)
                with self.assertRaises(GbcError):
                    files.copy_dataset(self.root, self.base / ("out-" + kind))
                linked.unlink()

    def test_explicit_validate_reestablishes_same_content_fence(self):
        manifest, _ = files.snapshot(self.root)
        self.service.db["datasets"]["dataset_fixture"] = {
            "datasetId": "dataset_fixture", "revision": 1, "manifestHash": digest(manifest), "_path": str(self.root)}
        payload = {"apiVersion": "gbc-ui-v1", "requestId": "explicit-validation", "datasetId": "dataset_fixture",
                   "expectedRevision": 1, "dataRuntimeId": "runtime_fixture", "confirm": True}
        with patch.object(self.service, "_data", return_value={"complete": True, "validatedSamples": 1}):
            status, response = self.service.post("datasets/validate", payload)
        self.assertEqual(status, 200, response)
        self.assertEqual(response["data"]["dataset"]["revision"], 2)
        self.service.resolve_dataset("dataset_fixture", deep=False)
        self.file.write_bytes(b"different content")
        with patch.object(self.service, "_data") as worker:
            payload.update(requestId="changed-validation", expectedRevision=2)
            status, response = self.service.post("datasets/validate", payload)
            self.assertEqual(response["error"]["code"], "SOURCE_CHANGED")
            worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()

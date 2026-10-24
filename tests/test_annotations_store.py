"""Offline security and CAS checks; never access the user's default state."""

import concurrent.futures
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from kohakumoss_ui.store.annotations import AnnotationStore, StoreError, MAX_RECORD


class AnnotationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.store = AnnotationStore(self.state)
        self.addCleanup(self.store.close)
        self.key = ("lib_" + "a" * 32, "doc_" + "b" * 32)

    def assert_error(self, code, function, *args):
        with self.assertRaises(StoreError) as cm:
            function(*args)
        self.assertEqual(cm.exception.code, code)
        self.assertEqual(cm.exception.payload["error"]["code"], code)
        return cm.exception

    def test_lazy_and_missing_read_no_creation(self):
        with patch("kohakumoss_ui.paths.state_dir", side_effect=AssertionError):
            AnnotationStore()
        self.assertIsNone(self.store.read_record("settings", "current"))
        self.assertFalse(self.state.exists())

    def test_cas_permissions_and_translation_revision(self):
        result = self.store.compare_write("translations", self.key, 0, {"history": []})
        self.assertEqual(result, {"revision": 1, "record": {"history": [], "revision": 1}})
        self.assertEqual(self.store.read_record("translations", self.key), result)
        self.assert_error("VERSION_CONFLICT", self.store.compare_write,
                          "translations", self.key, 0, {})
        self.assertEqual(self.store.compare_write("translations", self.key, 1, {})["revision"], 2)
        for parent, dirs, files in os.walk(self.state):
            self.assertEqual(Path(parent).stat().st_mode & 0o777, 0o700)
            for name in files:
                self.assertEqual((Path(parent) / name).stat().st_mode & 0o777, 0o600)

    def test_invalid_kinds_and_keys(self):
        for kind, key in [("credentials", "current"), ("settings", "../current"),
                          ("translations", [*self.key]), ("requests", "req_demo")]:
            self.assert_error("INVALID_REQUEST", self.store.read_record, kind, key)
        self.assert_error("INVALID_REQUEST", self.store.compare_write,
                          "settings", "current", True, {})

    def test_corrupt_fail_closed(self):
        self.store.compare_write("settings", "current", 0, {})
        path = self.state / "annotations/translation-settings.json"
        for data in [b"{", b'{"revision":1,"revision":2,"record":{}}',
                     b'{"revision":true,"record":{}}', b'{"revision":1,"record":{"x":NaN}}',
                     b'{"revision":1,"record":{"x":1e999}}',
                     b'{"revision":1,"record":{"x":"\\ud800"}}']:
            path.write_bytes(data)
            self.assert_error("RECOVERY_REQUIRED", self.store.read_record, "settings", "current")
            self.assert_error("RECOVERY_REQUIRED", self.store.compare_write,
                              "settings", "current", 0, {})
            self.assertEqual(path.read_bytes(), data)

    def test_symlink_hardlink_fifo_and_mode(self):
        self.store.compare_write("settings", "current", 0, {})
        path = self.state / "annotations/translation-settings.json"
        original = path.read_bytes()
        path.chmod(0o644)
        self.assert_error("UNSAFE_STORAGE", self.store.read_record, "settings", "current")
        path.chmod(0o600)
        other = Path(self.temp.name) / "other"
        os.link(path, other)
        self.assert_error("UNSAFE_STORAGE", self.store.read_record, "settings", "current")
        other.unlink()
        path.unlink()
        other.write_bytes(original)
        path.symlink_to(other)
        self.assert_error("UNSAFE_STORAGE", self.store.read_record, "settings", "current")
        self.assert_error("UNSAFE_STORAGE", self.store.compare_write, "settings", "current", 0, {})
        path.unlink()
        os.mkfifo(path, 0o600)
        self.assert_error("UNSAFE_STORAGE", self.store.read_record, "settings", "current")

    def test_parent_symlink_permissions_and_replacement(self):
        self.store.acquire_owner()
        private = self.state / "annotations"
        private.chmod(0o750)
        self.assert_error("UNSAFE_STORAGE", self.store.read_record, "settings", "current")
        private.chmod(0o700)
        moved = self.state / "old"
        private.rename(moved)
        private.symlink_to(moved, target_is_directory=True)
        self.assert_error("UNSAFE_STORAGE", self.store.read_record, "settings", "current")
        private.unlink()
        private.mkdir(mode=0o700)
        self.assert_error("UNSAFE_STORAGE", self.store.compare_write, "settings", "current", 0, {})

    def test_world_writable_nonsticky_ancestor_and_sticky_child(self):
        unsafe = Path(self.temp.name) / "unsafe"
        unsafe.mkdir(mode=0o777)
        unsafe.chmod(0o777)
        store = AnnotationStore(unsafe / "state")
        self.addCleanup(store.close)
        self.assert_error("UNSAFE_STORAGE", store.acquire_owner)
        # /tmp's immediate child must be uid-owned 0700, even as an ancestor.
        os.chmod(self.temp.name, 0o755)
        self.addCleanup(os.chmod, self.temp.name, 0o700)
        self.assert_error("UNSAFE_STORAGE", self.store.acquire_owner)

    def test_credentials_private_and_not_record_route(self):
        self.assertIsNone(self.store.read_credentials())
        self.store.write_credentials({"apiKey": "private", "salt": "fixture"})
        self.assertEqual(self.store.read_credentials()["apiKey"], "private")
        self.assert_error("INVALID_REQUEST", self.store.read_record, "credentials", "current")
        path = self.state / "annotations/translation-credentials.json"
        path.write_bytes(b"broken")
        self.assert_error("RECOVERY_REQUIRED", self.store.write_credentials, {})
        self.assertEqual(path.read_bytes(), b"broken")

    def test_owner_process_lock_and_release(self):
        self.store.acquire_owner()
        self.store.acquire_owner()
        other = AnnotationStore(self.state)
        self.addCleanup(other.close)
        error = self.assert_error("BUSY", other.compare_write, "settings", "current", 0, {})
        self.assertTrue(error.payload["error"]["retryable"])
        script = ("from kohakumoss_ui.store.annotations import AnnotationStore,StoreError; "
                  "s=AnnotationStore(__import__('sys').argv[1]);\n"
                  "try: s.acquire_owner()\n"
                  "except StoreError as e: print(e.code)\n")
        result = subprocess.run([sys.executable, "-c", script, str(self.state)],
                                text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.strip(), "BUSY")
        self.store.close()
        other.acquire_owner()

    def test_owner_link_refused(self):
        self.store.acquire_owner()
        self.store.close()
        lock = self.state / "annotations/owner.lock"
        os.link(lock, self.state / "linked")
        self.assert_error("UNSAFE_STORAGE", self.store.acquire_owner)

    def test_owner_uid_refused(self):
        self.store.acquire_owner()
        original = os.fstat
        def wrong_owner(fd):
            result = original(fd)
            fields = list(result)
            fields[4] = os.getuid() + 1
            return os.stat_result(fields)
        with patch("os.fstat", side_effect=wrong_owner):
            self.assert_error("UNSAFE_STORAGE", self.store.read_record, "settings", "current")

    def test_concurrent_cas_one_winner(self):
        def write(_):
            try:
                return self.store.compare_write("settings", "current", 0, {})["revision"]
            except StoreError as exc:
                return exc.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(write, range(12)))
        self.assertEqual(results.count(1), 1)
        self.assertEqual(results.count("VERSION_CONFLICT"), 11)

    def test_list_recovery_and_size_history_limit(self):
        job = "job_" + "d" * 32
        self.store.compare_write("translation-jobs", job, 0, {"status": "running"})
        self.assertEqual(self.store.list_records("translation-jobs")[0]["record"]["status"], "running")
        self.assertEqual(self.store.list_records("requests"), [])
        self.assert_error("STORAGE_FULL", self.store.compare_write,
                          "translations", self.key, 0, {"history": [{}] * 11})
        self.assert_error("STORAGE_FULL", self.store.compare_write,
                          "settings", "current", 0, {"x": "a" * MAX_RECORD})
        self.assertIsNone(self.store.read_record("settings", "current"))
        self.store.compare_write("translations", self.key, 0, {})
        path = self.state / "annotations/translations" / self.key[0] / (self.key[1] + ".json")
        path.write_text(json.dumps({"revision": 1, "record": {"revision": True}}))
        self.assert_error("RECOVERY_REQUIRED", self.store.read_record, "translations", self.key)
        path.rename(path.with_suffix(""))
        self.assert_error("UNSAFE_STORAGE", self.store.list_records, "translations")

    def test_write_fsync_and_failure_keep_old(self):
        self.store.compare_write("settings", "current", 0, {"old": True})
        original = os.fsync
        modes = []
        def fsync(fd):
            modes.append(os.fstat(fd).st_mode)
            original(fd)
        with patch("os.fsync", side_effect=fsync):
            self.store.compare_write("settings", "current", 1, {"old": False})
        import stat
        self.assertTrue(any(stat.S_ISDIR(mode) for mode in modes))
        self.assertTrue(any(stat.S_ISREG(mode) for mode in modes))
        with patch("os.replace", side_effect=OSError(errno.ENOSPC, "full")):
            self.assert_error("STORAGE_FULL", self.store.compare_write,
                              "settings", "current", 2, {})
        self.assertEqual(self.store.read_record("settings", "current")["revision"], 2)
        self.assertFalse(list((self.state / "annotations").glob(".tmp-*")))


if __name__ == "__main__":
    unittest.main()

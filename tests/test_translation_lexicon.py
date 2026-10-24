"""Synthetic SQLite fixtures only; download tests use an in-memory opener."""

import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from kohakumoss_ui.contract import lexicon as module
from kohakumoss_ui.contract.lexicon import Lexicon, LexiconError, REPOSITORY_URL
from kohakumoss_ui.store.annotations import AnnotationStore


class LexiconTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = AnnotationStore(self.root / "state")
        self.addCleanup(self.store.close)
        self.lexicon = Lexicon(self.store)
        self.serial = 0

    def fixture(self, tables=None, schema=None):
        self.serial += 1
        path = self.root / (str(self.serial) + ".sqlite")
        with sqlite3.connect(path) as db:
            for name, rows in (tables or {"real_table": [("blue_hair", 0, "blue", 7),
                                                       ("empty", None, "", None)]}).items():
                db.execute('CREATE TABLE "' + name.replace('"', '""') + '" (' +
                           (schema or "name TEXT, category INTEGER, cn_name TEXT, post_count INTEGER") + ")")
                if rows:
                    db.executemany('INSERT INTO "' + name.replace('"', '""') + '" VALUES (?, ?, ?, ?)', rows)
        return path

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(LexiconError) as cm:
            function(*args, **kwargs)
        self.assertEqual(cm.exception.code, code)
        self.assertEqual(cm.exception.payload["error"]["code"], code)
        return cm.exception

    def test_offline_missing_status_no_state(self):
        with patch("urllib.request.build_opener", side_effect=AssertionError("network")):
            self.assertEqual(self.lexicon.status(), {
                "status": "missing", "dictionaryVersion": None, "tableName": None,
                "rows": 0, "ref": None, "repositoryUrl": REPOSITORY_URL,
                "license": "MIT", "error": None})
            self.assertIsNone(self.lexicon.manifest())
            self.assertIsNone(self.lexicon.lookup("blue_hair"))
        self.assertFalse((self.root / "state").exists())

    def test_import_manifest_hash_permissions_and_no_mutation(self):
        source = self.fixture()
        before = source.read_bytes()
        manifest = self.lexicon.import_file(source)
        sha = hashlib.sha256(before).hexdigest()
        canonical = json.dumps({"sqliteSha256": sha, "tableName": "real_table", "schemaVersion": 1},
                               ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(manifest["dictionaryVersion"], "dict_" + hashlib.sha256(canonical).hexdigest())
        self.assertEqual(manifest["sqliteSha256"], sha)
        self.assertEqual(manifest["rows"], 2)
        self.assertEqual(manifest["columns"], ["name", "category", "cn_name", "post_count"])
        self.assertEqual(self.lexicon.manifest(), manifest)
        self.assertEqual(self.lexicon.manifest(manifest["dictionaryVersion"]), manifest)
        self.assertEqual(self.lexicon.status()["status"], "ready")
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(list(source.parent.glob("*-wal")), [])
        for parent, dirs, files in os.walk(self.root / "state"):
            self.assertEqual(Path(parent).stat().st_mode & 0o777, 0o700)
            for name in files:
                self.assertEqual((Path(parent) / name).stat().st_mode & 0o777, 0o600)

    def test_exact_normalization_missing_and_parameterized_lookup(self):
        path = self.fixture({"quoted\"table": [
            ("blue_hair", 1, "one", 2), ("a_(b)", None, "bracket", None),
            ("x' OR 1=1 --", 2, "literal", 5), ("x'_OR_1=1_--", 3, "bound", 6),
            ("empty", None, "", None)]})
        self.lexicon.import_file(path)
        self.assertEqual(self.lexicon.lookup("  blue hair "), {"targetText": "one", "category": 1, "postCount": 2})
        self.assertEqual(self.lexicon.lookup(r"a_\(b\)")["targetText"], "bracket")
        self.assertEqual(self.lexicon.lookup("x' OR 1=1 --")["targetText"], "bound")
        for text in ["Blue hair", "absent", "empty", "' OR 1=1 --"]:
            self.assertIsNone(self.lexicon.lookup(text))

    def test_multiple_tables_explicit_choice_and_versions(self):
        path = self.fixture({"first": [("tag", 1, "first target", 2)],
                             "second": [("tag", 2, "second target", 3)]})
        error = self.assert_error("DICTIONARY_TABLE_REQUIRED", self.lexicon.import_file, path)
        self.assertEqual(error.payload["error"]["details"]["tables"], ["first", "second"])
        first = self.lexicon.import_file(path, "first")
        second = self.lexicon.import_file(path, "second", expected_version=first["dictionaryVersion"])
        self.assertEqual(first["sqliteSha256"], second["sqliteSha256"])
        self.assertNotEqual(first["dictionaryVersion"], second["dictionaryVersion"])
        self.assertEqual(self.lexicon.lookup("tag", first["dictionaryVersion"])["targetText"], "first target")
        self.assertEqual(self.lexicon.lookup("tag")["targetText"], "second target")

    def test_pinned_versions_ignore_mutable_overrides(self):
        path = self.fixture({"first": [("tag", 1, "first target", 2)],
                             "second": [("tag", 2, "second target", 3)]})
        first = self.lexicon.import_file(path, "first")
        second = self.lexicon.import_file(path, "second")
        self.lexicon.set_override("tag", "mutable override")
        self.assertEqual(self.lexicon.lookup("tag")["targetText"], "mutable override")
        self.assertEqual(self.lexicon.lookup("tag", first["dictionaryVersion"])["targetText"], "first target")
        self.assertEqual(self.lexicon.lookup("tag", second["dictionaryVersion"])["targetText"], "second target")
        self.assertEqual(self.lexicon.manifest(), second)

    def test_unknown_table_invalid_schema_and_views(self):
        path = self.fixture()
        self.assert_error("DICTIONARY_INVALID", self.lexicon.import_file, path, "missing")
        for schema in ["name TEXT, category INTEGER, target TEXT, post_count INTEGER",
                       "name BLOB, category INTEGER, cn_name TEXT, post_count INTEGER",
                       "name TEXT, category TEXT, cn_name TEXT, post_count INTEGER"]:
            bad = self.fixture({"table1": []}, schema)
            self.assert_error("DICTIONARY_INVALID", self.lexicon.import_file, bad)
        self.serial += 1
        path = self.root / (str(self.serial) + ".sqlite")
        with sqlite3.connect(path) as db:
            db.execute("CREATE VIEW bait AS SELECT 'x' AS name, 1 AS category, 'y' AS cn_name, 2 AS post_count")
        self.assert_error("DICTIONARY_INVALID", self.lexicon.import_file, path)

    def test_full_row_type_duplicate_and_null_validation(self):
        invalid = [(None, 0, "target", 1), ("", 0, "target", 1),
                   ("bad", "text", "target", 1), ("bad", 0.5, "target", 1),
                   ("bad", 0, None, 1), ("bad", 0, b"blob", 1),
                   ("bad", 0, "target", "lots")]
        for row in invalid:
            with self.subTest(row=row):
                path = self.fixture({"data": [("good", 0, "good", 1)] * 1 + [row]})
                self.assert_error("DICTIONARY_INVALID", self.lexicon.import_file, path)
        dup = self.fixture({"data": [("same", 0, "a", 1), ("same", 1, "b", 2)]})
        self.assert_error("DICTIONARY_INVALID", self.lexicon.import_file, dup)
        nullable = self.fixture({"data": [("okay", None, "value", None)]})
        self.lexicon.import_file(nullable)
        self.assertEqual(self.lexicon.lookup("okay")["category"], None)

    def test_override_persists_without_modifying_dictionary(self):
        source = self.fixture()
        manifest = self.lexicon.import_file(source)
        blob = self.root / "state/annotations/dictionary/versions" / manifest["dictionaryVersion"] / "tag.sqlite"
        before = blob.read_bytes()
        self.lexicon.set_override("blue hair", "corrected")
        self.lexicon.set_override("new tag", "new correction")
        again = Lexicon(self.store)
        self.assertEqual(again.lookup("blue_hair")["targetText"], "corrected")
        self.assertTrue(again.lookup("new_tag")["override"])
        self.assertEqual(blob.read_bytes(), before)
        self.assertEqual(again.manifest(), manifest)
        self.lexicon.set_override("blue_hair", "")
        self.assertIsNone(again.lookup("blue_hair"))

    def test_corrupt_update_rolls_back_and_idempotent_import(self):
        source = self.fixture()
        manifest = self.lexicon.import_file(source)
        rev = self.store.read_record("dictionary", "current")["revision"]
        self.assertEqual(self.lexicon.import_file(source), manifest)
        self.assertEqual(self.store.read_record("dictionary", "current")["revision"], rev)
        corrupt = self.root / "corrupt.sqlite"
        corrupt.write_bytes(b"not sqlite")
        self.assert_error("DICTIONARY_INVALID", self.lexicon.import_file, corrupt)
        self.assertEqual(self.lexicon.manifest(), manifest)
        self.assertEqual(self.lexicon.lookup("blue hair")["targetText"], "blue")

    def test_expected_version_and_publish_cas(self):
        first = self.lexicon.import_file(self.fixture())
        second = self.fixture({"another": [("tag", 0, "second", 1)]})
        self.assert_error("VERSION_CONFLICT", self.lexicon.import_file,
                          second, expected_version="dict_" + "0" * 64)
        original = module._probe
        def concurrent_change(*args):
            result = original(*args)
            envelope = self.store.read_record("dictionary", "current")
            self.store.compare_write("dictionary", "current", envelope["revision"], envelope["record"])
            return result
        with patch.object(module, "_probe", side_effect=concurrent_change):
            self.assert_error("VERSION_CONFLICT", self.lexicon.import_file, second,
                              expected_version=first["dictionaryVersion"])
        self.assertEqual(self.lexicon.manifest(), first)

    def test_cancel_keeps_active_and_cleans_scratch(self):
        first = self.lexicon.import_file(self.fixture())
        event = threading.Event()
        event.set()
        self.assert_error("CANCELLED", self.lexicon.import_file, self.fixture(), cancel=event)
        self.assertEqual(self.lexicon.manifest(), first)
        original = module._probe
        def cancel_after_probe(*args):
            result = original(*args)
            event.set()
            return result
        event.clear()
        with patch.object(module, "_probe", side_effect=cancel_after_probe):
            self.assert_error("CANCELLED", self.lexicon.import_file,
                              self.fixture({"other": [("tag", 0, "x", 1)]}), cancel=event)
        self.assertEqual(self.lexicon.manifest(), first)

    def test_source_symlink_hardlink_and_fifo(self):
        source = self.fixture()
        linked = self.root / "linked.sqlite"
        linked.symlink_to(source)
        self.assert_error("UNSAFE_PATH", self.lexicon.import_file, linked)
        linked.unlink()
        os.link(source, linked)
        self.assert_error("UNSAFE_PATH", self.lexicon.import_file, source)
        linked.unlink()
        fifo = self.root / "pipe"
        os.mkfifo(fifo, 0o600)
        self.assert_error("UNSAFE_PATH", self.lexicon.import_file, fifo)
        self.assert_error("UNSAFE_PATH", self.lexicon.import_file, "relative.sqlite")

    def test_oversized_sparse_import_and_corrupt_manifest(self):
        large = self.root / "large.sqlite"
        with large.open("wb") as target:
            target.truncate(module.MAX_DATABASE + 1)
        self.assert_error("LIMIT_EXCEEDED", self.lexicon.import_file, large)
        self.lexicon.import_file(self.fixture())
        path = self.root / "state/annotations/dictionary/manifest.json"
        path.write_bytes(b"bad")
        self.assertEqual(self.lexicon.status()["status"], "error")
        self.assert_error("RECOVERY_REQUIRED", self.lexicon.import_file, self.fixture())

    def test_readonly_sqlite_configuration(self):
        path = self.fixture()
        with module._database(path) as db:
            self.assertEqual(db.execute("PRAGMA query_only").fetchone(), (1,))
            self.assertEqual(db.execute("PRAGMA trusted_schema").fetchone(), (0,))
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM real_table")
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("SELECT load_extension('invalid')")

    def test_invalid_download_urls_never_open_network(self):
        ref = "a" * 40
        sha = "b" * 64
        base = "https://raw.githubusercontent.com" + module.REPOSITORY_PATH
        invalid = [base + "/main/tag.sqlite", "https://evil.invalid/file",
                   base + "/" + ref + "/../tag.sqlite", base + "/" + ref + "/tag.sqlite?x=1",
                   base + "/" + ref + "/tag.sqlite#x", base + "/" + ref + "/%2e%2e/tag.sqlite",
                   "https://u:p@raw.githubusercontent.com" + module.REPOSITORY_PATH + "/" + ref + "/tag.sqlite"]
        with patch("urllib.request.build_opener", side_effect=AssertionError("network")):
            for url in invalid:
                self.assert_error("UNSAFE_PATH", self.lexicon.install, url, ref, sha)
            self.assert_error("INVALID_REQUEST", self.lexicon.install, base + "/main/tag.sqlite", "main", sha)

    def test_mocked_install_hash_no_proxy_and_rollback(self):
        source = self.fixture()
        content = source.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        ref = "a" * 40
        url = "https://raw.githubusercontent.com" + module.REPOSITORY_PATH + "/" + ref + "/tag.sqlite"
        class Response(io.BytesIO):
            status = 200
            headers = {"Content-Length": str(len(content))}
        class Opener:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return Response(content)
        opener = Opener()
        with patch("urllib.request.build_opener", return_value=opener) as factory:
            manifest = self.lexicon.install(url, ref, sha)
            handlers = factory.call_args.args
            self.assertEqual(handlers[0].proxies, {})
            self.assertIsInstance(handlers[1], module._NoRedirect)
            self.assertEqual(manifest["ref"], ref)
            self.assertEqual(opener.timeout, 60)
            self.assert_error("DICTIONARY_INVALID", self.lexicon.install, url, ref, "0" * 64)
        self.assertEqual(self.lexicon.manifest(), manifest)

    def test_redirect_and_timeout_rejected(self):
        handler = module._NoRedirect()
        self.assert_error("REMOTE_FAILED", handler.redirect_request,
                          None, None, 302, "", {}, "https://evil.invalid")
        ref = "a" * 40
        url = "https://raw.githubusercontent.com" + module.REPOSITORY_PATH + "/" + ref + "/tag.sqlite"
        with patch("urllib.request.OpenerDirector.open", side_effect=TimeoutError):
            self.assert_error("REMOTE_TIMEOUT", self.lexicon.install, url, ref, "b" * 64)
        import http.client
        with patch("urllib.request.OpenerDirector.open", side_effect=http.client.IncompleteRead(b"secret")):
            error = self.assert_error("REMOTE_FAILED", self.lexicon.install, url, ref, "b" * 64)
            self.assertNotIn("secret", str(error.payload))


if __name__ == "__main__":
    unittest.main()

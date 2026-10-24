"""D1-D4 regressions using private synthetic state and loopback fixtures only."""
import copy
import json
from pathlib import Path
import sqlite3
import time
import unittest
from unittest.mock import patch

import test_translation as base
from kohakumoss_ui.contract import translation_schema as schema
from kohakumoss_ui.contract.translation import TranslationService
from kohakumoss_ui.contract.lexicon import Lexicon, LexiconError, REPOSITORY_URL
from kohakumoss_ui.store.annotations import AnnotationStore

ident = base.ident


class RegressionTests(unittest.TestCase):
    setUp = base.TranslationTests.setUp
    tearDown = base.TranslationTests.tearDown
    post = base.TranslationTests.post
    get = base.TranslationTests.get
    configure = base.TranslationTests.configure
    plan = base.TranslationTests.plan
    start = base.TranslationTests.start
    finish = base.TranslationTests.finish
    manual = base.TranslationTests.manual

    def dictionary_fixture(self):
        path = Path(self.temp.name) / "two-tables.sqlite"
        with sqlite3.connect(path) as conn:
            for table in ("first", "second"):
                conn.execute("CREATE TABLE " + table + "(name TEXT, category INTEGER, cn_name TEXT, post_count INTEGER)")
                conn.execute("INSERT INTO " + table + " VALUES ('blue_hair', 0, ?, 1)", (table,))
        return path

    def reopen(self):
        self.service.close()
        self.service = TranslationService(self.contract)
        self.service.store = AnnotationStore(self.temp.name)
        self.service.lexicon = Lexicon(self.service.store)

    def test_dictionary_async_details_and_explicit_table_retry(self):
        path = self.dictionary_fixture()
        result = self.post("dictionary/import", expected=202, path=str(path))
        failed = self.finish(result["job"])
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["error"]["code"], "DICTIONARY_TABLE_REQUIRED")
        self.assertEqual(failed["error"]["details"], {"tables": ["first", "second"]})
        self.assertNotIn(str(path), json.dumps(failed))
        self.assertEqual(self.get("jobs/segments", jobId=failed["jobId"])["segments"], [])
        self.assertEqual(self.get("dictionary")["status"], "missing")
        imported = self.post("dictionary/import", expected=202, path=str(path), tableName="second")
        self.assertEqual(self.finish(imported["job"])["written"], 1)
        self.assertEqual(self.get("dictionary")["tableName"], "second")
        self.assertFalse(self.provider.requests)
        unsafe = LexiconError(422, "DICTIONARY_TABLE_REQUIRED", "SECRET", {"tables": ["first"], "apiKey": "SECRET"})
        self.assertEqual(self.service._exception_error(unsafe)["code"], "INTERNAL")
        self.assertNotIn("SECRET", json.dumps(self.service._exception_error(unsafe)))

    def test_dictionary_cache_and_old_plan_keep_pinned_version(self):
        path = self.dictionary_fixture()
        first = self.service.lexicon.import_file(path, "first")
        self.contract.doc = base.fixture(("blue hair",), "tags")
        self.finish(self.start(self.plan("dictionary")))
        cache = self.service._read("cache", "current")
        cache["entries"][0]["result"]["targetText"] = "legacy mutable override"
        self.service._write("cache", "current", cache)
        self.service.lexicon.set_override("blue hair", "new mutable override")
        self.assertEqual(self.plan("dictionary")["cacheHits"], 0)
        # Emulate an already-frozen plan from an older build with that cache.
        clean = copy.deepcopy(cache)
        clean["entries"][0]["result"]["targetText"] = "first"
        self.service._write("cache", "current", clean)
        old_plan = self.plan("dictionary")
        self.assertEqual(old_plan["cacheHits"], 1)
        record = self.service._read("translation-plans", old_plan["planId"])
        record["cached"][ident("seg")]["targetText"] = "legacy mutable override"
        self.service._write("translation-plans", old_plan["planId"], record)
        self.service.lexicon.import_file(path, "second")
        done = self.finish(self.start(old_plan))
        result = self.get("jobs/segments", jobId=done["jobId"])["segments"][0]
        self.assertEqual((result["targetText"], result["dictionaryVersion"]), ("first", first["dictionaryVersion"]))
        self.assertEqual(self.get("view", documentId=ident("doc"))["segments"][0]["staleReason"], "dictionary_changed")
        self.assertFalse(self.provider.requests)

    def test_history_manual_all_skip_preserves_active_language_and_revision(self):
        self.configure()
        self.manual(text="Chinese v1")
        chinese = self.manual(text="Chinese v2")["segment"]
        self.manual(text="French", targetLanguage="fr", switchLanguagePair=True)
        key = (ident("lib"), ident("doc"))
        before = self.service.store.read_record("translations", key)
        plan = self.plan(segmentIds=[ident("seg")])
        target = self.get("plans/segments", planId=plan["planId"])["segments"][0]
        self.assertEqual((target["status"], target["code"], target["expectedSegmentRevision"]), ("skip", "MANUAL_PROTECTED", 2))
        done = self.finish(self.start(plan))
        self.assertEqual((done["written"], done["skipped"], done["failed"]), (0, 1, 0))
        result = self.get("jobs/segments", jobId=done["jobId"])["segments"][0]
        self.assertEqual(result, {**chinese, "code": "MANUAL_PROTECTED"})
        self.assertEqual(self.service.store.read_record("translations", key), before)
        self.assertFalse(self.provider.requests)

    def test_corrupt_persisted_records_fail_closed_and_do_not_rewrite(self):
        self.configure()
        self.manual(text="config request preserved")
        plan = self.plan()
        done = self.finish(self.start(plan))
        key = (ident("lib"), ident("doc"))
        cases = [
            ("settings", "current", "settings", {}, lambda v: v.update(apiKey="SHOULD_NOT_LEAK")),
            ("translation-jobs", done["jobId"], "jobs", {"jobId": [done["jobId"]]}, lambda v: v["job"].pop("failed")),
            ("translation-plans", plan["planId"], "plans", {"planId": [plan["planId"]]}, lambda v: v["targets"][0]["segment"].update(extra="SHOULD_NOT_LEAK")),
            ("translations", key, "view", {"documentId": [ident("doc")]}, lambda v: v["segments"][0].update(segmentRevision=True)),
            ("cache", "current", None, {}, lambda v: v["entries"][0]["result"].update(apiKey="SHOULD_NOT_LEAK")),
        ]
        for kind, record_key, route, query, corrupt in cases:
            with self.subTest(kind=kind):
                saved = self.service.store.read_record(kind, record_key)
                broken = copy.deepcopy(saved)
                corrupt(broken["record"])
                location = self.service.store._location(kind, record_key)
                self.service.store._write_json(location, broken)
                if route:
                    status, payload = self.service.get(route, query)
                else:
                    status, payload = self.service.post("plans", dict(requestId=schema.uid("req"),
                        scope=dict(mode="page_all", snapshotId=ident("snap"), pageOffset=0), engine="api",
                        sourceLanguage="en", targetLanguage="zh-CN"))
                self.assertEqual(status, 409, payload)
                self.assertEqual(payload["error"]["code"], "RECOVERY_REQUIRED")
                self.assertNotIn("SHOULD_NOT_LEAK", json.dumps(payload))
                self.assertEqual(self.service.store.read_record(kind, record_key), broken)
                self.service.store._write_json(location, saved)

    def test_corrupt_history_and_wal_recovery_fail_closed(self):
        self.manual(text="First")
        self.manual(text="Second", targetLanguage="fr", switchLanguagePair=True)
        key = (ident("lib"), ident("doc"))
        saved = self.service.store.read_record("translations", key)
        broken = copy.deepcopy(saved)
        broken["record"]["history"][0]["segments"][0]["sourceLanguage"] = "fr"
        location = self.service.store._location("translations", key)
        self.service.store._write_json(location, broken)
        status, payload = self.service.get("view", {"documentId": [ident("doc")]})
        self.assertEqual((status, payload["error"]["code"]), (409, "RECOVERY_REQUIRED"))
        self.service.store._write_json(location, saved)
        wal = self.service.store.list_records("requests")[0]
        wal["record"]["receipt"]["data"]["segment"]["extra"] = "PRIVATE"
        rid = wal["record"]["requestId"]
        self.service.store._write_json(self.service.store._location("requests", rid), wal)
        self.reopen()
        status, payload = self.service.get("settings", {})
        self.assertEqual((status, payload["error"]["code"]), (409, "RECOVERY_REQUIRED"))
        self.assertEqual(self.service.store.read_record("translations", key), saved)
        self.assertFalse(self.provider.requests)

    def test_retention_tombstones_keep_job_receipt_and_never_resend(self):
        self.configure()
        plan = self.plan()
        request_id = ident("req", 990)
        accepted = self.start(plan, requestId=request_id)
        done = self.finish(accepted)
        self.manual(text="PERMANENT HUMAN")
        self.manual(text="PERMANENT FRENCH", targetLanguage="fr", switchLanguagePair=True)
        key = (ident("lib"), ident("doc"))
        saved = self.service.store.read_record("translations", key)
        recent = self.plan(segmentIds=[ident("seg", 2)])
        old_time = int(time.time()) - 31 * 86400
        record = self.service._read("translation-jobs", accepted["jobId"])
        record["job"].update(createdAt=old_time, updatedAt=old_time, finishedAt=old_time)
        self.service._write("translation-jobs", accepted["jobId"], record)
        for env in self.service.store.list_records("requests"):
            wal = env["record"]
            if wal["resourceId"] == recent["planId"]:
                continue
            wal["createdAt"] = old_time
            self.service._write("requests", wal["requestId"], wal)
        calls = len(self.provider.requests)
        self.reopen()
        self.get("settings")
        compact = self.service._read("translation-jobs", accepted["jobId"])
        self.assertEqual(set(compact), {"job", "tombstone"})
        self.assertEqual(compact["job"]["written"], done["written"])
        self.assertEqual(self.start(plan, requestId=request_id), accepted)
        self.assertEqual(len(self.provider.requests), calls)
        conflict = self.post("jobs/start", expected=409, requestId=request_id, planId=plan["planId"],
                             manifestHash=plan["manifestHash"], confirmRemote=True, confirmCost=False,
                             acceptMaximumOutputTokens=plan["maximumOutputTokens"])
        self.assertEqual(conflict["code"], "IDEMPOTENCY_CONFLICT")
        self.assertEqual(self.service.get("jobs/segments", {"jobId": [accepted["jobId"]]})[0], 404)
        self.assertEqual(self.service.store.read_record("translations", key), saved)
        for env in self.service.store.list_records("requests"):
            wal = env["record"]
            if wal["resourceId"] == recent["planId"]:
                self.assertNotIn("tombstone", wal)
                self.assertIn("effects", wal)
            else:
                self.assertTrue(wal["tombstone"])
                self.assertNotIn("effects", wal)
                self.assertNotIn("receipt", wal)
                self.assertNotIn("PERMANENT HUMAN", json.dumps(wal))
                self.assertNotIn("sourceText", json.dumps(wal))
        self.reopen()
        self.assertEqual(self.start(plan, requestId=request_id), accepted)
        self.assertEqual(len(self.provider.requests), calls)

    def test_metadata_tombstones_reconcile_same_id_without_reapplying(self):
        manual = dict(requestId=ident("req", 991), documentId=ident("doc"), expectedSourceVersion=ident("ver"),
                      expectedRevision=0, segmentId=ident("seg"), targetText="PERMANENT", sourceLanguage="en", targetLanguage="zh-CN")
        settings = dict(requestId=ident("req", 992), expectedConfigVersion=None, model="before")
        self.post("manual", **manual)
        saved = self.post("settings", **settings)
        credentials = dict(requestId=ident("req", 993), expectedConfigVersion=saved["configVersion"], apiKey="FAKE-ONLY-SECRET")
        self.post("credentials", **credentials)
        for body in (manual, settings, credentials):
            wal = self.service._read("requests", body["requestId"])
            wal["createdAt"] = int(time.time()) - 31 * 86400
            self.service._write("requests", body["requestId"], wal)
        record = self.service.store.read_record("translations", (ident("lib"), ident("doc")))
        self.reopen()
        for route, body, changed in (("manual", manual, {"targetText": "different"}),
                                     ("settings", settings, {"model": "different"}),
                                     ("credentials", credentials, {"apiKey": "different"})):
            self.assertEqual(self.post(route, expected=409, **body)["code"], "RECOVERY_REQUIRED")
            self.assertEqual(self.post(route, expected=409, **{**body, **changed})["code"], "IDEMPOTENCY_CONFLICT")
            tombstone = self.service._read("requests", body["requestId"])
            self.assertTrue(tombstone["tombstone"])
            self.assertEqual(tombstone["state"], "completed")
            self.assertNotIn("FAKE-ONLY-SECRET", json.dumps(tombstone))
        self.assertEqual(self.service.store.read_record("translations", (ident("lib"), ident("doc"))), record)
        self.assertFalse(self.provider.requests)

    def test_all_sixteen_response_schemas_reject_nested_extra_and_missing(self):
        captured = {}
        original = schema.validate_response
        def capture(method, route, status, payload):
            result = original(method, route, status, payload)
            captured[(method, route)] = (status, copy.deepcopy(payload))
            return result
        with patch.object(schema, "validate_response", side_effect=capture):
            self.configure()
            self.manual(text="config request ORIGINAL")
            self.manual(text="French", targetLanguage="fr", switchLanguagePair=True)
            self.get("view", documentId=ident("doc"))
            plan = self.plan(segmentIds=[ident("seg", 2)])
            self.get("plans", planId=plan["planId"])
            self.get("plans/segments", planId=plan["planId"])
            done = self.finish(self.start(plan))
            self.get("jobs/segments", jobId=done["jobId"])
            self.post("jobs/stop", jobId=done["jobId"])
            path = self.dictionary_fixture()
            self.finish(self.post("dictionary/import", expected=202, path=str(path), tableName="first")["job"])
            state = self.get("dictionary")
            body = dict(downloadUrl=REPOSITORY_URL + "/raw/" + "a" * 40 + "/tag.sqlite",
                        ref="a" * 40, expectedSha256="b" * 64, confirmNetwork=True, tableName="first")
            def install(*args, **kwargs):
                return self.service.lexicon.import_file(path, table_name=kwargs["table_name"],
                    expected_version=kwargs.get("expected_version"))
            with patch.object(self.service.lexicon, "install", side_effect=install):
                self.finish(self.post("dictionary/install", expected=202, **body)["job"])
                self.finish(self.post("dictionary/update", expected=202,
                    expectedDictionaryVersion=state["dictionaryVersion"], **body)["job"])
        self.assertEqual(set(captured), set(schema.RESPONSES))
        def locations(value, path=()):
            if isinstance(value, dict):
                yield path
                for key, child in value.items():
                    yield from locations(child, path + (key,))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    yield from locations(child, path + (index,))
        for (method, route), (status, payload) in captured.items():
            original_bytes = schema.canonical(payload)
            self.assertEqual(original(method, route, status, payload), (status, payload))
            self.assertEqual(schema.canonical(payload), original_bytes)
            for path in locations(payload):
                extra = copy.deepcopy(payload)
                node = extra
                for part in path:
                    node = node[part]
                node["unexpected"] = "PRIVATE"
                with self.subTest(route=route, path=path, kind="extra"):
                    with self.assertRaises(schema.TranslationError):
                        original(method, route, status, extra)
                source = payload
                for part in path:
                    source = source[part]
                for key in source:
                    missing = copy.deepcopy(payload)
                    node = missing
                    for part in path:
                        node = node[part]
                    del node[key]
                    with self.subTest(route=route, path=path, missing=key):
                        with self.assertRaises(schema.TranslationError):
                            original(method, route, status, missing)


if __name__ == "__main__":
    unittest.main()

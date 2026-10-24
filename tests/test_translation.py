"""Offline integration tests: real private temp store and fake loopback provider."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from kohakumoss_ui.contract import annotations as a
from kohakumoss_ui.contract import translation_schema as schema
from kohakumoss_ui.contract.translation import TranslationService
from kohakumoss_ui.store.annotations import AnnotationStore
from kohakumoss_ui.contract.lexicon import Lexicon


def ident(prefix, number=1):
    return prefix + "_" + format(number, "032x")


def fixture(texts=("A quiet room.", "A bright window."), kind="nl", structured=False):
    """Full production-shaped Document/Segment, never IDs from documentation."""
    value = (", " if kind == "tags" else "\n\n").join(texts)
    pointer = "/caption" if structured else ""
    raw = '{"caption":' + json.dumps(value) + ', "image":"PRIVATE.jpg", "score":1e+02, "meta":{"secret":"DO_NOT_SEND"}}' if structured else value
    digest = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
    field_start = raw.index('"' + value.split("\n")[0]) if structured else 0
    segments, offset = [], 0
    for index, text in enumerate(texts):
        # The fixture uses escaped JSON newlines; raw ranges refer to whole source.
        raw_start = raw.index(text) if structured else offset
        segments.append(dict(segmentId=ident("seg", index + 1), recordId=ident("rec"), pointer=pointer,
                             ordinal=index, kind=kind, text=text, sourceHash=digest, sourceVersion=ident("ver"), generation=1,
                             rawRanges=[dict(start=raw_start, end=raw_start + len(text))],
                             valueRange=dict(start=offset, end=offset + len(text)), elementIndex=None))
        offset += len(text) + 2
    spec = dict(format="json" if structured else "txt", kind=kind, delimiter="comma" if kind == "tags" else None,
                mapping=dict(mode="sidecar", recordsPointer="", recordIdPointer=None, imagePointer=None) if structured else dict(mode="sidecar"),
                fields=[dict(pointer=pointer, type="string")])
    field = dict(recordId=ident("rec"), pointer=pointer, type="string", kind=kind, delimiter=spec["delimiter"], value=value,
                 rawRange=dict(start=field_start, end=field_start + len(json.dumps(value)) if structured else len(raw)), segments=segments)
    return dict(libraryId=ident("lib"), documentId=ident("doc"), group="fixture", path="sample.json" if structured else "sample.txt",
                format=spec["format"], sourceHash=digest, sourceVersion=ident("ver"), generation=1, bytes=len(raw.encode()),
                bom=False, newline="none" if structured else "lf", finalNewline=False, rawText=raw,
                bindings=[dict(bindingId=ident("bind"), bindingRevision=1, itemIds=[ident("item")], recordIds=[ident("rec")], spec=spec, status="confirmed")],
                fields=[field], readOnly=False, readOnlyReason=None)


class Contract:
    def __init__(self, document):
        a.document(document)
        self.doc = document
        self.reads = []

    def document(self, document_id):
        self.reads.append(document_id)
        return copy.deepcopy(self.doc)

    def get(self, route, query):
        assert route == "snapshots"
        doc = self.doc
        candidate = dict(documentId=doc["documentId"], path=doc["path"], format=doc["format"], sourceHash=doc["sourceHash"],
                         sourceVersion=doc["sourceVersion"], bytes=doc["bytes"], bindingIds=[ident("bind")], suggestedKind=doc["fields"][0]["kind"], status="confirmed")
        item = dict(itemId=ident("item"), group="fixture", imagePath="sample.png", imageUrl="/files/fixture/sample.png",
                    candidates=[candidate], bindingId=ident("bind"), annotationStatus="confirmed")
        snap = dict(snapshotId=ident("snap"), libraryId=doc["libraryId"], group="fixture", filters=dict(format="any", status="any", search=""),
                    total=1, offset=int(query["offset"][0]), limit=60, items=[item], truncated=False, createdAt=int(time.time()), expiresAt=int(time.time()) + 900)
        a.snapshot(snap)
        return 200, {"ok": True, "data": snap}


class Provider:
    def __init__(self):
        self.requests = []
        self.mode = "ok"
        self.block = None
        self.entered = threading.Event()
        self.active = 0
        self.maximum = 0
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with owner.lock:
                    owner.requests.append(value)
                    owner.active += 1
                    owner.maximum = max(owner.maximum, owner.active)
                owner.entered.set()
                try:
                    if owner.block:
                        owner.block.wait(10)
                    data = json.loads(value["messages"][1]["content"])
                    rows = [{"id": seg["id"], "text": "Translated " + seg["text"]} for seg in data["segments"]]
                    response = {"segments": rows}
                    if owner.mode == "missing":
                        rows.pop()
                    elif owner.mode == "extra":
                        rows.append({"id": ident("seg", 900), "text": "extra"})
                    elif owner.mode == "duplicate":
                        rows[-1] = rows[0]
                    elif owner.mode == "field":
                        rows[0]["instruction"] = "ignored"
                    elif owner.mode == "outer":
                        response["instruction"] = "ignored"
                    elif owner.mode == "null":
                        rows[0]["text"] = None
                    elif owner.mode == "blank":
                        rows[0]["text"] = ""
                    elif owner.mode == "reverse":
                        rows.reverse()
                    elif owner.mode == "delay":
                        time.sleep(6)
                    if owner.mode == "429":
                        self.send_response(429)
                        self.end_headers()
                        self.wfile.write(b"secret provider diagnostic")
                        return
                    if owner.mode == "redirect":
                        self.send_response(302)
                        self.send_header("Location", "/not-allowed")
                        self.end_headers()
                        return
                    message = {"role": "assistant", "content": json.dumps(response)}
                    if owner.mode == "tools":
                        message["tool_calls"] = [{"function": {"name": "evil"}}]
                    encoded = json.dumps({"choices": [{"message": message}], "usage": {"total_tokens": 1}}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    try:
                        self.wfile.write(encoded)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                finally:
                    with owner.lock:
                        owner.active -= 1

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d/v1/chat/completions" % self.server.server_port

    def close(self):
        if self.block:
            self.block.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.contract = Contract(fixture())
        self.service = TranslationService(self.contract)
        self.service.store = AnnotationStore(self.temp.name)
        self.service.lexicon = Lexicon(self.service.store)
        self.provider = Provider()
        self.counter = 100

    def tearDown(self):
        if self.provider.block:
            self.provider.block.set()
        self.service.close()
        self.provider.close()
        self.temp.cleanup()

    def post(self, route, expected=200, **body):
        self.counter += 1
        body.setdefault("requestId", ident("req", self.counter))
        status, payload = self.service.post(route, body)
        self.assertEqual(status, expected, payload)
        return payload["data"] if expected < 400 else payload["error"]

    def get(self, route, **query):
        status, payload = self.service.get(route, {k: [str(v)] for k, v in query.items()})
        self.assertEqual(status, 200, payload)
        return payload["data"]

    def configure(self, **overrides):
        current = self.get("settings")
        saved = self.post("settings", expectedConfigVersion=current["configVersion"], endpoint=self.provider.url,
                          model="fake-only", enabled=True, **overrides)
        return self.post("credentials", expectedConfigVersion=saved["configVersion"], apiKey="")

    def plan(self, engine="api", **extra):
        body = dict(scope=dict(mode="page_all", snapshotId=ident("snap"), pageOffset=0), engine=engine,
                    sourceLanguage="en", targetLanguage="zh-CN")
        body.update(extra)
        return self.post("plans", expected=201, **body)

    def start(self, plan, **extra):
        body = dict(planId=plan["planId"], manifestHash=plan["manifestHash"], confirmRemote=plan["engine"] == "api",
                    confirmCost=plan["engine"] == "api", acceptMaximumOutputTokens=plan["maximumOutputTokens"])
        body.update(extra)
        return self.post("jobs/start", expected=202, **body)["job"]

    def finish(self, job):
        thread = self.service._threads.get(job["jobId"])
        if thread:
            thread.join(12)
            self.assertFalse(thread.is_alive())
        return self.get("jobs", jobId=job["jobId"])

    def manual(self, number=1, text="Human", **extra):
        view = self.get("view", documentId=ident("doc"))
        body = dict(documentId=ident("doc"), expectedSourceVersion=self.contract.doc["sourceVersion"],
                    expectedRevision=view["revision"], segmentId=ident("seg", number), targetText=text,
                    sourceLanguage="en", targetLanguage="zh-CN")
        body.update(extra)
        return self.post("manual", **body)

    def test_lazy_default_schema_and_credentials_hmac(self):
        with patch("kohakumoss_ui.contract.translation.AnnotationStore") as store:
            unused = TranslationService(self.contract)
            unused.close()
            store.return_value.acquire_owner.assert_not_called()
        self.assertIsNone(self.get("settings")["configVersion"])
        bad = [({"enabled": 1}, "INVALID_REQUEST"), ({"timeoutSeconds": True}, "INVALID_REQUEST"),
               ({"temperature": float("nan")}, "INVALID_REQUEST"), ({"endpoint": "http://example.com/v1"}, "INVALID_REQUEST"),
               ({"hasKey": True}, "INVALID_REQUEST"), ({"sourceLanguage": "not_a_lang"}, "INVALID_REQUEST")]
        for value, code in bad:
            self.assertEqual(self.post("settings", expected=400, expectedConfigVersion=None, **value)["code"], code)
        secret = "NEVER-LOG-THIS-KEY"
        result = self.post("credentials", expectedConfigVersion=None, apiKey=secret, requestId=ident("req", 700))
        self.assertEqual(result["keyHint"], "configured")
        again = self.post("credentials", expectedConfigVersion=None, apiKey=secret, requestId=ident("req", 700))
        self.assertEqual(result, again)
        unchanged = self.post("credentials", expectedConfigVersion=result["configVersion"], apiKey="")
        self.assertEqual(unchanged, result)
        self.assertNotIn(secret, json.dumps(self.get("settings")))
        for path in Path(self.temp.name).rglob("*.json"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            if path.name != "translation-credentials.json":
                self.assertNotIn(secret, path.read_text())
        wal = self.service._read("requests", ident("req", 700))
        self.assertTrue(wal["digest"].startswith("hmac-sha256:"))
        self.assertEqual(wal["state"], "completed")
        cleared = self.post("credentials", expectedConfigVersion=result["configVersion"], clear=True)
        self.assertFalse(cleared["hasKey"])
        self.assertNotEqual(cleared["configVersion"], result["configVersion"])

    def test_method_query_unknown_type_validation(self):
        for method, route, value, expected in [("GET", "credentials", {}, 405), ("GET", "unknown", {}, 404),
                                               ("GET", "settings", {"extra": ["1"]}, 400),
                                               ("GET", "view", {"documentId": [ident("doc"), ident("doc")]}, 400),
                                               ("GET", "plans", {"planId": ["plan_demo"]}, 400),
                                               ("POST", "manual", [], 400)]:
            self.assertEqual(self.service._call(method, route, value)[0], expected)

    def test_text_only_json_and_cache_and_same_job_receipt(self):
        self.contract = Contract(fixture(structured=True))
        self.service.annotations = self.contract
        original = copy.deepcopy(self.contract.doc)
        self.configure()
        plan = self.plan()
        self.assertFalse(self.provider.requests)
        job = self.start(plan, requestId=ident("req", 800))
        finished = self.finish(job)
        self.assertEqual((finished["status"], finished["written"]), ("done", 2))
        replay = self.start(plan, requestId=ident("req", 800))
        self.assertEqual(job, replay)
        self.assertEqual(len(self.provider.requests), 1)
        request = self.provider.requests[0]
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertFalse(request["stream"])
        self.assertNotIn("tools", request)
        sent = json.dumps(request)
        for hidden in ("PRIVATE.jpg", "DO_NOT_SEND", "sourceHash", "rawRange", "imageUrl", "file", "group"):
            self.assertNotIn(hidden, sent)
        self.assertEqual(self.contract.doc, original)
        cached = self.plan()
        self.assertEqual((cached["cacheHits"], cached["remoteBatches"], cached["maximumOutputTokens"]), (2, 0, 0))
        self.finish(self.start(cached))
        self.assertEqual(len(self.provider.requests), 1)
        cache = self.service._read("cache", "current")
        self.assertNotIn("segmentId", json.dumps(cache))
        self.manual(text="PRIVATE HUMAN")
        self.assertNotIn("PRIVATE HUMAN", json.dumps(self.service._read("cache", "current")))

    def test_provider_strict_ids_fields_tools(self):
        self.configure()
        for mode in ("missing", "extra", "duplicate", "field", "outer", "null", "blank", "tools"):
            self.provider.mode = mode
            plan = self.plan(replaceManual=True)
            job = self.finish(self.start(plan))
            self.assertEqual(job["failed"], 2, (mode, job))
            rows = self.get("jobs/segments", jobId=job["jobId"])["segments"]
            self.assertTrue(all(row["code"] == "TRANSLATION_PROTOCOL" and row["targetText"] == row["sourceText"] for row in rows), (mode, rows))
        self.provider.mode = "reverse"
        job = self.finish(self.start(self.plan()))
        self.assertEqual(job["written"], 2)

    def test_settings_cost_plan_consumption_and_expiry(self):
        self.configure()
        plan = self.plan()
        data = dict(planId=plan["planId"], manifestHash=plan["manifestHash"], confirmRemote=True, confirmCost=True,
                    acceptMaximumOutputTokens=plan["maximumOutputTokens"] + 1)
        self.post("jobs/start", expected=400, **data)
        saved = self.get("settings")
        self.post("settings", expectedConfigVersion=saved["configVersion"], model="changed")
        data["acceptMaximumOutputTokens"] -= 1
        self.assertEqual(self.post("jobs/start", expected=409, **data)["code"], "SETTINGS_CHANGED")
        self.assertFalse(self.provider.requests)
        fresh = self.plan()
        self.finish(self.start(fresh))
        data.update(planId=fresh["planId"], manifestHash=fresh["manifestHash"], acceptMaximumOutputTokens=fresh["maximumOutputTokens"])
        self.assertEqual(self.post("jobs/start", expected=409, **data)["code"], "PLAN_CONSUMED")
        expiring = self.plan()
        with patch("kohakumoss_ui.contract.translation_records.now", return_value=expiring["expiresAt"]):
            self.assertEqual(self.get("plans", planId=expiring["planId"])["status"], "expired")
            status, payload = self.service.get("plans/segments", {"planId": [expiring["planId"]]})
            self.assertEqual((status, payload["error"]["code"]), (410, "PLAN_EXPIRED"))

    def test_manual_same_second_conflict_and_unrelated_segment_merge(self):
        self.configure()
        # A third source segment remains automatic while segment one is edited.
        self.contract.doc = fixture(("First", "Second", "Third"))
        with patch("kohakumoss_ui.contract.translation_records.now", return_value=123456):
            first = self.manual(text="Human 1")
            second = self.manual(text="Human 2")
            self.assertEqual((first["segment"]["segmentRevision"], second["segment"]["segmentRevision"]), (1, 2))
            self.provider.block = threading.Event()
            plan = self.plan(replaceManual=True)
            job = self.start(plan)
            self.assertTrue(self.provider.entered.wait(3))
            latest = self.manual(text="Human 3")
            self.provider.block.set()
            done = self.finish(job)
            self.assertEqual((done["written"], done["skipped"]), (2, 1))
            rows = self.get("jobs/segments", jobId=job["jobId"])["segments"]
            protected = next(x for x in rows if x["segmentId"] == ident("seg"))
            self.assertEqual((protected["code"], protected["segmentRevision"]), ("MANUAL_CONFLICT", 3))
            self.assertEqual(latest["segment"]["targetText"], "Human 3")

    def test_cancel_no_late_results_and_global_job_limit(self):
        self.configure()
        self.contract.doc = fixture(tuple("Text %02d" % n for n in range(45)))
        self.provider.block = threading.Event()
        plan = self.plan()
        job = self.start(plan)
        self.assertTrue(self.provider.entered.wait(3))
        another = self.plan()
        self.post("jobs/start", expected=429, planId=another["planId"], manifestHash=another["manifestHash"],
                  confirmRemote=True, confirmCost=True, acceptMaximumOutputTokens=another["maximumOutputTokens"])
        self.post("jobs/stop", jobId=job["jobId"])
        self.provider.block.set()
        done = self.finish(job)
        self.assertEqual(done["status"], "stopped")
        self.assertLessEqual(self.provider.maximum, 2)
        self.assertLessEqual(len(self.provider.requests), 2)
        self.assertEqual(done["written"], 0)
        rows = self.get("jobs/segments", jobId=job["jobId"])["segments"]
        self.assertGreater(len(rows), 0)
        self.assertEqual(len(rows), done["skipped"])
        self.assertTrue(all(x["code"] == "CANCELLED" and x["segmentRevision"] == 0 and
                            x["targetText"] == x["sourceText"] for x in rows))
        self.assertEqual(self.get("view", documentId=ident("doc"))["segments"], [])

    def test_timeout_429_redirect_no_retry_and_errors_masked(self):
        self.configure(timeoutSeconds=5)
        for mode, code in (("429", "REMOTE_FAILED"), ("redirect", "REMOTE_FAILED"), ("delay", "REMOTE_TIMEOUT")):
            self.provider.mode = mode
            count = len(self.provider.requests)
            plan = self.plan()
            job = self.finish(self.start(plan))
            self.assertEqual(job["failed"], 2, job)
            self.assertEqual(len(self.provider.requests), count + 1)
            rows = self.get("jobs/segments", jobId=job["jobId"])["segments"]
            self.assertTrue(all(row["code"] == code for row in rows))
            self.assertNotIn("secret provider diagnostic", json.dumps(rows))

    def test_language_history_snapshot_and_generation_aba(self):
        first = self.manual(text="Chinese")
        french = self.manual(text="French", targetLanguage="fr", switchLanguagePair=True)
        chinese_view = self.get("view", documentId=ident("doc"), targetLanguage="zh-CN")
        self.assertEqual(chinese_view["revision"], french["revision"])
        self.assertEqual(chinese_view["snapshotRevision"], first["revision"])
        self.assertEqual(chinese_view["segments"][0]["targetText"], "Chinese")
        no_match = self.get("view", documentId=ident("doc"), targetLanguage="ja")
        self.assertEqual(no_match["segments"], [])
        self.assertIsNone(no_match["snapshotRevision"])
        self.assertEqual(no_match["historyCount"], 1)
        third = self.manual(text="Chinese again", switchLanguagePair=True)
        self.assertEqual(third["segment"]["segmentRevision"], 2)
        current = self.contract.doc
        current["sourceVersion"], current["generation"] = ident("ver", 3), 3
        for field in current["fields"]:
            for seg in field["segments"]:
                seg["sourceVersion"], seg["generation"] = ident("ver", 3), 3
                seg["segmentId"] = ident("seg", seg["ordinal"] + 101)
        stale = self.get("view", documentId=ident("doc"))
        self.assertEqual(stale["segments"][0]["staleReason"], "source_changed")
        self.assertEqual(stale["snapshotGeneration"], 1)
        self.assertEqual(stale["generation"], 3)
        self.assertEqual(stale["revision"], third["revision"])
        self.manual(number=101, text="New generation")
        self.assertEqual(self.get("view", documentId=ident("doc"))["historyCount"], 3)

    def test_dictionary_explicit_job_tags_only_missing_no_api(self):
        path = Path(self.temp.name) / "fixture.sqlite"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE words(name TEXT, category INTEGER, cn_name TEXT, post_count INTEGER)")
            conn.execute("INSERT INTO words VALUES ('blue_hair', 0, 'translated hair', 10)")
        result = self.post("dictionary/import", expected=202, path=str(path))
        self.assertEqual(self.finish(result["job"])["written"], 1)
        self.assertEqual(self.get("dictionary")["status"], "ready")
        self.contract.doc = fixture(("blue hair", "absent"), "tags")
        plan = self.plan("dictionary")
        done = self.finish(self.start(plan))
        self.assertEqual(done["written"], 2)
        rows = self.get("jobs/segments", jobId=done["jobId"])["segments"]
        self.assertEqual(rows[1]["code"], "MISSING_TRANSLATION")
        self.assertEqual(rows[1]["sourceText"], rows[1]["targetText"])
        self.assertFalse(self.provider.requests)
        self.contract.doc = fixture()
        blocked = self.plan("dictionary")
        self.assertEqual(blocked["status"], "blocked")

    def test_recovery_receipt_and_no_provider_replay(self):
        self.configure()
        plan = self.plan()
        # Simulate accepted job at crash: effects durable, receipt reserved.
        original_launch = self.service._launch
        self.service._launch = lambda *args: None
        job = self.start(plan, requestId=ident("req", 888))
        self.service._launch = original_launch
        wal = self.service._read("requests", ident("req", 888))
        wal.update(state="reserved", httpStatus=None, response=None)
        self.service._write("requests", ident("req", 888), wal)
        self.service.close()
        self.service = TranslationService(self.contract)
        self.service.store = AnnotationStore(self.temp.name)
        self.service.lexicon = Lexicon(self.service.store)
        recovered = self.get("jobs", jobId=job["jobId"])
        self.assertEqual(recovered["status"], "interrupted")
        self.assertFalse(self.provider.requests)
        replay = self.start(plan, requestId=ident("req", 888))
        self.assertEqual(replay, job)
        self.assertFalse(self.provider.requests)

    def test_source_change_during_request_no_attach(self):
        self.configure()
        self.provider.block = threading.Event()
        job = self.start(self.plan())
        self.assertTrue(self.provider.entered.wait(3))
        self.contract.doc["sourceVersion"] = ident("ver", 2)
        self.contract.doc["generation"] = 2
        for seg in self.contract.doc["fields"][0]["segments"]:
            seg.update(sourceVersion=ident("ver", 2), generation=2, segmentId=ident("seg", seg["ordinal"] + 201))
        self.provider.block.set()
        done = self.finish(job)
        self.assertEqual(done["skipped"], 2)
        rows = self.get("jobs/segments", jobId=job["jobId"])["segments"]
        self.assertTrue(all(x["code"] == "SOURCE_CHANGED" for x in rows))
        self.assertEqual(self.get("view", documentId=ident("doc"))["segments"], [])


if __name__ == "__main__":
    unittest.main()

"""End-to-end wire test: real ASG annotation HTTP server <-> UI contract.

Only temporary roots, loopback ephemeral ports and synthetic SQLite fixtures are
used. The UI bridge performs real HTTP and validates responses with the same
strict schema the production grabber uses. ASG never imports UI code.
"""

import copy
import http.client
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import urlencode

REPO = Path(__file__).resolve().parents[2]
ASG_DIR = REPO / "ArtistSourceGrabber"
if str(ASG_DIR) not in sys.path:
    sys.path.insert(0, str(ASG_DIR))

import annotations as asg_annotations  # noqa: E402
import app as asg_app  # noqa: E402

from kohakumoss_ui.contract import annotations as a  # noqa: E402
from kohakumoss_ui.contract import translation_schema as schema  # noqa: E402
from kohakumoss_ui.contract.translation import TranslationService  # noqa: E402
from kohakumoss_ui.store.annotations import StoreError  # noqa: E402


TERMINAL = {"done", "stopped", "error", "interrupted"}


def rid(number):
    return "req_%032x" % number


def sidecar_spec(fmt="txt", kind="nl", delimiter=None, pointer="", field_type="string"):
    if fmt in ("txt", "caption"):
        mapping = {"mode": "sidecar"}
    else:
        mapping = {"mode": "sidecar", "recordsPointer": "",
                   "recordIdPointer": None, "imagePointer": None}
    return {"format": fmt, "kind": kind, "delimiter": delimiter, "mapping": mapping,
            "fields": [{"pointer": pointer, "type": field_type}]}


def records_spec(fmt="jsonl", pointer="/caption", field_type="string", kind="nl", delimiter=None):
    return {"format": fmt, "kind": kind, "delimiter": delimiter,
            "mapping": {"mode": "records", "recordsPointer": "" if fmt == "jsonl" else "/records",
                        "recordIdPointer": "/id", "imagePointer": "/image"},
            "fields": [{"pointer": pointer, "type": field_type}]}


def tag_spec(fmt="txt", delimiter="comma"):
    return sidecar_spec(fmt=fmt, kind="tags", delimiter=delimiter)


class IsolatedServer:
    """ASG Handler dispatch with the module globals pointed at a temp root."""

    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self.service = asg_annotations.AnnotationService(str(root), self.lock, lambda: False)
        self.service.recover()
        self._old_annotations = asg_app.ANNOTATIONS
        self._old_download = asg_app.DOWNLOAD_DIR
        asg_app.ANNOTATIONS = self.service
        asg_app.DOWNLOAD_DIR = str(root)
        from http.server import ThreadingHTTPServer
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), asg_app.Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        try:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(5)
            self.service.close()
        finally:
            asg_app.ANNOTATIONS = self._old_annotations
            asg_app.DOWNLOAD_DIR = self._old_download


class HttpBridge:
    """Real HTTP bridge with the same return shape as Grabber.annotation_request."""

    def __init__(self, port):
        self.port = port

    def _exchange(self, path, method, data):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            connection.request(method, path, body=data,
                               headers={"Content-Type": "application/json",
                                        "Accept": "application/json"})
            response = connection.getresponse()
            status = response.status
            content_type = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
            length = response.getheader("Content-Length")
            if content_type != "application/json":
                raise a.AnnotationError(502, "UPSTREAM_PROTOCOL", "Invalid upstream response headers")
            if length is not None and (not re.fullmatch(r"[0-9]{1,10}", length)
                                       or int(length) > a.MAX_RESPONSE):
                raise a.AnnotationError(502, "UPSTREAM_PROTOCOL", "Invalid upstream response headers")
            raw = response.read(a.MAX_RESPONSE + 1)
        finally:
            connection.close()
        if len(raw) > a.MAX_RESPONSE or (length is not None and len(raw) != int(length)):
            raise a.AnnotationError(502, "UPSTREAM_PROTOCOL", "Invalid upstream response size")
        try:
            payload = a.strict_json(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise a.AnnotationError(502, "UPSTREAM_PROTOCOL", "Invalid upstream JSON response") from None
        if not isinstance(payload, dict):
            raise a.AnnotationError(502, "UPSTREAM_PROTOCOL", "Invalid upstream JSON response")
        return status, payload

    def annotation_request(self, method, route, query=None, body=None):
        params, clean_body = a.validate_request(method, route, query, body)
        status, metadata = self._exchange("/api/sources", "GET", None)
        capabilities = metadata.get("capabilities")
        if status != 200 or not isinstance(capabilities, dict) or capabilities.get("annotations_v1") is not True:
            raise a.AnnotationError(409, "UNSUPPORTED_CAPABILITY",
                                    "ASG annotations_v1 capability missing")
        data = None if clean_body is None else json.dumps(
            clean_body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        path = "/api/annotations/" + route + ("?" + urlencode(params) if params else "")
        status, payload = self._exchange(path, method, data)
        return a.validate_response(method, route, status, payload,
                                   params if method == "GET" else clean_body)


def write(path, raw):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(raw)
    os.chmod(path, 0o600)
    return path


def build_fixture(root):
    main = root / "main"
    main.mkdir(mode=0o700)
    write(main / "alpha.png", b"png-alpha")
    write(main / "alpha.txt", b"red, red, Red\r\n")
    write(main / "alpha.caption", b"\xef\xbb\xbfA quiet room.\r\n\r\nSecond line.\r\n")
    write(main / "alpha.json", b'\xef\xbb\xbf{ "caption" : "old caption", "tags":[ "x y", "x,y", "x y" ], "n":1e+02, "keep":true }')
    write(main / "beta.png", b"png-beta")
    write(main / "beta.caption", b"\xef\xbb\xbfOne line.\r\n\r\nTwo line.\r\n")
    write(main / "beta.txt", b"tagA tagB tagA")
    write(main / "solo.png", b"png-solo")
    write(main / "solo.json", b'\xef\xbb\xbf{ "caption" : "old caption", "tags":[ "x y", "x,y", "x y" ], "n":1e+02, "keep":true }')
    write(main / "gamma.png", b"png-gamma")
    write(main / "delta.png", b"png-delta")
    write(main / "shared.jsonl", b'\xef\xbb\xbf{"id":"1","image":"gamma.png","caption":"old","n":1e+02}\r\n\r\n{"id":"2","image":"delta.png","caption":"old","b":false}\n')
    write(main / "unknown.png", b"png-unknown")
    write(main / "unknown.json", b'{"prompt":"not a known annotation schema"}')
    write(main / "missing.png", b"png-missing")

    trans = root / "trans"
    trans.mkdir(mode=0o700)
    write(trans / "dict.png", b"png-dict")
    write(trans / "dict.txt", b"red, blue, Red, blue eyes, unknown_tag\n")
    write(trans / "note.png", b"png-note")
    write(trans / "note.txt", b"First line.\n\nSecond line.\n")

    batch = root / "batch"
    batch.mkdir(mode=0o700)
    for index in range(65):
        write(batch / ("b%02d.png" % index), ("png-%02d" % index).encode())
        if index < 60:
            write(batch / ("b%02d.txt" % index), b"one, dup, dup\n")
    return root


def make_sqlite(path, tables):
    connection = sqlite3.connect(str(path))
    try:
        for name, rows in tables.items():
            connection.execute('CREATE TABLE "%s" (name TEXT NOT NULL, category INTEGER, '
                               'cn_name TEXT NOT NULL, post_count INTEGER)' % name)
            connection.executemany('INSERT INTO "%s" VALUES (?, ?, ?, ?)' % name, rows)
        connection.commit()
    finally:
        connection.close()
    os.chmod(path, 0o600)
    return path


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="asg-ui-bridge-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        build_fixture(self.root)
        self.state = self.root / "ui-state"
        self.state.mkdir(mode=0o700)
        self._env = mock.patch.dict(os.environ, {"KOHAKUMOSS_UI_STATE": str(self.state)})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.server = IsolatedServer(self.root)
        self.addCleanup(self.server.close)
        self.contract = a.AnnotationContract(HttpBridge(self.server.port))
        self.translation = TranslationService(self.contract)
        self.addCleanup(self.translation.close)
        self.counter = 0

    def request_id(self):
        self.counter += 1
        return rid(self.counter)

    def post(self, route, body):
        return self.contract.post(route, body)

    def get(self, route, query):
        return self.contract.get(route, query)

    def tpost(self, route, body):
        return self.translation.post(route, body)

    def tget(self, route, query):
        return self.translation.get(route, query)

    def ok(self, result, status=None):
        code, payload = result
        self.assertTrue(payload.get("ok"), (code, payload))
        if status is not None:
            self.assertEqual(code, status, payload)
        return payload["data"]

    def error(self, result, status, code):
        actual_status, payload = result
        self.assertEqual(actual_status, status, payload)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], code, payload)
        return payload["error"]

    def snapshot(self, group="main", limit=60, filters=None):
        body = {"requestId": self.request_id(), "group": group, "limit": limit}
        if filters is not None:
            body["filters"] = filters
        return self.ok(self.post("snapshots", body), 201)

    def item(self, snapshot, image_path):
        return next(item for item in snapshot["items"] if item["imagePath"] == image_path)

    def candidates(self, snapshot, item, shared=None):
        query = {"snapshotId": [snapshot["snapshotId"]], "itemId": [item["itemId"]]}
        if shared is not None:
            query["sharedPath"] = [shared]
        return self.ok(self.get("candidates", query))["candidates"]

    def confirm(self, snapshot, image_path, candidate_path, spec, shared=None, record_ids=None):
        item = self.item(snapshot, image_path)
        candidate = next(c for c in self.candidates(snapshot, item, shared)
                         if c["path"] == candidate_path)
        body = {"requestId": self.request_id(), "snapshotId": snapshot["snapshotId"],
                "itemId": item["itemId"], "documentId": candidate["documentId"],
                "expectedVersion": candidate["sourceVersion"], "spec": spec}
        if record_ids is not None:
            body["recordIds"] = record_ids
        return self.ok(self.post("bindings/confirm", body), 200), item, candidate

    def select(self, snapshot, image_path, binding_id, revision):
        item = self.item(snapshot, image_path)
        data = self.ok(self.post("bindings/select", {
            "requestId": self.request_id(), "snapshotId": snapshot["snapshotId"],
            "itemId": item["itemId"], "bindingId": binding_id,
            "expectedBindingRevision": revision}), 200)
        self.assertEqual(data["bindingId"], binding_id)
        return data

    def document(self, document_id, expected=None):
        query = {"documentId": [document_id]}
        if expected is not None:
            query["expectedVersion"] = [expected]
        return self.ok(self.get("documents", query))

    def plan(self, snapshot, item_ids, operation, selections=None, missing="skip"):
        body = {"requestId": self.request_id(),
                "scope": {"mode": "page_selected", "snapshotId": snapshot["snapshotId"],
                          "pageOffset": 0, "itemIds": item_ids},
                "operation": operation, "missingPolicy": missing}
        if selections is not None:
            body["selections"] = selections
        return self.ok(self.post("plans", body), 201)

    def plan_all(self, snapshot, operation, selections=None, missing="skip"):
        body = {"requestId": self.request_id(),
                "scope": {"mode": "all_filtered", "snapshotId": snapshot["snapshotId"]},
                "operation": operation, "missingPolicy": missing}
        if selections is not None:
            body["selections"] = selections
        return self.ok(self.post("plans", body), 201)

    def rows(self, plan, offset=0, limit=60, include_diff=False):
        query = {"planId": [plan["planId"]], "offset": [str(offset)],
                 "limit": [str(limit)], "includeDiff": ["true" if include_diff else "false"]}
        return self.ok(self.get("plans/rows", query))

    def apply(self, plan):
        request = self.request_id()
        self.last_apply_request = request
        accepted = self.ok(self.post("plans/apply", {
            "requestId": request, "planId": plan["planId"],
            "manifestHash": plan["manifestHash"], "confirm": True}), 202)
        job_id = accepted["job"]["jobId"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = self.ok(self.get("jobs", {"jobId": [job_id]}))
            if job["status"] in TERMINAL:
                return job
            time.sleep(0.01)
        self.fail("annotation job did not finish")

    def tjob(self, job_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = self.ok(self.tget("jobs", {"jobId": [job_id]}))
            if job["status"] in TERMINAL:
                return job
            time.sleep(0.01)
        self.fail("translation job did not finish")

    def bytes(self, relative):
        return (self.root / relative).read_bytes()

    # a. -----------------------------------------------------------------
    def test_snapshots_candidates_explicit_binding_and_documents(self):
        snapshot = self.snapshot("main")
        self.assertEqual(snapshot["total"], 7)
        self.assertEqual([i["imagePath"] for i in snapshot["items"]],
                         ["alpha.png", "beta.png", "delta.png", "gamma.png",
                          "missing.png", "solo.png", "unknown.png"])
        alpha = self.item(snapshot, "alpha.png")
        self.assertEqual(alpha["annotationStatus"], "ambiguous")
        self.assertIsNone(alpha["bindingId"])
        candidates = self.candidates(snapshot, alpha)
        self.assertEqual([c["path"] for c in candidates],
                         ["alpha.caption", "alpha.json", "alpha.txt"])
        self.assertTrue(all(c["status"] == "unconfirmed" for c in candidates))
        # The broken JSON sidecar is listed and never auto-selected.
        self.assertEqual(self.item(snapshot, "missing.png")["annotationStatus"], "missing")

        tags, _, _ = self.confirm(snapshot, "alpha.png", "alpha.txt", tag_spec("txt", "comma"))
        self.assertEqual(tags["bindingRevision"], 1)
        document = tags["document"]
        self.assertFalse(document["readOnly"])
        self.assertEqual(document["fields"][0]["value"], "red, red, Red\r\n")
        self.assertEqual([s["ordinal"] for s in document["fields"][0]["segments"]], [0, 1, 2])
        self.assertEqual(document["fields"][0]["segments"][0]["text"], "red")
        self.assertEqual(document["newline"], "crlf")

        nl, _, _ = self.confirm(snapshot, "alpha.png", "alpha.caption",
                                sidecar_spec("caption", "nl"))
        self.assertTrue(nl["document"]["bom"])
        self.assertEqual(nl["document"]["rawText"], "A quiet room.\r\n\r\nSecond line.\r\n")
        snapshot2 = self.snapshot("main")
        selected = self.select(snapshot2, "alpha.png", tags["bindingId"], tags["bindingRevision"])
        self.assertEqual(selected["bindingId"], tags["bindingId"])
        snapshot3 = self.snapshot("main")
        self.assertEqual(self.item(snapshot3, "alpha.png")["bindingId"], tags["bindingId"])
        self.assertEqual(self.item(snapshot3, "alpha.png")["annotationStatus"], "confirmed")

        # A different group and both other scope modes stay readable.
        batch = self.snapshot("batch", limit=60)
        self.assertEqual(batch["total"], 65)
        self.assertEqual(len(batch["items"]), 60)
        page = self.ok(self.get("snapshots", {"snapshotId": [batch["snapshotId"]],
                                              "offset": ["60"]}))
        self.assertEqual(page["total"], 65)
        self.assertEqual(len(page["items"]), 5)
        page_all = self.ok(self.post("plans", {
            "requestId": self.request_id(),
            "scope": {"mode": "page_all", "snapshotId": batch["snapshotId"], "pageOffset": 0},
            "operation": {"op": "tag_dedupe"}}), 201)
        self.assertEqual(page_all["summary"]["images"], 60)
        filtered = self.ok(self.post("plans", {
            "requestId": self.request_id(),
            "scope": {"mode": "all_filtered", "snapshotId": batch["snapshotId"]},
            "operation": {"op": "tag_dedupe"}}), 201)
        self.assertEqual(filtered["summary"]["images"], 65)

    # b. tags -------------------------------------------------------------
    def test_tag_plan_apply_readback_undo_and_request_replay(self):
        snapshot = self.snapshot("main")
        confirmed, item, _ = self.confirm(snapshot, "alpha.png", "alpha.txt",
                                          tag_spec("txt", "comma"))
        document_id = confirmed["document"]["documentId"]
        binding = confirmed["document"]["bindings"][0]
        fresh = self.snapshot("main")
        item = self.item(fresh, "alpha.png")
        plan = self.plan(fresh, [item["itemId"]],
                         {"op": "tag_add", "tokens": ["green", "red"]},
                         [{"itemId": item["itemId"], "bindingId": binding["bindingId"],
                           "recordIds": binding["recordIds"], "pointers": [""]}])
        self.assertEqual(plan["summary"],
                         {"images": 1, "documents": 1, "rows": 1, "fields": 1,
                          "matchedOccurrences": 2, "changedOccurrences": 1,
                          "duplicateOccurrences": 1, "ready": 1, "skipped": 0, "errors": 0})
        row = self.rows(plan, include_diff=True)["rows"][0]
        self.assertEqual(row["diff"], {"before": "red, red, Red\r\n",
                                       "after": "red, red, Red, green\r\n"})
        job = self.apply(plan)
        self.assertEqual((job["status"], job["written"], job["processed"]), ("done", 1, 1))
        self.assertEqual(self.bytes("main/alpha.txt"), b"red, red, Red, green\r\n")
        read_back = self.document(document_id)
        self.assertNotEqual(read_back["sourceVersion"], confirmed["document"]["sourceVersion"])
        self.assertEqual(read_back["fields"][0]["value"], "red, red, Red, green\r\n")
        self.assertGreaterEqual(read_back["generation"], 2)

        # Replaying the same apply request returns the original job, not a new write.
        replay = self.ok(self.post("plans/apply", {
            "requestId": self.last_apply_request,
            "planId": plan["planId"], "manifestHash": plan["manifestHash"],
            "confirm": True}), 202)
        self.assertEqual(replay["job"]["jobId"], job["jobId"])
        self.assertEqual(self.bytes("main/alpha.txt"), b"red, red, Red, green\r\n")
        self.error(self.post("plans/apply", {
            "requestId": rid(901), "planId": plan["planId"],
            "manifestHash": plan["manifestHash"], "confirm": True}),
            409, "PLAN_CONSUMED")

        undo = self.ok(self.post("undo/plans", {"requestId": self.request_id(),
                                                "jobId": job["jobId"]}), 201)
        self.assertEqual(undo["kind"], "undo")
        self.assertIsNone(undo["scope"])
        undone = self.apply(undo)
        self.assertEqual(undone["written"], 1)
        self.assertEqual(self.bytes("main/alpha.txt"), b"red, red, Red\r\n")
        self.assertEqual(self.document(document_id)["fields"][0]["value"], "red, red, Red\r\n")

        # The same single-item flow with a space-delimited tag sidecar.
        snapshot = self.snapshot("main")
        spaced, _, _ = self.confirm(snapshot, "beta.png", "beta.txt",
                                    tag_spec("txt", "space"))
        spaced_id = spaced["document"]["documentId"]
        fresh = self.snapshot("main")
        item = self.item(fresh, "beta.png")
        binding = next(b for b in self.document(spaced_id)["bindings"]
                       if b["bindingId"] == item["bindingId"])
        plan = self.plan(fresh, [item["itemId"]],
                         {"op": "tag_add", "tokens": ["tagC"]},
                         [{"itemId": item["itemId"], "bindingId": binding["bindingId"],
                           "recordIds": binding["recordIds"], "pointers": [""]}])
        self.assertEqual(plan["summary"]["changedOccurrences"], 1)
        job = self.apply(plan)
        self.assertEqual(job["written"], 1)
        self.assertEqual(self.bytes("main/beta.txt"), b"tagA tagB tagA tagC")
        self.assertEqual(self.document(spaced_id)["fields"][0]["value"],
                         "tagA tagB tagA tagC")
        undo = self.ok(self.post("undo/plans", {"requestId": self.request_id(),
                                                "jobId": job["jobId"]}), 201)
        self.assertEqual(self.apply(undo)["written"], 1)
        self.assertEqual(self.bytes("main/beta.txt"), b"tagA tagB tagA")

    # b. natural language ------------------------------------------------
    def test_nl_prepend_append_literal_and_clear_preserve_bom_crlf(self):
        snapshot = self.snapshot("main")
        text, _, _ = self.confirm(snapshot, "alpha.png", "alpha.txt",
                                  sidecar_spec("txt", "nl"))
        text_id = text["document"]["documentId"]
        fresh = self.snapshot("main")
        item = self.item(fresh, "alpha.png")
        binding = next(b for b in self.document(text_id)["bindings"]
                       if b["bindingId"] == item["bindingId"])
        plan = self.plan(fresh, [item["itemId"]], {"op": "nl_prepend", "text": "Note"},
                         [{"itemId": item["itemId"], "bindingId": binding["bindingId"],
                           "recordIds": binding["recordIds"], "pointers": [""]}])
        self.assertEqual(self.apply(plan)["written"], 1)
        self.assertEqual(self.bytes("main/alpha.txt"), b"Note\r\nred, red, Red\r\n")
        self.assertEqual(self.document(text_id)["fields"][0]["value"],
                         "Note\r\nred, red, Red\r\n")

        confirmed, _, _ = self.confirm(snapshot, "beta.png", "beta.caption",
                                       sidecar_spec("caption", "nl"))
        document_id = confirmed["document"]["documentId"]
        original = b"\xef\xbb\xbfOne line.\r\n\r\nTwo line.\r\n"

        def edit(operation, expected):
            fresh = self.snapshot("main")
            item = self.item(fresh, "beta.png")
            binding = next(b for b in self.document(document_id)["bindings"]
                           if b["bindingId"] == item["bindingId"])
            plan = self.plan(fresh, [item["itemId"]], operation,
                             [{"itemId": item["itemId"], "bindingId": binding["bindingId"],
                               "recordIds": binding["recordIds"], "pointers": [""]}])
            job = self.apply(plan)
            self.assertEqual(job["written"], 1, job)
            self.assertEqual(self.bytes("main/beta.caption"), expected)
            return plan

        edit({"op": "nl_prepend", "text": "Intro"},
             b"\xef\xbb\xbfIntro\r\nOne line.\r\n\r\nTwo line.\r\n")
        edit({"op": "nl_append", "text": "End", "separator": " "},
             b"\xef\xbb\xbfIntro\r\nOne line.\r\n\r\nTwo line.\r\n End")
        edit({"op": "nl_replace", "match": "line", "replacement": "row"},
             b"\xef\xbb\xbfIntro\r\nOne row.\r\n\r\nTwo row.\r\n End")
        edit({"op": "clear"}, b"\xef\xbb\xbf")
        document = self.document(document_id)
        self.assertEqual(document["fields"][0]["value"], "")
        self.assertEqual(document["fields"][0]["segments"], [])
        self.assertTrue(document["bom"])
        self.assertEqual(document["bytes"], 3)

    # b. JSON -------------------------------------------------------------
    def test_json_string_array_and_string_edit_only_selected_bytes(self):
        snapshot = self.snapshot("main")
        tags, _, _ = self.confirm(snapshot, "solo.png", "solo.json",
                                  tag_spec("json", None) | {"fields": [{"pointer": "/tags", "type": "string_array"}]})
        document_id = tags["document"]["documentId"]
        caption, _, _ = self.confirm(snapshot, "solo.png", "solo.json",
                                     sidecar_spec("json", "nl", pointer="/caption"))
        # Mixed semantics on one record need two distinct bindings.
        self.assertNotEqual(tags["bindingId"], caption["bindingId"])
        current = self.document(document_id)
        self.assertEqual(len(current["bindings"]), 2)
        self.assertEqual({f["pointer"] for f in current["fields"]}, {"/caption", "/tags"})
        self.assertEqual({f["recordId"] for f in current["fields"]},
                         {current["fields"][0]["recordId"]})
        self.assertEqual(len({s["segmentId"] for f in current["fields"] for s in f["segments"]}), 4)

        original = self.bytes("main/solo.json")
        fresh = self.snapshot("main")
        item = self.item(fresh, "solo.png")
        raw_tags = self.document(document_id)
        tags_binding = next(b for b in raw_tags["bindings"] if b["bindingId"] == tags["bindingId"])
        self.select(fresh, "solo.png", tags_binding["bindingId"], tags_binding["bindingRevision"])
        fresh = self.snapshot("main")
        item = self.item(fresh, "solo.png")
        tags_binding = next(b for b in self.document(document_id)["bindings"]
                            if b["bindingId"] == tags["bindingId"])
        plan = self.plan(fresh, [item["itemId"]],
                         {"op": "tag_set", "tokens": ["a", "b", "a"]},
                         [{"itemId": item["itemId"], "bindingId": tags_binding["bindingId"],
                           "recordIds": tags_binding["recordIds"], "pointers": ["/tags"]}])
        self.assertEqual(self.apply(plan)["written"], 1)
        after_tags = original.replace(b'[ "x y", "x,y", "x y" ]', b'["a","b","a"]')
        self.assertEqual(self.bytes("main/solo.json"), after_tags)
        document = self.document(document_id)
        field = next(f for f in document["fields"] if f["pointer"] == "/tags")
        self.assertEqual(field["value"], ["a", "b", "a"])
        self.assertEqual([(s["ordinal"], s["elementIndex"], s["text"]) for s in field["segments"]],
                         [(0, 0, "a"), (1, 1, "b"), (2, 2, "a")])
        self.assertIn(b'"n":1e+02', self.bytes("main/solo.json"))
        self.assertIn(b'"keep":true', self.bytes("main/solo.json"))

        fresh = self.snapshot("main")
        caption_binding = next(b for b in self.document(document_id)["bindings"]
                               if b["bindingId"] == caption["bindingId"])
        self.select(fresh, "solo.png", caption_binding["bindingId"], caption_binding["bindingRevision"])
        fresh = self.snapshot("main")
        item = self.item(fresh, "solo.png")
        caption_binding = next(b for b in self.document(document_id)["bindings"]
                               if b["bindingId"] == caption["bindingId"])
        plan = self.plan(fresh, [item["itemId"]],
                         {"op": "nl_replace", "match": "old", "replacement": "new"},
                         [{"itemId": item["itemId"], "bindingId": caption_binding["bindingId"],
                           "recordIds": caption_binding["recordIds"], "pointers": ["/caption"]}])
        self.assertEqual(self.apply(plan)["written"], 1)
        self.assertEqual(self.bytes("main/solo.json"),
                         after_tags.replace(b'"old caption"', b'"new caption"'))

    # b. shared JSONL -----------------------------------------------------
    def test_shared_jsonl_two_images_one_physical_write_and_undo(self):
        snapshot = self.snapshot("main")
        first, _, _ = self.confirm(snapshot, "gamma.png", "shared.jsonl",
                                   records_spec("jsonl"), shared="shared.jsonl")
        second, _, _ = self.confirm(snapshot, "delta.png", "shared.jsonl",
                                    records_spec("jsonl"), shared="shared.jsonl")
        self.assertEqual(first["document"]["documentId"], second["document"]["documentId"])
        documented = self.document(first["document"]["documentId"])
        self.assertEqual(len(documented["bindings"]), 2)

        fresh = self.snapshot("main")
        gamma = self.item(fresh, "gamma.png")
        delta = self.item(fresh, "delta.png")
        gamma_binding = next(b for b in documented["bindings"] if b["bindingId"] == gamma["bindingId"])
        delta_binding = next(b for b in documented["bindings"] if b["bindingId"] == delta["bindingId"])
        selections = [
            {"itemId": gamma["itemId"], "bindingId": gamma_binding["bindingId"],
             "recordIds": gamma_binding["recordIds"], "pointers": ["/caption"]},
            {"itemId": delta["itemId"], "bindingId": delta_binding["bindingId"],
             "recordIds": delta_binding["recordIds"], "pointers": ["/caption"]},
        ]
        plan = self.plan(fresh, [gamma["itemId"], delta["itemId"]],
                         {"op": "nl_replace", "match": "old", "replacement": "new"},
                         selections)
        self.assertEqual(plan["summary"],
                         {"images": 2, "documents": 1, "rows": 1, "fields": 2,
                          "matchedOccurrences": 2, "changedOccurrences": 2,
                          "duplicateOccurrences": 0, "ready": 1, "skipped": 0, "errors": 0})
        original = self.bytes("main/shared.jsonl")
        job = self.apply(plan)
        self.assertEqual((job["written"], job["processed"]), (1, 1))
        self.assertEqual(self.bytes("main/shared.jsonl"),
                         original.replace(b'"old"', b'"new"'))
        self.assertIn(b'\r\n\r\n', self.bytes("main/shared.jsonl"))
        self.assertIn(b'"n":1e+02', self.bytes("main/shared.jsonl"))
        self.assertIn(b'"b":false', self.bytes("main/shared.jsonl"))
        backup = self.root / asg_annotations.STATE / "backups" / job["jobId"]
        self.assertEqual(len(list(backup.iterdir())), 1)
        self.assertEqual(next(backup.iterdir()).read_bytes(), original)

        undo = self.ok(self.post("undo/plans", {"requestId": self.request_id(),
                                                "jobId": job["jobId"]}), 201)
        self.assertEqual(self.apply(undo)["written"], 1)
        self.assertEqual(self.bytes("main/shared.jsonl"), original)

    # c. deletion ---------------------------------------------------------
    def test_delete_only_annotation_file_and_clear_keeps_image(self):
        snapshot = self.snapshot("main")
        confirmed, _, _ = self.confirm(snapshot, "beta.png", "beta.txt",
                                       tag_spec("txt", "space"))
        document_id = confirmed["document"]["documentId"]
        fresh = self.snapshot("main")
        item = self.item(fresh, "beta.png")
        binding = next(b for b in self.document(document_id)["bindings"]
                       if b["bindingId"] == item["bindingId"])
        plan = self.plan(fresh, [item["itemId"]],
                         {"op": "delete_file", "confirmDelete": True},
                         [{"itemId": item["itemId"], "bindingId": binding["bindingId"],
                           "recordIds": binding["recordIds"], "pointers": [""]}])
        self.assertEqual(plan["summary"]["ready"], 1)
        job = self.apply(plan)
        self.assertEqual(job["written"], 1)
        self.assertFalse((self.root / "main/beta.txt").exists())
        image = (self.root / "main/beta.png").stat()
        self.assertTrue(stat.S_ISREG(image.st_mode))
        self.assertEqual(self.bytes("main/beta.png"), b"png-beta")

        # clear keeps the physical file as well.
        snapshot = self.snapshot("main")
        confirmed, _, _ = self.confirm(snapshot, "alpha.png", "alpha.txt",
                                       tag_spec("txt", "comma"))
        fresh = self.snapshot("main")
        item = self.item(fresh, "alpha.png")
        binding = next(b for b in self.document(confirmed["document"]["documentId"])["bindings"]
                       if b["bindingId"] == item["bindingId"])
        plan = self.plan(fresh, [item["itemId"]], {"op": "clear"},
                         [{"itemId": item["itemId"], "bindingId": binding["bindingId"],
                           "recordIds": binding["recordIds"], "pointers": [""]}])
        self.assertEqual(self.apply(plan)["written"], 1)
        self.assertEqual(self.bytes("main/alpha.txt"), b"")
        self.assertTrue(stat.S_ISREG((self.root / "main/alpha.png").stat().st_mode))

    # d. unknown schema ---------------------------------------------------
    def test_unknown_schema_is_blocked_and_never_treated_as_tags(self):
        snapshot = self.snapshot("main")
        item = self.item(snapshot, "unknown.png")
        self.assertIsNone(item["bindingId"])
        self.assertEqual(item["annotationStatus"], "unconfirmed")
        candidate = self.candidates(snapshot, item)[0]
        document = self.document(candidate["documentId"])
        self.assertTrue(document["readOnly"])
        self.assertEqual(document["readOnlyReason"], "SCHEMA_REQUIRED")
        self.assertEqual(document["fields"], [])

        original = self.bytes("main/unknown.json")
        plan = self.plan(snapshot, [item["itemId"]], {"op": "clear"})
        self.assertEqual((plan["status"], plan["summary"]["errors"]), ("blocked", 1))
        row = self.rows(plan)["rows"][0]
        self.assertEqual((row["status"], row["code"]), ("error", "SCHEMA_REQUIRED"))
        self.error(self.post("plans/apply", {
            "requestId": self.request_id(), "planId": plan["planId"],
            "manifestHash": plan["manifestHash"], "confirm": True}),
            422, "INVALID_OPERATION")
        self.assertEqual(self.bytes("main/unknown.json"), original)

        # A structurally wrong records mapping is rejected, not guessed.
        self.error(self.post("bindings/confirm", {
            "requestId": self.request_id(), "snapshotId": snapshot["snapshotId"],
            "itemId": item["itemId"], "documentId": candidate["documentId"],
            "expectedVersion": candidate["sourceVersion"],
            "spec": records_spec("json", pointer="/tags", field_type="string_array",
                                 kind="tags")}), 422, "SCHEMA_MISMATCH")
        self.assertEqual(self.bytes("main/unknown.json"), original)

    # e. external changes -------------------------------------------------
    def test_external_changes_skip_and_never_overwrite_manual_edits(self):
        snapshot = self.snapshot("main")
        confirmed, _, _ = self.confirm(snapshot, "alpha.png", "alpha.txt",
                                       tag_spec("txt", "comma"))
        document_id = confirmed["document"]["documentId"]
        fresh = self.snapshot("main")
        item = self.item(fresh, "alpha.png")
        binding = next(b for b in self.document(document_id)["bindings"]
                       if b["bindingId"] == item["bindingId"])
        plan = self.plan(fresh, [item["itemId"]],
                         {"op": "tag_add", "tokens": ["green"]},
                         [{"itemId": item["itemId"], "bindingId": binding["bindingId"],
                           "recordIds": binding["recordIds"], "pointers": [""]}])
        # mtime-only change is already a conflict; bytes must not move.
        alpha_path = self.root / "main/alpha.txt"
        original_alpha = alpha_path.read_bytes()
        info = alpha_path.stat()
        os.utime(alpha_path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
        job = self.apply(plan)
        self.assertEqual((job["written"], job["skipped"], job["failed"]), (0, 1, 0))
        job_rows = self.ok(self.get("jobs/rows", {"jobId": [job["jobId"]]}))["rows"]
        self.assertEqual(job_rows[0]["code"], "VERSION_CONFLICT")
        self.assertEqual(self.bytes("main/alpha.txt"), original_alpha)
        write(self.root / "main/alpha.txt", b"manual, edit\r\n")

        # A new plan sees the stale binding and refuses at plan time.
        blocked_snapshot = self.snapshot("main")
        blocked_item = self.item(blocked_snapshot, "alpha.png")
        self.assertEqual(blocked_item["annotationStatus"], "stale")
        blocked = self.plan(blocked_snapshot, [blocked_item["itemId"]],
                            {"op": "tag_add", "tokens": ["x"]})
        self.assertEqual(blocked["status"], "blocked")
        row = self.rows(blocked)["rows"][0]
        self.assertEqual((row["status"], row["code"]), ("error", "BINDING_STALE"))
        self.error(self.post("plans/apply", {
            "requestId": self.request_id(), "planId": blocked["planId"],
            "manifestHash": blocked["manifestHash"], "confirm": True}),
            422, "INVALID_OPERATION")
        self.assertEqual(self.bytes("main/alpha.txt"), b"manual, edit\r\n")

        # A later manual edit also defeats undo of an earlier successful job.
        write(self.root / "main/edit.png", b"png-edit")
        write(self.root / "main/edit.txt", b"one, two\r\n")
        snapshot = self.snapshot("main")
        confirmed, _, _ = self.confirm(snapshot, "edit.png", "edit.txt",
                                       tag_spec("txt", "comma"))
        edit_id = confirmed["document"]["documentId"]
        fresh = self.snapshot("main")
        item = self.item(fresh, "edit.png")
        binding = next(b for b in self.document(edit_id)["bindings"]
                       if b["bindingId"] == item["bindingId"])
        plan = self.plan(fresh, [item["itemId"]],
                         {"op": "tag_add", "tokens": ["three"]},
                         [{"itemId": item["itemId"], "bindingId": binding["bindingId"],
                           "recordIds": binding["recordIds"], "pointers": [""]}])
        job = self.apply(plan)
        self.assertEqual(job["written"], 1)
        self.assertEqual(self.bytes("main/edit.txt"), b"one, two, three\r\n")
        write(self.root / "main/edit.txt", b"human, keep\r\n")
        undo = self.ok(self.post("undo/plans", {"requestId": self.request_id(),
                                                "jobId": job["jobId"]}), 201)
        self.assertEqual((undo["status"], undo["summary"]["skipped"]), ("ready", 1))
        job = self.apply(undo)
        self.assertEqual((job["written"], job["skipped"]), (0, 1))
        self.assertEqual(self.bytes("main/edit.txt"), b"human, keep\r\n")

    # f. summary, pagination, idempotency, TTL ----------------------------
    def test_batch_summary_rows_pagination_idempotency_and_ttl(self):
        snapshot = self.snapshot("main")
        alpha, _, _ = self.confirm(snapshot, "alpha.png", "alpha.txt",
                                   tag_spec("txt", "comma"))
        beta, _, _ = self.confirm(snapshot, "beta.png", "beta.caption",
                                  sidecar_spec("caption", "nl"))
        solo_tags, _, _ = self.confirm(
            snapshot, "solo.png", "solo.json",
            tag_spec("json", None) | {"fields": [{"pointer": "/tags",
                                                  "type": "string_array"}]})
        self.confirm(snapshot, "solo.png", "solo.json",
                     sidecar_spec("json", "nl", pointer="/caption"))
        self.confirm(snapshot, "gamma.png", "shared.jsonl", records_spec("jsonl"),
                     shared="shared.jsonl")
        self.confirm(snapshot, "delta.png", "shared.jsonl", records_spec("jsonl"),
                     shared="shared.jsonl")
        solo_doc = self.document(solo_tags["document"]["documentId"])
        fresh = self.snapshot("main")
        solo_item = self.item(fresh, "solo.png")
        tags_binding = next(b for b in solo_doc["bindings"] if b["bindingId"] == solo_tags["bindingId"])
        self.select(fresh, "solo.png", tags_binding["bindingId"], tags_binding["bindingRevision"])

        fresh = self.snapshot("main")
        plan = self.plan_all(fresh, {"op": "tag_dedupe"})
        self.assertEqual(plan["summary"],
                         {"images": 7, "documents": 4, "rows": 6, "fields": 2,
                          "matchedOccurrences": 2, "changedOccurrences": 2,
                          "duplicateOccurrences": 2, "ready": 2, "skipped": 1, "errors": 3})
        self.assertEqual(plan["status"], "blocked")
        rows = self.rows(plan)["rows"]
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(row["diff"] is None for row in rows))
        by_path = {}
        for row in rows:
            by_path.setdefault(row["path"], []).append(row["code"])
        self.assertEqual(by_path["alpha.txt"], [None])
        self.assertEqual(by_path["beta.caption"], ["INVALID_OPERATION"])
        self.assertEqual(by_path["shared.jsonl"], ["INVALID_OPERATION"])
        self.assertEqual(by_path["solo.json"], [None])
        self.assertNotIn("unknown.json", by_path)
        self.assertEqual(sorted(by_path[None]), ["MISSING_ANNOTATION", "SCHEMA_REQUIRED"])
        self.error(self.post("plans/apply", {
            "requestId": self.request_id(), "planId": plan["planId"],
            "manifestHash": plan["manifestHash"], "confirm": True}),
            422, "INVALID_OPERATION")

        # An unchanged token is a NO_CHANGE skip row, and an all-skip job is legal.
        noop_snapshot = self.snapshot("main")
        noop_item = self.item(noop_snapshot, "alpha.png")
        noop_binding = next(b for b in self.document(alpha["document"]["documentId"])["bindings"]
                            if b["bindingId"] == noop_item["bindingId"])
        noop = self.plan(noop_snapshot, [noop_item["itemId"]],
                         {"op": "tag_add", "tokens": ["red"]},
                         [{"itemId": noop_item["itemId"],
                           "bindingId": noop_binding["bindingId"],
                           "recordIds": noop_binding["recordIds"], "pointers": [""]}])
        self.assertEqual((noop["status"], noop["summary"]["ready"],
                          noop["summary"]["skipped"], noop["summary"]["errors"]),
                         ("ready", 0, 1, 0))
        noop_row = self.rows(noop)["rows"][0]
        self.assertEqual((noop_row["action"], noop_row["status"], noop_row["code"]),
                         ("noop", "skip", "NO_CHANGE"))
        before = self.bytes("main/alpha.txt")
        noop_job = self.apply(noop)
        self.assertEqual((noop_job["written"], noop_job["skipped"], noop_job["processed"]),
                         (0, 1, 1))
        self.assertEqual(self.bytes("main/alpha.txt"), before)

        # 65-row plan: 60 unconfirmed documents error, 5 missing images skip.
        batch = self.snapshot("batch", limit=60)
        body = {"requestId": rid(500),
                "scope": {"mode": "all_filtered", "snapshotId": batch["snapshotId"]},
                "operation": {"op": "tag_dedupe"}}
        first = self.ok(self.post("plans", body), 201)
        self.assertEqual(first["summary"]["rows"], 65)
        self.assertEqual((first["summary"]["errors"], first["summary"]["skipped"],
                          first["summary"]["ready"]), (60, 5, 0))
        replay = self.ok(self.post("plans", body), 201)
        self.assertEqual(replay["planId"], first["planId"])
        self.error(self.post("plans", {**body, "operation": {"op": "clear"}}),
                   409, "IDEMPOTENCY_CONFLICT")
        page = self.rows(first, offset=60, limit=10)
        self.assertEqual((page["total"], page["offset"], len(page["rows"])), (65, 60, 5))
        self.assertTrue(all(row["diff"] is None for row in page["rows"]))
        full = self.rows(first, offset=0, limit=100)
        self.assertEqual(len(full["rows"]), 65)

        expiring = self.snapshot("batch")
        with mock.patch.object(asg_annotations, "now", return_value=expiring["expiresAt"] + 1):
            self.error(self.get("snapshots", {"snapshotId": [expiring["snapshotId"]]}),
                       410, "SNAPSHOT_EXPIRED")
        with mock.patch.object(asg_annotations.AnnotationService, "PLAN_BUDGET_SECONDS", 0):
            self.error(self.post("plans", body), 413, "LIMIT_EXCEEDED")
        with mock.patch.object(asg_annotations, "now", return_value=first["expiresAt"] + 1):
            expired = self.ok(self.get("plans", {"planId": [first["planId"]]}))
            self.assertEqual(expired["status"], "expired")
            self.error(self.get("plans/rows", {"planId": [first["planId"]]}),
                       410, "PLAN_EXPIRED")
            self.error(self.post("plans/apply", {
                "requestId": rid(501), "planId": first["planId"],
                "manifestHash": first["manifestHash"], "confirm": True}),
                410, "PLAN_EXPIRED")
            # A completed request receipt survives TTL and does not re-run.
            self.assertEqual(self.ok(self.post("plans", body), 201)["planId"],
                             first["planId"])

    # g. dictionary -------------------------------------------------------
    def dictionary(self, name="dict_basic.sqlite", tables=None):
        path = self.root / name
        if tables is None:
            tables = {"tags": [("red", 0, "\u7ea2\u8272", 100),
                               ("blue", 0, "\u84dd\u8272", 50),
                               ("blue_eyes", 0, "\u84dd\u773c\u775b", 20)]}
        return make_sqlite(path, tables)

    def import_dictionary(self, path, table_name=None):
        body = {"requestId": self.request_id(), "path": str(path)}
        if table_name is not None:
            body["tableName"] = table_name
        accepted = self.ok(self.tpost("dictionary/import", body), 202)
        return self.tjob(accepted["job"]["jobId"])

    def test_dictionary_translation_missing_stays_missing_and_table_required(self):
        dictionary = self.dictionary()
        job = self.import_dictionary(dictionary)
        self.assertEqual((job["status"], job["written"], job["processed"]), ("done", 1, 1))
        status = self.ok(self.tget("dictionary", {}))
        self.assertEqual(status["status"], "ready")
        self.assertEqual(status["tableName"], "tags")
        self.assertEqual(status["rows"], 3)
        self.assertTrue(status["dictionaryVersion"].startswith("dict_"))
        self.assertEqual(len(status["dictionaryVersion"]), 69)

        snapshot = self.snapshot("trans")
        confirmed, _, _ = self.confirm(snapshot, "dict.png", "dict.txt",
                                       tag_spec("txt", "comma"))
        document_id = confirmed["document"]["documentId"]
        fresh = self.snapshot("trans")
        item = self.item(fresh, "dict.png")
        binding = next(b for b in self.document(document_id)["bindings"]
                       if b["bindingId"] == item["bindingId"])
        selections = [{"itemId": item["itemId"], "bindingId": binding["bindingId"],
                       "recordIds": binding["recordIds"], "pointers": [""]}]
        with mock.patch.object(schema, "remote") as remote:
            plan = self.ok(self.tpost("plans", {
                "requestId": self.request_id(),
                "scope": {"mode": "page_selected", "snapshotId": fresh["snapshotId"],
                          "pageOffset": 0, "itemIds": [item["itemId"]]},
                "selections": selections, "engine": "dictionary",
                "sourceLanguage": "en", "targetLanguage": "zh-CN"}), 201)
            self.assertEqual(plan["status"], "ready")
            self.assertEqual(plan["targetSegments"], 5)
            self.assertEqual(plan["dictionaryVersion"], status["dictionaryVersion"])
            started = self.ok(self.tpost("jobs/start", {
                "requestId": self.request_id(), "planId": plan["planId"],
                "manifestHash": plan["manifestHash"], "confirmRemote": False,
                "confirmCost": False, "acceptMaximumOutputTokens": 0}), 202)
            result = self.tjob(started["job"]["jobId"])
            self.assertEqual((result["status"], result["written"]), ("done", 5))
            self.assertEqual(result["failed"], 0)
            remote.assert_not_called()

        view = self.ok(self.tget("view", {"documentId": [document_id]}))
        # Root revision: one base record plus one write per translated/missing target.
        self.assertEqual(view["revision"], 6)
        self.assertEqual(view["sourceVersion"], self.document(document_id)["sourceVersion"])
        translated = {s["sourceText"]: s for s in view["segments"]}
        self.assertEqual(translated["red"]["targetText"], "\u7ea2\u8272")
        self.assertEqual(translated["blue"]["targetText"], "\u84dd\u8272")
        self.assertEqual(translated["blue eyes"]["targetText"], "\u84dd\u773c\u775b")
        self.assertEqual(translated["Red"]["status"], "missing")
        self.assertEqual(translated["Red"]["targetText"], "Red")
        self.assertEqual(translated["unknown_tag"]["status"], "missing")
        self.assertEqual(translated["unknown_tag"]["targetText"], "unknown_tag")
        self.assertTrue(all(s["engine"] == "dictionary" for s in view["segments"]))

        # A different table in a different database is a new dictionary version;
        # old dictionary translations become dictionary_changed, not re-paired.
        second = self.dictionary("dict_second.sqlite",
                                 {"tags_v2": [("red", 0, "\u7ea2", 1)]})
        self.assertEqual(self.import_dictionary(second)["status"], "done")
        changed = self.ok(self.tget("view", {"documentId": [document_id]}))
        self.assertTrue(all(s["staleReason"] == "dictionary_changed" for s in changed["segments"]))
        self.assertTrue(all(s["status"] == "stale" for s in changed["segments"]))

        # Two valid candidate tables require an explicit choice and never guess.
        ambiguous = self.dictionary("dict_ambiguous.sqlite", {
            "tags_a": [("a", 0, "A", 1)],
            "tags_b": [("b", 0, "B", 1)]})
        failed = self.import_dictionary(ambiguous)
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["error"]["code"], "DICTIONARY_TABLE_REQUIRED")
        self.assertEqual(failed["error"]["details"]["tables"], ["tags_a", "tags_b"])
        self.assertEqual(self.ok(self.tget("dictionary", {}))["status"], "ready")

    # g. API fail closed --------------------------------------------------
    def test_api_translation_requires_configuration_and_cost_confirmation(self):
        snapshot = self.snapshot("trans")
        confirmed, _, _ = self.confirm(snapshot, "note.png", "note.txt",
                                       sidecar_spec("txt", "nl"))
        document_id = confirmed["document"]["documentId"]
        fresh = self.snapshot("trans")
        item = self.item(fresh, "note.png")
        binding = next(b for b in self.document(document_id)["bindings"]
                       if b["bindingId"] == item["bindingId"])
        selections = [{"itemId": item["itemId"], "bindingId": binding["bindingId"],
                       "recordIds": binding["recordIds"], "pointers": [""]}]
        scope = {"mode": "page_selected", "snapshotId": fresh["snapshotId"],
                 "pageOffset": 0, "itemIds": [item["itemId"]]}
        with mock.patch.object(schema, "remote") as remote:
            self.error(self.tpost("plans", {
                "requestId": self.request_id(), "scope": scope,
                "selections": selections, "engine": "api",
                "sourceLanguage": "en", "targetLanguage": "zh-CN"}),
                400, "INVALID_REQUEST")
            remote.assert_not_called()

            settings = self.ok(self.tpost("settings", {
                "requestId": self.request_id(), "expectedConfigVersion": None,
                "endpoint": "http://127.0.0.1:9/v1/chat/completions",
                "model": "offline-fixture", "enabled": True}), 200)
            credentials = self.ok(self.tpost("credentials", {
                "requestId": self.request_id(),
                "expectedConfigVersion": settings["configVersion"],
                "apiKey": ""}), 200)
            # An explicitly saved empty key is only trusted for loopback HTTP.
            self.assertFalse(credentials["hasKey"])
            plan = self.ok(self.tpost("plans", {
                "requestId": self.request_id(), "scope": scope,
                "selections": selections, "engine": "api",
                "sourceLanguage": "en", "targetLanguage": "zh-CN"}), 201)
            self.assertEqual(plan["status"], "ready")
            self.assertEqual(plan["remoteBatches"], 1)
            self.assertGreater(plan["maximumOutputTokens"], 0)
            for confirm_remote, confirm_cost, maximum in (
                    (False, False, plan["maximumOutputTokens"]),
                    (True, False, plan["maximumOutputTokens"]),
                    (True, True, plan["maximumOutputTokens"] - 1)):
                self.error(self.tpost("jobs/start", {
                    "requestId": self.request_id(), "planId": plan["planId"],
                    "manifestHash": plan["manifestHash"],
                    "confirmRemote": confirm_remote, "confirmCost": confirm_cost,
                    "acceptMaximumOutputTokens": maximum}),
                    400, "INVALID_REQUEST")
            remote.assert_not_called()
            self.assertEqual(self.ok(self.tget("plans", {"planId": [plan["planId"]]}))["status"],
                             "ready")
            view = self.ok(self.tget("view", {"documentId": [document_id]}))
            self.assertEqual((view["revision"], view["segments"]), (0, []))

    # g. store CAS and stale ---------------------------------------------
    def test_store_revision_cas_and_source_change_staleness(self):
        snapshot = self.snapshot("trans")
        confirmed, _, _ = self.confirm(snapshot, "note.png", "note.txt",
                                       sidecar_spec("txt", "nl"))
        document_id = confirmed["document"]["documentId"]
        document = self.document(document_id)
        segment = document["fields"][0]["segments"][0]
        manual = self.ok(self.tpost("manual", {
            "requestId": self.request_id(), "documentId": document_id,
            "expectedSourceVersion": document["sourceVersion"], "expectedRevision": 0,
            "segmentId": segment["segmentId"], "targetText": "\u7b2c\u4e00\u884c",
            "sourceLanguage": "en", "targetLanguage": "zh-CN"}), 200)
        self.assertEqual(manual["revision"], 1)
        self.assertEqual(manual["segment"]["segmentRevision"], 1)
        self.assertTrue(manual["segment"]["manual"])
        self.error(self.tpost("manual", {
            "requestId": self.request_id(), "documentId": document_id,
            "expectedSourceVersion": document["sourceVersion"], "expectedRevision": 0,
            "segmentId": segment["segmentId"], "targetText": "again",
            "sourceLanguage": "en", "targetLanguage": "zh-CN"}),
            409, "VERSION_CONFLICT")

        owner = self.state / "annotations" / "owner.lock"
        self.assertTrue(owner.is_file())
        self.assertEqual(stat.S_IMODE(owner.stat().st_mode), 0o600)
        key = (document["libraryId"], document_id)
        envelope = self.translation.store.read_record("translations", key)
        with self.assertRaises(StoreError) as caught:
            self.translation.store.compare_write("translations", key, 0,
                                                 copy.deepcopy(envelope["record"]))
        self.assertEqual(caught.exception.code, "VERSION_CONFLICT")

        original = self.bytes("trans/note.txt")
        view = self.ok(self.tget("view", {"documentId": [document_id]}))
        self.assertEqual(view["segments"][0]["targetText"], "\u7b2c\u4e00\u884c")
        self.assertIsNone(view["segments"][0]["staleReason"])
        old_version, old_generation = view["snapshotSourceVersion"], view["snapshotGeneration"]

        write(self.root / "trans/note.txt", b"Changed source.\n")
        stale = self.ok(self.tget("view", {"documentId": [document_id]}))
        self.assertNotEqual(stale["sourceVersion"], old_version)
        self.assertGreater(stale["generation"], old_generation)
        self.assertEqual(stale["snapshotSourceVersion"], old_version)
        self.assertEqual(stale["segments"][0]["status"], "stale")
        self.assertEqual(stale["segments"][0]["staleReason"], "source_changed")
        self.error(self.tpost("manual", {
            "requestId": self.request_id(), "documentId": document_id,
            "expectedSourceVersion": old_version, "expectedRevision": 1,
            "segmentId": segment["segmentId"], "targetText": "late",
            "sourceLanguage": "en", "targetLanguage": "zh-CN"}),
            409, "TRANSLATION_STALE")

        # Reverting the bytes does not re-pair by text: identity still changed.
        write(self.root / "trans/note.txt", original)
        reverted = self.ok(self.tget("view", {"documentId": [document_id]}))
        self.assertGreater(reverted["generation"], stale["generation"])
        self.assertEqual(reverted["snapshotSourceVersion"], old_version)
        self.assertEqual(reverted["segments"][0]["staleReason"], "source_changed")


if __name__ == "__main__":
    unittest.main()

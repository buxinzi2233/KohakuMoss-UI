"""Offline annotation bridge fixtures and strict HTTP boundary tests.

No ASG imports, processes, source files, real state or provider requests.
Fixture factories are reusable by integration and translation tests.
"""

import copy
import hashlib
import http.client
import json
import os
import socket
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler

from kohakumoss_ui.contract import annotations as a
from kohakumoss_ui.contract.grabber import Grabber, NoRedirect
from kohakumoss_ui.contract.root import TrainerRoot
from kohakumoss_ui.server.http import Api, Handler, make_server


def ident(prefix, number=1):
    return prefix + "_" + ("%032x" % number)


def request_id():
    return "req_00000000000040008000000000000001"


def spec():
    return {"format": "txt", "kind": "nl", "delimiter": None,
            "mapping": {"mode": "sidecar"}, "fields": [{"pointer": "", "type": "string"}]}


def document_fixture(raw="api_key=literal-secret\r\n\U0001f433 config request"):
    hashed = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
    size = a.utf16(raw)
    segment = {"segmentId": ident("seg"), "recordId": ident("rec"), "pointer": "", "ordinal": 0,
               "kind": "nl", "text": raw, "sourceHash": hashed, "sourceVersion": ident("ver"),
               "generation": 1, "rawRanges": [{"start": 0, "end": size}],
               "valueRange": {"start": 0, "end": size}, "elementIndex": None}
    return {"libraryId": ident("lib"), "documentId": ident("doc"), "group": "example", "path": "image.txt",
            "format": "txt", "sourceHash": hashed, "sourceVersion": ident("ver"), "generation": 1,
            "bytes": len(raw.encode()), "bom": False, "newline": "crlf" if "\r\n" in raw else "none",
            "finalNewline": False, "rawText": raw,
            "bindings": [{"bindingId": ident("bind"), "bindingRevision": 1, "itemIds": [ident("item")],
                          "recordIds": [ident("rec")], "spec": spec(), "status": "confirmed"}],
            "fields": [{"recordId": ident("rec"), "pointer": "", "type": "string", "kind": "nl", "delimiter": None,
                        "value": raw, "rawRange": {"start": 0, "end": size}, "segments": [segment] if raw else []}],
            "readOnly": False, "readOnlyReason": None}


def candidate_fixture():
    doc = document_fixture()
    return {"documentId": doc["documentId"], "path": doc["path"], "format": doc["format"],
            "sourceHash": doc["sourceHash"], "sourceVersion": doc["sourceVersion"], "bytes": doc["bytes"],
            "bindingIds": [ident("bind")], "suggestedKind": "nl", "status": "confirmed"}


def snapshot_fixture():
    return {"snapshotId": ident("snap"), "libraryId": ident("lib"), "group": "example",
            "filters": {"format": "any", "status": "any", "search": ""}, "total": 1, "offset": 0, "limit": 60,
            "items": [{"itemId": ident("item"), "group": "example", "imagePath": "image.png",
                       "imageUrl": "/files/example/image.png", "candidates": [candidate_fixture()],
                       "bindingId": ident("bind"), "annotationStatus": "confirmed"}],
            "truncated": False, "createdAt": 1, "expiresAt": 901}


def scope_fixture():
    return {"mode": "page_selected", "snapshotId": ident("snap"), "pageOffset": 0, "itemIds": [ident("item")]}


def selection_fixture():
    return {"itemId": ident("item"), "bindingId": ident("bind"), "recordIds": [ident("rec")], "pointers": [""]}


def plan_fixture(undo=False):
    return {"planId": ident("plan"), "libraryId": ident("lib"), "requestId": request_id(),
            "kind": "undo" if undo else "edit", "status": "ready", "scope": None if undo else scope_fixture(),
            "operation": {"op": "restore_job", "jobId": ident("job")} if undo else {"op": "clear"},
            "summary": {"images": 1, "documents": 1, "rows": 1, "fields": 1, "matchedOccurrences": 1,
                        "changedOccurrences": 1, "duplicateOccurrences": 0, "ready": 1, "skipped": 0, "errors": 0},
            "createdAt": 1, "expiresAt": 1801, "manifestHash": "sha256:" + "1" * 64}


def job_fixture():
    return {"jobId": ident("job"), "planId": ident("plan"), "kind": "apply", "status": "done",
            "total": 1, "processed": 1, "written": 1, "skipped": 0, "failed": 0, "currentDocumentId": None,
            "cancelRequested": False, "createdAt": 1, "updatedAt": 2, "finishedAt": 2, "error": None,
            "undoAvailable": True}


def cases():
    """The fourteen method/path pairs, valid request and complete response."""
    plan = plan_fixture()
    row = {"documentId": ident("doc"), "path": "image.txt", "itemIds": [ident("item")],
           "beforeHash": document_fixture()["sourceHash"], "afterHash": "sha256:" + "0" * 64,
           "beforeVersion": ident("ver"), "bindingRevisions": [{"bindingId": ident("bind"), "bindingRevision": 1}],
           "action": "write", "status": "ready", "code": None, "matchedOccurrences": 1,
           "changedOccurrences": 1, "duplicateOccurrences": 0, "diff": None}
    job_row = {"documentId": ident("doc"), "status": "written", "code": None,
               "beforeHash": row["beforeHash"], "afterHash": row["afterHash"], "beforeVersion": ident("ver"),
               "afterVersion": ident("ver", 2), "backupId": ident("job") + ":" + ident("doc")}
    return [
        ("POST", "snapshots", {"requestId": request_id(), "group": "example"}, 201, snapshot_fixture()),
        ("GET", "snapshots", {"snapshotId": [ident("snap")]}, 200, snapshot_fixture()),
        ("GET", "candidates", {"snapshotId": [ident("snap")], "itemId": [ident("item")]},
         200, {"itemId": ident("item"), "candidates": [candidate_fixture()]}),
        ("POST", "bindings/confirm", {"requestId": request_id(), "snapshotId": ident("snap"), "itemId": ident("item"),
         "documentId": ident("doc"), "expectedVersion": ident("ver"), "spec": spec()},
         200, {"bindingId": ident("bind"), "bindingRevision": 1, "document": document_fixture()}),
        ("POST", "bindings/select", {"requestId": request_id(), "snapshotId": ident("snap"), "itemId": ident("item"),
         "bindingId": ident("bind"), "expectedBindingRevision": 1},
         200, {"itemId": ident("item"), "bindingId": ident("bind"), "bindingRevision": 1}),
        ("GET", "documents", {"documentId": [ident("doc")]}, 200, document_fixture()),
        ("POST", "plans", {"requestId": request_id(), "scope": scope_fixture(), "operation": {"op": "clear"}}, 201, plan),
        ("GET", "plans", {"planId": [ident("plan")]}, 200, plan),
        ("GET", "plans/rows", {"planId": [ident("plan")]}, 200,
         {"planId": ident("plan"), "total": 1, "offset": 0, "limit": 60, "rows": [row]}),
        ("POST", "plans/apply", {"requestId": request_id(), "planId": ident("plan"), "manifestHash": plan["manifestHash"],
         "confirm": True}, 202, {"job": job_fixture()}),
        ("GET", "jobs", {"jobId": [ident("job")]}, 200, job_fixture()),
        ("GET", "jobs/rows", {"jobId": [ident("job")]}, 200,
         {"jobId": ident("job"), "total": 1, "offset": 0, "limit": 60, "rows": [job_row]}),
        ("POST", "jobs/stop", {"requestId": request_id(), "jobId": ident("job")}, 200, {"job": job_fixture()}),
        ("POST", "undo/plans", {"requestId": request_id(), "jobId": ident("job")}, 201, plan_fixture(True)),
    ]


class FakeAnnotations(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.respond(None)

    def do_POST(self):
        self.respond(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))

    def respond(self, body):
        self.server.seen.append((self.command, self.path, body))
        if self.path == "/api/sources":
            code, payload = 200, {"capabilities": self.server.capabilities}
            raw = json.dumps(payload).encode()
            mime = "application/json"
        else:
            code, payload = self.server.reply
            raw = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            mime = self.server.mime
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        if code == 302:
            self.send_header("Location", "/unapproved-target")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            # Oversize-response tests deliberately close before receiving bytes.
            pass


class AnnotationCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"KOHAKUMOSS_UI_STATE": str(self.base / "state"),
                                          "KOHAKUMOSS_GRABBER_ROOT": str(self.base / "asg")})
        env.start()
        self.addCleanup(env.stop)
        self.root = TrainerRoot(self.base / "trainer")
        self.client = Grabber(self.root)
        self.contract = a.AnnotationContract(self.client)
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeAnnotations)
        self.upstream.capabilities = {"annotations_v1": True}
        self.upstream.reply = (200, {"ok": True, "data": document_fixture()})
        self.upstream.mime = "application/json"
        self.upstream.seen = []
        self.start_server(self.upstream)
        self.client._process = mock.Mock()
        self.client._process.poll.return_value = None
        self.client._port = self.upstream.server_address[1]
        self.addCleanup(self.client.close)

    def start_server(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.addCleanup(close)

    def invoke(self, method, route, params):
        if method == "GET":
            return self.contract.get(route, params)
        return self.contract.post(route, params)


class BridgeTests(AnnotationCase):
    def test_all_fourteen_routes_exact_wire_and_no_redaction(self):
        self.assertEqual(len(cases()), 14)
        with mock.patch.object(self.client, "_redact", side_effect=AssertionError("No redaction")):
            for method, route, params, status, data in cases():
                with self.subTest(method=method, route=route):
                    payload = {"ok": True, "data": data}
                    self.upstream.reply = (status, payload)
                    self.assertEqual(self.invoke(method, route, params), (status, payload))
                    self.assertEqual(self.upstream.seen[-2], ("GET", "/api/sources", None))
                    actual = self.upstream.seen[-1]
                    self.assertEqual(actual[0], method)
                    self.assertEqual(urlsplit(actual[1]).path, "/api/annotations/" + route)
                    if method == "POST":
                        self.assertEqual(actual[2], params)
        self.assertFalse((self.base / "state").exists())

    def test_document_helper_and_get_token_not_forwarded(self):
        self.assertEqual(self.contract.document(ident("doc")), document_fixture())
        self.contract.get("documents", {"documentId": [ident("doc")], "token": ["private-ui-token"]})
        self.assertNotIn("token", self.upstream.seen[-1][1])
        with self.assertRaises(a.AnnotationError) as error:
            self.contract.document(ident("doc", 2))
        self.assertEqual(error.exception.code, "UPSTREAM_PROTOCOL")

    def test_structured_json_document_utf16_and_full_readonly_64k(self):
        doc = document_fixture("x" * 65536)
        doc.update(fields=[], bindings=[], readOnly=True, readOnlyReason="SCHEMA_REQUIRED")
        self.upstream.reply = 200, {"ok": True, "data": doc}
        self.assertEqual(len(self.contract.document(ident("doc"))["rawText"]), 65536)
        raw = '{"caption":"\\u4e2d\\ud83d\\udc33","config":{"request":1e+02}}'
        decoded = "\u4e2d\U0001f433"
        doc = document_fixture(raw)
        doc.update(format="json", path="image.json")
        binding = doc["bindings"][0]["spec"]
        binding.update(format="json", mapping={"mode": "sidecar", "recordsPointer": "",
                                              "recordIdPointer": None, "imagePointer": None},
                       fields=[{"pointer": "/caption", "type": "string"}])
        f = doc["fields"][0]
        start = raw.index('"', raw.index(":"))
        end = raw.index('"', start + 1) + 1
        f.update(pointer="/caption", value=decoded, rawRange={"start": start, "end": end})
        f["segments"][0].update(pointer="/caption", text=decoded,
                               rawRanges=[{"start": start + 1, "end": end - 1}],
                               valueRange={"start": 0, "end": 3})
        self.upstream.reply = 200, {"ok": True, "data": doc}
        actual = self.contract.document(ident("doc"))
        self.assertEqual(actual, doc)
        self.assertIn('"config":{"request":1e+02}', actual["rawText"])

    def test_each_route_rejects_unknown_fields_and_wrong_methods_before_network(self):
        for method, route, params, _, _ in cases():
            with self.subTest(route=route, method=method):
                bad = {**params, "secret": ["x"] if method == "GET" else "x"}
                with self.assertRaises(a.AnnotationError) as error:
                    self.invoke(method, route, bad)
                self.assertEqual(error.exception.status, 400)
                with self.assertRaises(a.AnnotationError) as error:
                    self.client.annotation_request("PATCH", route)
                self.assertEqual(error.exception.status, 405)
        for route in ("../documents", "documents?secret=x", "/documents", "http://evil", "documents/", "unknown"):
            with self.assertRaises(a.AnnotationError):
                self.contract.get(route, {})
        self.assertEqual(self.upstream.seen, [])

    def test_nested_request_schemas_boolean_ids_scope_operations(self):
        valid = {"requestId": request_id(), "scope": scope_fixture(), "operation": {"op": "clear"}}
        invalid = []
        for key, val in (("mode", "unknown"), ("pageOffset", True), ("itemIds", []),
                         ("snapshotId", "snap_short"), ("secret", "bad")):
            invalid.append({**valid, "scope": {**valid["scope"], key: val}})
        for operation in ({"op": "clear", "text": "extra"}, {"op": "nl_replace", "match": "", "replacement": "x"},
                          {"op": "delete_file", "confirmDelete": 1}, {"op": "restore_job", "jobId": ident("job")},
                          {"op": "tag_add", "tokens": []}, {"op": "tag_add", "tokens": ["a\x00"]}):
            invalid.append({**valid, "operation": operation})
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(a.AnnotationError):
                self.contract.post("plans", body)
        for entry in cases():
            if entry[1] == "bindings/confirm":
                base = entry[2]
                break
        for value in ({**spec(), "secret": "bad"}, {**spec(), "mapping": {"mode": "sidecar", "path": "/tmp"}},
                      {**spec(), "delimiter": "comma"}, {**spec(), "fields": [{"pointer": "", "type": "number"}]}):
            with self.assertRaises(a.AnnotationError):
                self.contract.post("bindings/confirm", {**base, "spec": value})
        self.assertEqual(self.upstream.seen, [])

    def test_all_operation_variants_accept_complete_schema(self):
        ops = [{"op": name, "tokens": ["Tag", "tag"]} for name in ("tag_add", "tag_remove", "tag_set")]
        ops += [{"op": "tag_replace", "match": "a", "replacement": "b"}, {"op": "tag_dedupe"},
                {"op": "nl_prepend", "text": "a", "separator": "\n"}, {"op": "nl_append", "text": "a"},
                {"op": "nl_replace", "match": "a", "replacement": ""}, {"op": "nl_delete", "match": "a"},
                {"op": "whole_text", "text": ""}, {"op": "clear"}, {"op": "delete_file", "confirmDelete": True},
                {"op": "whole_document", "rawText": "{}"}, {"op": "normalize_newlines", "newline": "crlf"},
                {"op": "create", "itemId": ident("item"), "path": "image.txt", "spec": spec(), "initialValue": ""}]
        for op in ops:
            with self.subTest(op=op):
                a.validate_request("POST", "plans", body={"requestId": request_id(), "scope": scope_fixture(),
                                   "operation": op, "selections": [selection_fixture()], "missingPolicy": "error"})

    def test_capability_managed_child_proxy_redirect(self):
        for capabilities in ({}, {"annotations_v1": False}, {"annotations_v1": 1}, {"annotations_v1": "true"}):
            self.upstream.capabilities = capabilities
            with self.assertRaises(a.AnnotationError) as error:
                self.contract.document(ident("doc"))
            self.assertEqual(error.exception.status, 409)
            self.assertIn("Update ArtistSourceGrabber", str(error.exception))
        self.assertTrue(all(path == "/api/sources" for _, path, _ in self.upstream.seen))
        self.upstream.capabilities = {"annotations_v1": True}
        self.upstream.reply = (302, b"redirect")
        with self.assertRaises(a.AnnotationError) as error:
            self.contract.document(ident("doc"))
        self.assertEqual(error.exception.status, 502)
        self.assertFalse(any(path == "/unapproved-target" for _, path, _ in self.upstream.seen))
        self.assertTrue(any(isinstance(handler, NoRedirect) for handler in self.client._opener.handlers))
        self.assertFalse(any(isinstance(handler, ProxyHandler) and handler.proxies for handler in self.client._opener.handlers))
        self.client._process.poll.return_value = 1
        with self.assertRaises(a.AnnotationError) as error:
            self.contract.document(ident("doc"))
        self.assertEqual(error.exception.status, 503)

    def test_structured_upstream_status_details_retryable_preserved(self):
        for status, code in ((413, "DOCUMENT_TOO_LARGE"), (409, "VERSION_CONFLICT"), (410, "PLAN_EXPIRED"),
                             (422, "DUPLICATE_KEY"), (507, "STORAGE_FULL")):
            payload = a.AnnotationError(status, code, "sensitive upstream /home/private api_key=secret",
                                        {"documentId": ident("doc")}, False).payload
            self.upstream.reply = status, payload
            actual_status, actual = self.contract.get("documents", {"documentId": [ident("doc")]})
            self.assertEqual(actual_status, status)
            self.assertEqual(actual["error"]["details"], payload["error"]["details"])
            self.assertEqual(actual["error"]["code"], code)
            self.assertIs(actual["error"]["retryable"], False)
            self.assertNotIn("secret", json.dumps(actual))
        with self.assertRaises(a.AnnotationError) as error:
            self.contract.document(ident("doc"))
        self.assertEqual(error.exception.status, 507)

    def test_timeout_vs_unavailable_and_no_mutation_retry(self):
        for exception, status in ((TimeoutError(), 504), (URLError(socket.timeout()), 504),
                                  (URLError(ConnectionRefusedError()), 503), (ConnectionResetError(), 503)):
            with mock.patch.object(self.client._opener, "open", side_effect=exception) as opened:
                with self.assertRaises(a.AnnotationError) as error:
                    self.contract.post("jobs/stop", {"requestId": request_id(), "jobId": ident("job")})
                self.assertEqual(error.exception.status, status)
                self.assertTrue(error.exception.retryable)
                self.assertEqual(opened.call_count, 1)
                self.assertEqual(opened.call_args.kwargs["timeout"], 30)
        real_open = self.client._opener.open
        def timeout_mutation(req, **kwargs):
            if req.method == "POST":
                raise TimeoutError()
            return real_open(req, **kwargs)
        with mock.patch.object(self.client._opener, "open", side_effect=timeout_mutation) as opened:
            for _ in range(2):
                with self.assertRaises(a.AnnotationError):
                    self.contract.post("jobs/stop", {"requestId": request_id(), "jobId": ident("job")})
            mutations = [c.args[0] for c in opened.call_args_list if c.args[0].method == "POST"]
            self.assertEqual(len(mutations), 2)
            self.assertEqual(mutations[0].data, mutations[1].data)

    def test_bad_upstream_json_size_and_nested_leak_fail_closed(self):
        bad = [b"<html>secret</html>", b'{"ok":true,"ok":false}', b'{"n":NaN}', b'"text"',
               b'{"x":"\\ud800"}', b"\xff", b" " * (a.MAX_RESPONSE + 1)]
        for raw in bad:
            self.upstream.reply = (200, raw)
            with self.subTest(size=len(raw)), self.assertRaises(a.AnnotationError) as error:
                self.contract.document(ident("doc"))
            self.assertEqual(error.exception.status, 502)
        self.upstream.reply = (200, {"ok": True, "data": document_fixture()})
        self.upstream.mime = "text/html"
        with self.assertRaises(a.AnnotationError):
            self.contract.document(ident("doc"))

    def test_full_response_schema_rejects_extra_and_missing_fields_recursively(self):
        def objects(value, path=()):
            if isinstance(value, dict):
                yield path
                for key, child in value.items():
                    yield from objects(child, path + (key,))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    yield from objects(child, path + (index,))
        for method, route, _, status, data in cases():
            payload = {"ok": True, "data": data}
            for path in objects(payload):
                for action in ("extra", "missing"):
                    changed = copy.deepcopy(payload)
                    target = changed
                    for key in path:
                        target = target[key]
                    if action == "extra":
                        target["apiKey"] = "must-not-escape"
                    else:
                        if not target:
                            continue
                        target.pop(next(iter(target)))
                    with self.subTest(route=route, path=path, action=action), self.assertRaises(a.AnnotationError):
                        a.validate_response(method, route, status, changed)

    def test_document_segment_range_and_job_invariants(self):
        mutations = [lambda d: d.update(generation=True),
                     lambda d: d["fields"][0]["segments"][0].update(generation=2),
                     lambda d: d["fields"][0]["segments"][0].update(elementIndex=0),
                     lambda d: d["fields"][0]["segments"][0]["rawRanges"][0].update(end=99999),
                     lambda d: d["fields"][0]["segments"][0]["valueRange"].update(start=1),
                     lambda d: d.update(bytes=65537),
                     lambda d: d["fields"][0]["segments"].append(copy.deepcopy(d["fields"][0]["segments"][0]))]
        for mutate in mutations:
            doc = document_fixture()
            mutate(doc)
            with self.assertRaises(a.AnnotationError):
                a.validate_response("GET", "documents", 200, {"ok": True, "data": doc})
        job = job_fixture()
        job["processed"] = 0
        with self.assertRaises(a.AnnotationError):
            a.validate_response("GET", "jobs", 200, {"ok": True, "data": job})


class HTTPTests(AnnotationCase):
    def setUp(self):
        super().setUp()
        self.ui = make_server(self.root, "127.0.0.1", 0, "test-token")
        api = self.ui.RequestHandlerClass.api
        api.grabber = self.client
        api.annotations = self.contract
        self.start_server(self.ui)

    def call(self, method, suffix, raw=None, headers=None, prefix="/api/grabber/annotations/"):
        conn = http.client.HTTPConnection("127.0.0.1", self.ui.server_address[1], timeout=3)
        try:
            conn.request(method, prefix + suffix, body=raw,
                         headers={"X-UI-Token": "test-token", **(headers or {})})
            response = conn.getresponse()
            body = response.read()
            return response.status, json.loads(body) if body else None
        finally:
            conn.close()

    def test_http_all_routes_status_and_legacy_isolation(self):
        for method, route, params, status, data in cases():
            self.upstream.reply = status, {"ok": True, "data": data}
            suffix = route + ("?" + urlencode(params, doseq=True) if method == "GET" else "")
            raw = json.dumps(params) if method == "POST" else None
            actual = self.call(method, suffix, raw, {"Content-Type": "application/json"})
            self.assertEqual(actual, (status, {"ok": True, "data": data}))
        with mock.patch.object(self.client, "get", return_value={"legacy": True}):
            self.assertEqual(self.call("GET", "status", prefix="/api/grabber/"), (200, {"legacy": True}))

    def test_token_methods_query_and_json_parse_errors_are_structured(self):
        suffix = "documents?documentId=" + ident("doc")
        checks = [("GET", suffix, None, {"X-UI-Token": "bad"}, 403),
                  ("DELETE", suffix, None, {}, 405), ("PATCH", suffix, None, {}, 405),
                  ("PUT", suffix, None, {}, 405), ("OPTIONS", suffix, None, {}, 405),
                  ("GET", suffix + "&documentId=" + ident("doc"), None, {}, 400),
                  ("GET", suffix + "&unknown=", None, {}, 400),
                  ("GET", suffix + "&token=a&token=b", None, {}, 400),
                  ("GET", suffix + "&bad=%GG", None, {}, 400),
                  ("GET", "documents?documentId", None, {}, 400),
                  ("POST", "snapshots", "{}", {"Content-Type": "text/plain"}, 400),
                  ("POST", "snapshots?extra=1", "{}", {"Content-Type": "application/json"}, 400)]
        for raw in ('{"requestId":"a","requestId":"b"}', '{"x":NaN}', '{"x":Infinity}',
                    '{"x":1e999}', '{"x":"\\ud800"}', '[]', 'null', '{', ''):
            checks.append(("POST", "snapshots", raw, {"Content-Type": "application/json"}, 400))
        for method, path, raw, headers, expected in checks:
            with self.subTest(method=method, path=path, raw=raw):
                status, payload = self.call(method, path, raw, headers)
                self.assertEqual(status, expected)
                self.assertIs(payload["ok"], False)
                self.assertEqual(set(payload["error"]), {"code", "message", "details", "retryable"})
        self.assertEqual(self.upstream.seen, [])

    def test_framing_duplicate_length_type_transfer_encoding_and_size(self):
        for headers, expected in [([("Content-Length", "2"), ("Content-Length", "2")], 400),
                                  ([("Content-Length", "-1")], 400), ([("Content-Length", "+2")], 400),
                                  ([("Content-Length", "1000001")], 413),
                                  ([("Transfer-Encoding", "chunked")], 400),
                                  ([("Content-Length", "2"), ("Content-Type", "application/json")], 400), ([], 400)]:
            conn = http.client.HTTPConnection("127.0.0.1", self.ui.server_address[1], timeout=3)
            try:
                conn.putrequest("POST", "/api/grabber/annotations/snapshots")
                conn.putheader("X-UI-Token", "test-token")
                conn.putheader("Content-Type", "application/json")
                for key, value in headers:
                    conn.putheader(key, value)
                conn.endheaders()
                response = conn.getresponse()
                self.assertEqual(response.status, expected)
                self.assertIs(json.loads(response.read())["ok"], False)
            finally:
                conn.close()

    def test_translation_prefix_structured_errors_and_close_order(self):
        api = self.ui.RequestHandlerClass.api
        service = mock.Mock()
        service.get.return_value = (200, {"ok": True, "data": {"hasKey": False}})
        service.post.side_effect = a.AnnotationError(409, "SETTINGS_CHANGED", "Settings changed")
        api._translation = service
        self.assertEqual(self.call("GET", "settings", prefix="/api/translation/")[0], 200)
        status, payload = self.call("POST", "settings", "{}", {"Content-Type": "application/json"}, prefix="/api/translation/")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "SETTINGS_CHANGED")
        self.assertEqual(self.call("GET", "settings", headers={"X-UI-Token": "bad"}, prefix="/api/translation/")[0], 403)
        self.assertEqual(self.call("PATCH", "settings", prefix="/api/translation/")[0], 405)
        order = []
        service.close.side_effect = lambda: order.append("translation")
        with mock.patch.object(self.client, "close", side_effect=lambda: order.append("grabber")):
            api.close_services()
        self.assertEqual(order, ["translation", "grabber"])

    def test_old_api_construction_close_never_accesses_state(self):
        with mock.patch("kohakumoss_ui.paths.state_dir", side_effect=AssertionError("No private state")):
            api = Api(self.root, "test")
            self.assertIsNone(api._translation)
            api.close_services()
        self.assertFalse((self.base / "state").exists())

    def test_curl_smoke_loopback_only(self):
        url = "http://127.0.0.1:%s/api/grabber/annotations/documents?documentId=%s" % (self.ui.server_address[1], ident("doc"))
        result = subprocess.run(["curl", "--noproxy", "*", "--max-time", "3", "-sS", "-H",
                                 "X-UI-Token: test-token", url], capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), {"ok": True, "data": document_fixture()})


if __name__ == "__main__":
    unittest.main()

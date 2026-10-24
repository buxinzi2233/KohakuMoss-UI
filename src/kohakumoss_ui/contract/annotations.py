"""Strict annotation wire contract; no access to source files or private state.

The bridge validates complete schemas, never regex-redacts document text. All
validators return the original value: accepted source text is byte-for-byte
unchanged by serialization apart from JSON escaping.
"""

import json
import re

MAX_BODY = 1_000_000
MAX_RESPONSE = 16 * 1024 * 1024


class AnnotationError(Exception):
    def __init__(self, status, code, message, details=None, retryable=False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = {} if details is None else details
        self.retryable = retryable
        self.payload = {"ok": False, "error": {
            "code": code, "message": message, "details": self.details,
            "retryable": retryable}}


def _require(condition):
    if not condition:
        raise ValueError("Invalid annotation schema")


def text(value, maximum=32768, minimum=0):
    _require(isinstance(value, str) and minimum <= len(value) <= maximum)
    value.encode("utf-8", errors="strict")
    return value


def integer(value, minimum=0, maximum=2**63 - 1):
    _require(type(value) is int and minimum <= value <= maximum)
    return value


def boolean(value):
    _require(type(value) is bool)
    return value


def enum(*choices):
    def check(value):
        _require(type(value) in (str, type(None)) and value in choices)
        return value
    return check


def nullable(check):
    return lambda value: None if value is None else check(value)


def identifier(prefix):
    def check(value):
        _require(isinstance(value, str) and
                 re.fullmatch(prefix + r"_[0-9a-f]{32}", value) is not None)
        return value
    return check


def digest(value):
    _require(isinstance(value, str) and
             re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None)
    return value


def array(check, minimum=0, maximum=20000, unique=False):
    def validate(value):
        _require(isinstance(value, list) and minimum <= len(value) <= maximum)
        for item in value:
            check(item)
        if unique:
            _require(len({json.dumps(x, sort_keys=True, ensure_ascii=True)
                          for x in value}) == len(value))
        return value
    return validate


def obj(required, optional=None):
    optional = optional or {}
    def validate(value):
        _require(isinstance(value, dict) and set(required) <= set(value)
                 and not set(value) - (set(required) | set(optional)))
        for key, check in {**required, **optional}.items():
            if key in value:
                check(value[key])
        return value
    return validate


def relative_path(value):
    text(value, 2048, 1)
    _require(not any(c in value for c in "\\%:") and
             not any(ord(c) < 32 or ord(c) == 127 for c in value))
    parts = value.split("/")
    _require(len(parts) <= 5 and all(p and not p.startswith(".") and
                                    len(p) <= 255 for p in parts))
    return value


def group(value):
    relative_path(value)
    _require("/" not in value)
    return value


def pointer(value):
    text(value, 2048)
    _require(value == "" or value.startswith("/"))
    _require(re.search(r"~(?![01])", value) is None)
    parts = value.split("/")[1:]
    _require(all(p not in {"..", "-", "*", "**"} for p in parts))
    return value


ID = {p: identifier(p) for p in
      ("lib", "snap", "item", "doc", "bind", "rec", "seg", "plan", "job", "req", "ver")}
POSITIVE = lambda v: integer(v, 1)
LIMIT = lambda v: integer(v, 1, 100)
OFFSET = lambda v: integer(v, 0, 10000)
FORMAT = enum("txt", "caption", "json", "jsonl")
KIND = enum("tags", "nl")
DELIMITER = enum("comma", "space", None)
ANNOTATION_STATUS = enum("missing", "unconfirmed", "confirmed", "stale", "invalid", "ambiguous")
FILTERS = obj({"format": enum("any", "txt", "caption", "json", "jsonl"),
               "status": enum("any", "missing", "unconfirmed", "confirmed", "stale", "invalid", "ambiguous"),
               "search": lambda v: text(v, 200)})
FIELD_SPEC = obj({"pointer": pointer, "type": enum("string", "string_array")})


def binding_spec(value):
    obj({"format": FORMAT, "kind": KIND, "delimiter": DELIMITER,
         "mapping": lambda v: _require(isinstance(v, dict)),
         "fields": array(FIELD_SPEC, 1, 16, unique=True)})(value)
    mapping = value["mapping"]
    if value["format"] in {"txt", "caption"}:
        _require(mapping == {"mode": "sidecar"} and
                 value["fields"] == [{"pointer": "", "type": "string"}])
    else:
        obj({"mode": enum("sidecar", "records"), "recordsPointer": pointer,
             "recordIdPointer": nullable(pointer), "imagePointer": nullable(pointer)})(mapping)
        if mapping["mode"] == "sidecar":
            _require(mapping == {"mode": "sidecar", "recordsPointer": "",
                                 "recordIdPointer": None, "imagePointer": None})
        else:
            _require(mapping["imagePointer"] is not None)
        if value["format"] == "jsonl":
            _require(mapping["recordsPointer"] == "")
    pointers = [f["pointer"] for f in value["fields"]]
    _require(len(set(pointers)) == len(pointers))
    for p in pointers:
        _require(not any(p != q and (p == "" or q.startswith(p + "/")) for q in pointers))
    for field in value["fields"]:
        if field["type"] == "string_array":
            _require(value["kind"] == "tags" and value["delimiter"] is None)
        elif value["kind"] == "tags":
            _require(value["delimiter"] in {"comma", "space"})
        else:
            _require(value["delimiter"] is None)
    return value


def scope(value):
    _require(isinstance(value, dict))
    mode = value.get("mode")
    required = {"mode": enum("page_selected", "page_all", "all_filtered"), "snapshotId": ID["snap"]}
    if mode in {"page_selected", "page_all"}:
        required["pageOffset"] = OFFSET
    if mode == "page_selected":
        required["itemIds"] = array(ID["item"], 1, 100, unique=True)
    return obj(required)(value)


SELECTION = obj({"itemId": ID["item"], "bindingId": ID["bind"],
                 "recordIds": array(ID["rec"], 1, 1000, unique=True),
                 "pointers": array(pointer, 1, 16, unique=True)})
SELECTIONS = array(SELECTION, 1, 1000, unique=True)


def token(value):
    text(value, 200, 1)
    _require(not any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value))
    return value


def operation(value):
    _require(isinstance(value, dict))
    op = value.get("op")
    variants = {
        "tag_add": ({"tokens": array(token, 1)}, {}),
        "tag_remove": ({"tokens": array(token, 1)}, {}),
        "tag_replace": ({"match": token, "replacement": token}, {}),
        "tag_dedupe": ({}, {}),
        "nl_prepend": ({"text": lambda v: text(v, minimum=1)}, {"separator": enum("", " ", "\n")}),
        "nl_append": ({"text": lambda v: text(v, minimum=1)}, {"separator": enum("", " ", "\n")}),
        "nl_replace": ({"match": lambda v: text(v, minimum=1), "replacement": text}, {}),
        "nl_delete": ({"match": lambda v: text(v, minimum=1)}, {}),
        "whole_text": ({"text": text}, {}),
        "tag_set": ({"tokens": array(token)}, {}),
        "clear": ({}, {}),
        "delete_file": ({"confirmDelete": lambda v: _require(v is True)}, {}),
        "create": ({"itemId": ID["item"], "path": relative_path, "spec": binding_spec,
                    "initialValue": lambda v: array(text, maximum=2000)(v) if isinstance(v, list) else text(v)}, {}),
        "whole_document": ({"rawText": lambda v: text(v, 65536)}, {}),
        "normalize_newlines": ({"newline": enum("lf", "crlf")}, {}),
    }
    _require(isinstance(op, str) and op in variants)
    required, optional = variants[op]
    obj({"op": enum(op), **required}, optional)(value)
    if op == "create":
        spec = value["spec"]
        _require(value["path"].lower().endswith("." + spec["format"]))
        _require(spec["mapping"]["mode"] == "sidecar" and len(spec["fields"]) == 1)
        field = spec["fields"][0]
        if spec["format"] in {"json", "jsonl"}:
            _require((field == {"pointer": "/caption", "type": "string"} and spec["kind"] == "nl") or
                     (field == {"pointer": "/tags", "type": "string_array"} and spec["kind"] == "tags"))
        if field["type"] == "string_array":
            array(token, maximum=2000)(value["initialValue"])
        else:
            text(value["initialValue"])
    return value


GET_SCHEMAS = {
    "snapshots": ({"snapshotId": ID["snap"]}, {"offset": OFFSET, "limit": LIMIT}),
    "candidates": ({"snapshotId": ID["snap"], "itemId": ID["item"]}, {"sharedPath": relative_path}),
    "documents": ({"documentId": ID["doc"]}, {"expectedVersion": ID["ver"]}),
    "plans": ({"planId": ID["plan"]}, {}),
    "plans/rows": ({"planId": ID["plan"]}, {"offset": OFFSET, "limit": LIMIT, "includeDiff": boolean}),
    "jobs": ({"jobId": ID["job"]}, {}),
    "jobs/rows": ({"jobId": ID["job"]}, {"offset": OFFSET, "limit": LIMIT}),
}
POST_SCHEMAS = {
    "snapshots": ({"group": group}, {"filters": FILTERS, "limit": LIMIT}),
    "bindings/confirm": ({"snapshotId": ID["snap"], "itemId": ID["item"], "documentId": ID["doc"],
                          "expectedVersion": ID["ver"], "spec": binding_spec},
                         {"recordIds": array(ID["rec"], 1, 1000, unique=True)}),
    "bindings/select": ({"snapshotId": ID["snap"], "itemId": ID["item"], "bindingId": ID["bind"],
                         "expectedBindingRevision": POSITIVE}, {}),
    "plans": ({"scope": scope, "operation": operation}, {"selections": SELECTIONS, "missingPolicy": enum("skip", "error")}),
    "plans/apply": ({"planId": ID["plan"], "manifestHash": digest, "confirm": lambda v: _require(v is True)}, {}),
    "jobs/stop": ({"jobId": ID["job"]}, {}),
    "undo/plans": ({"jobId": ID["job"]}, {"documentIds": array(ID["doc"], 1, 1000, unique=True)}),
}


def validate_request(method, route, query=None, body=None):
    """Validate all nested input before contacting the managed child."""
    if not isinstance(route, str) or route not in GET_SCHEMAS.keys() | POST_SCHEMAS.keys():
        raise AnnotationError(404, "NOT_FOUND", "Unknown annotation endpoint")
    schemas = GET_SCHEMAS if method == "GET" else POST_SCHEMAS if method == "POST" else {}
    if route not in schemas:
        raise AnnotationError(405, "METHOD_NOT_ALLOWED", "Method not allowed")
    try:
        required, optional = schemas[route]
        if method == "GET":
            _require(body is None and isinstance(query if query is not None else {}, dict))
            params = {}
            normalized = {}
            for key, values in (query or {}).items():
                _require(isinstance(values, list) and len(values) == 1 and isinstance(values[0], str))
                raw = values[0]
                if key == "token":
                    text(raw, 4096)
                    continue
                if key in {"offset", "limit"}:
                    _require(re.fullmatch(r"[0-9]{1,10}", raw) is not None)
                    normalized[key] = int(raw)
                elif key == "includeDiff":
                    _require(raw in {"true", "false"})
                    normalized[key] = raw == "true"
                else:
                    normalized[key] = raw
                params[key] = raw
            obj(required, optional)(normalized)
            return params, None
        _require(query is None or query == {})
        obj({"requestId": ID["req"], **required}, optional)(body)
        if route == "plans":
            op = body["operation"]["op"]
            selections = body.get("selections", [])
            if selections:
                _require(len({s["itemId"] for s in selections}) == len(selections))
                if body["scope"]["mode"] == "page_selected":
                    _require({s["itemId"] for s in selections} == set(body["scope"]["itemIds"]))
            if op in {"whole_text", "tag_set"}:
                _require(len(selections) == 1 and len(selections[0]["recordIds"]) == 1 and
                         len(selections[0]["pointers"]) == 1)
            if op == "whole_document":
                _require(bool(selections))
            if op == "create":
                _require(body.get("missingPolicy", "skip") == "error" and
                         body["scope"]["mode"] == "page_selected" and
                         body["scope"]["itemIds"] == [body["operation"]["itemId"]])
        encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_BODY:
            raise AnnotationError(413, "BODY_TOO_LARGE", "Request body exceeds 1000000 bytes")
        return {}, body
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise AnnotationError(400, "INVALID_REQUEST", "Invalid annotation request fields") from None


def strict_json(raw):
    """UTF-8 only, no duplicate keys, non-finite numbers or lone surrogates."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("Non-finite JSON number")
    def walk(value, depth=0):
        _require(depth <= 40)
        if isinstance(value, dict):
            for key, item in value.items():
                text(key, MAX_RESPONSE)
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)
        elif isinstance(value, str):
            text(value, MAX_RESPONSE)
        elif isinstance(value, float):
            import math
            _require(math.isfinite(value))
    value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=pairs,
                       parse_constant=constant)
    walk(value)
    return value


def utf16(value):
    return len(value.encode("utf-16-le")) // 2


def range_value(value):
    obj({"start": integer, "end": integer})(value)
    _require(value["start"] <= value["end"])
    return value


def segment(value):
    return obj({"segmentId": ID["seg"], "recordId": ID["rec"], "pointer": pointer,
                "ordinal": integer, "kind": KIND, "text": text, "sourceHash": digest,
                "sourceVersion": ID["ver"], "generation": POSITIVE,
                "rawRanges": array(range_value, 1, 65536), "valueRange": range_value,
                "elementIndex": nullable(integer)})(value)


def field(value):
    obj({"recordId": ID["rec"], "pointer": pointer, "type": enum("string", "string_array"),
         "kind": KIND, "delimiter": DELIMITER,
         "value": lambda v: array(text, maximum=2000)(v) if isinstance(v, list) else text(v),
         "rawRange": range_value, "segments": array(segment, maximum=2000)})(value)
    if value["type"] == "string":
        text(value["value"])
        _require(value["delimiter"] in ({"comma", "space"} if value["kind"] == "tags" else {None}))
    else:
        array(text, maximum=2000)(value["value"])
        _require(value["kind"] == "tags" and value["delimiter"] is None)
    return value


BINDING = obj({"bindingId": ID["bind"], "bindingRevision": POSITIVE,
               "itemIds": array(ID["item"], 1, 10000, unique=True),
               "recordIds": array(ID["rec"], 1, 1000, unique=True), "spec": binding_spec,
               "status": enum("confirmed", "stale", "unconfirmed")})
CODE = lambda v: _require(isinstance(v, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", v) is not None)


def document(value):
    obj({"libraryId": ID["lib"], "documentId": ID["doc"], "group": group,
         "path": relative_path, "format": FORMAT, "sourceHash": digest,
         "sourceVersion": ID["ver"], "generation": POSITIVE,
         "bytes": lambda v: integer(v, 0, 65536), "bom": boolean,
         "newline": enum("lf", "crlf", "cr", "mixed", "none"), "finalNewline": boolean,
         "rawText": lambda v: text(v, 65536), "bindings": array(BINDING, maximum=10000),
         "fields": array(field, maximum=2000), "readOnly": boolean,
         "readOnlyReason": nullable(CODE)})(value)
    raw = value["rawText"]
    _require(len(raw.encode("utf-8")) + (3 if value["bom"] else 0) == value["bytes"])
    raw_size = utf16(raw)
    seen = set()
    fields_seen = set()
    for f in value["fields"]:
        key = (f["recordId"], f["pointer"])
        _require(key not in fields_seen)
        fields_seen.add(key)
        _require(f["rawRange"]["end"] <= raw_size)
        if value["format"] in {"txt", "caption"}:
            _require(f["rawRange"] == {"start": 0, "end": raw_size})
        for ordinal, s in enumerate(f["segments"]):
            _require(s["segmentId"] not in seen and s["recordId"] == f["recordId"] and
                     s["pointer"] == f["pointer"] and s["kind"] == f["kind"] and s["ordinal"] == ordinal)
            seen.add(s["segmentId"])
            for key in ("sourceHash", "sourceVersion", "generation"):
                _require(s[key] == value[key])
            if f["type"] == "string":
                _require(s["elementIndex"] is None)
                decoded = f["value"]
            else:
                integer(s["elementIndex"], 0, len(f["value"]) - 1)
                decoded = f["value"][s["elementIndex"]]
            start, end = s["valueRange"]["start"], s["valueRange"]["end"]
            _require(end <= utf16(decoded))
            _require(decoded.encode("utf-16-le")[start * 2:end * 2].decode("utf-16-le") == s["text"])
            previous = f["rawRange"]["start"]
            for r in s["rawRanges"]:
                _require(previous <= r["start"] < r["end"] <= f["rawRange"]["end"])
                previous = r["end"]
    _require(len(seen) <= 2000)
    _require(len({b["bindingId"] for b in value["bindings"]}) == len(value["bindings"]))
    return value


CANDIDATE = obj({"documentId": ID["doc"], "path": relative_path, "format": FORMAT,
                 "sourceHash": nullable(digest), "sourceVersion": ID["ver"], "bytes": integer,
                 "bindingIds": array(ID["bind"], unique=True), "suggestedKind": enum("tags", "nl", None),
                 "status": enum("unconfirmed", "confirmed", "stale", "invalid", "too_large")})


def image_url(value):
    from urllib.parse import unquote
    text(value, 8192, 1)
    _require(value.startswith("/files/"))
    relative_path(unquote(value[7:], errors="strict"))
    return value


ITEM = obj({"itemId": ID["item"], "group": group, "imagePath": relative_path, "imageUrl": image_url,
            "candidates": array(CANDIDATE), "bindingId": nullable(ID["bind"]), "annotationStatus": ANNOTATION_STATUS})


def snapshot(value):
    obj({"snapshotId": ID["snap"], "libraryId": ID["lib"], "group": group, "filters": FILTERS,
         "total": lambda v: integer(v, 0, 10000), "offset": OFFSET, "limit": LIMIT,
         "items": array(ITEM, maximum=100), "truncated": boolean,
         "createdAt": integer, "expiresAt": integer})(value)
    _require(len(value["items"]) <= value["limit"] and
             len({i["itemId"] for i in value["items"]}) == len(value["items"]))
    _require(all(i["group"] == value["group"] for i in value["items"]))
    return value


def summary(value):
    obj({k: integer for k in ("images", "documents", "rows", "fields", "matchedOccurrences",
                              "changedOccurrences", "duplicateOccurrences", "ready", "skipped", "errors")})(value)
    _require(value["ready"] + value["skipped"] + value["errors"] == value["rows"])
    return value


def plan(value):
    obj({"planId": ID["plan"], "libraryId": ID["lib"], "requestId": ID["req"], "kind": enum("edit", "undo"),
         "status": enum("ready", "applying", "consumed", "expired", "blocked"), "scope": nullable(scope),
         "operation": lambda v: _require(isinstance(v, dict)), "summary": summary,
         "createdAt": integer, "expiresAt": integer, "manifestHash": digest})(value)
    if value["kind"] == "undo":
        _require(value["scope"] is None)
        obj({"op": enum("restore_job"), "jobId": ID["job"]})(value["operation"])
    else:
        scope(value["scope"])
        operation(value["operation"])
    return value


PLAN_ROW = obj({"documentId": nullable(ID["doc"]), "path": nullable(relative_path),
                "itemIds": array(ID["item"], 1, 1000, unique=True), "beforeHash": nullable(digest),
                "afterHash": nullable(digest), "beforeVersion": nullable(ID["ver"]),
                "bindingRevisions": array(obj({"bindingId": ID["bind"], "bindingRevision": POSITIVE})),
                "action": enum("create", "write", "delete", "noop"), "status": enum("ready", "skip", "error"),
                "code": nullable(CODE), "matchedOccurrences": integer, "changedOccurrences": integer,
                "duplicateOccurrences": integer,
                "diff": nullable(obj({"before": nullable(lambda v: text(v, 65536)),
                                      "after": nullable(lambda v: text(v, 65536))}))})

def field_name(value):
    text(value, 2048)
    if value.startswith("/") or value == "":
        pointer(value)
    else:
        _require(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,127}", value) is not None)
    return value


# Never forward arbitrary detail objects or upstream exception messages. These
# fields are sufficient for reconciliation, conflict display and limit notices.
DETAILS = {
    **{k: ID[p] for k, p in {"libraryId": "lib", "snapshotId": "snap", "itemId": "item", "documentId": "doc",
                            "bindingId": "bind", "planId": "plan", "jobId": "job", "requestId": "req",
                            "segmentId": "seg", "expectedVersion": "ver", "actualVersion": "ver",
                            "sourceVersion": "ver", "expectedSourceVersion": "ver"}.items()},
    **{k: integer for k in ("count", "limit", "maximum", "actual", "bytes", "images", "documents", "segments",
                            "offset", "total", "expectedRevision", "actualRevision", "bindingRevision",
                            "expectedBindingRevision", "actualBindingRevision", "generation")},
    "path": relative_path, "group": group, "field": field_name,
    "fields": array(field_name, maximum=100), "documentIds": array(ID["doc"], maximum=1000, unique=True),
}
ERROR_STATUSES = {
    400: {"INVALID_REQUEST", "INVALID_JSON", "UNSAFE_PATH"},
    403: {"AUTH_REQUIRED", "UNSAFE_STORAGE"}, 404: {"NOT_FOUND"}, 405: {"METHOD_NOT_ALLOWED"},
    409: {"BUSY", "UNSUPPORTED_CAPABILITY", "SNAPSHOT_TRUNCATED", "SNAPSHOT_CHANGED", "VERSION_CONFLICT",
          "BINDING_STALE", "AMBIGUOUS_SIDECAR", "IDEMPOTENCY_CONFLICT", "PLAN_CONSUMED", "RETAG_PROTECTED",
          "RECOVERY_REQUIRED", "DICTIONARY_MISSING", "SETTINGS_CHANGED", "TRANSLATION_STALE"},
    410: {"SNAPSHOT_EXPIRED", "PLAN_EXPIRED"},
    413: {"BODY_TOO_LARGE", "LIMIT_EXCEEDED", "DOCUMENT_TOO_LARGE"},
    422: {"INVALID_ENCODING", "INVALID_DOCUMENT", "DUPLICATE_KEY", "SCHEMA_REQUIRED", "SCHEMA_MISMATCH",
          "INVALID_OPERATION", "DICTIONARY_INVALID", "DICTIONARY_TABLE_REQUIRED"},
    429: {"JOB_LIMIT"}, 500: {"INTERNAL"},
    502: {"UPSTREAM_PROTOCOL", "TRANSLATION_PROTOCOL", "REMOTE_FAILED"},
    503: {"UPSTREAM_UNAVAILABLE"}, 504: {"UPSTREAM_TIMEOUT", "REMOTE_TIMEOUT"}, 507: {"STORAGE_FULL"},
}


def error_object(value):
    obj({"code": CODE, "message": lambda v: text(v, 1000, 1),
         "details": obj({}, DETAILS), "retryable": boolean})(value)
    return value


def job(value):
    obj({"jobId": ID["job"], "planId": nullable(ID["plan"]),
         "kind": enum("apply", "undo", "translation", "dictionary"),
         "status": enum("preparing", "running", "stopping", "done", "stopped", "error", "interrupted"),
         **{k: integer for k in ("total", "processed", "written", "skipped", "failed", "createdAt", "updatedAt")},
         "currentDocumentId": nullable(ID["doc"]), "cancelRequested": boolean,
         "finishedAt": nullable(integer), "error": nullable(error_object), "undoAvailable": boolean})(value)
    _require(value["processed"] == value["written"] + value["skipped"] + value["failed"] <= value["total"])
    _require((value["planId"] is None) == (value["kind"] == "dictionary"))
    if value["kind"] in {"translation", "dictionary"}:
        _require(value["undoAvailable"] is False)
    return value


def backup_id(value):
    text(value, 128)
    parts = value.split(":")
    _require(len(parts) == 2)
    ID["job"](parts[0])
    ID["doc"](parts[1])
    return value


JOB_ROW = obj({"documentId": nullable(ID["doc"]), "status": enum("written", "skipped", "failed", "uncertain"),
               "code": nullable(CODE), "beforeHash": nullable(digest), "afterHash": nullable(digest),
               "beforeVersion": nullable(ID["ver"]), "afterVersion": nullable(ID["ver"]),
               "backupId": nullable(backup_id)})


def page(id_key, row_validator):
    def validate(value):
        obj({id_key: ID["plan" if id_key == "planId" else "job"], "total": integer,
             "offset": OFFSET, "limit": LIMIT, "rows": array(row_validator, maximum=100)})(value)
        _require(len(value["rows"]) <= value["limit"])
        return value
    return validate


RESPONSES = {
    ("POST", "snapshots"): (201, snapshot), ("GET", "snapshots"): (200, snapshot),
    ("GET", "candidates"): (200, obj({"itemId": ID["item"], "candidates": array(CANDIDATE)})),
    ("POST", "bindings/confirm"): (200, obj({"bindingId": ID["bind"], "bindingRevision": POSITIVE, "document": document})),
    ("POST", "bindings/select"): (200, obj({"itemId": ID["item"], "bindingId": ID["bind"], "bindingRevision": POSITIVE})),
    ("GET", "documents"): (200, document), ("POST", "plans"): (201, plan), ("GET", "plans"): (200, plan),
    ("GET", "plans/rows"): (200, page("planId", PLAN_ROW)),
    ("POST", "plans/apply"): (202, obj({"job": job})), ("GET", "jobs"): (200, job),
    ("GET", "jobs/rows"): (200, page("jobId", JOB_ROW)),
    ("POST", "jobs/stop"): (200, obj({"job": job})), ("POST", "undo/plans"): (201, plan),
}


def validate_response(method, route, status, payload, request=None):
    try:
        _require(type(status) is int and isinstance(payload, dict))
        if status >= 400:
            obj({"ok": lambda v: _require(v is False), "error": error_object})(payload)
            _require(payload["error"]["code"] in ERROR_STATUSES.get(status, set()))
            err = payload["error"]
            # Codes/details are structural, messages are untrusted diagnostics.
            return status, AnnotationError(status, err["code"],
                                            "Annotation request failed: " + err["code"],
                                            err["details"], err["retryable"]).payload
        expected, check = RESPONSES[(method, route)]
        _require(status == expected)
        obj({"ok": lambda v: _require(v is True), "data": check})(payload)
        data = payload["data"]
        if request is not None:
            if method == "GET":
                for key in ("documentId", "snapshotId", "itemId", "planId", "jobId"):
                    if key in request and key in data:
                        _require(request[key] == data[key])
                if route == "documents" and "expectedVersion" in request:
                    _require(request["expectedVersion"] == data["sourceVersion"])
                if route in {"snapshots", "plans/rows", "jobs/rows"}:
                    _require(data["offset"] == int(request.get("offset", "0")))
                    if "limit" in request or route != "snapshots":
                        _require(data["limit"] == int(request.get("limit", "60")))
                if route == "plans/rows" and request.get("includeDiff", "false") == "false":
                    _require(all(row["diff"] is None for row in data["rows"]))
            elif route in {"plans", "undo/plans"}:
                _require(data["requestId"] == request["requestId"])
            elif route == "snapshots":
                _require(data["group"] == request["group"] and data["offset"] == 0 and
                         data["limit"] == request.get("limit", 60))
            elif route == "bindings/confirm":
                _require(data["document"]["documentId"] == request["documentId"])
            elif route == "bindings/select":
                _require(data["bindingId"] == request["bindingId"] and data["itemId"] == request["itemId"])
            elif route == "plans/apply":
                _require(data["job"]["planId"] == request["planId"])
            elif route == "jobs/stop":
                _require(data["job"]["jobId"] == request["jobId"])
        # Nested job errors use the same safe-message rule, not text substitution.
        result_job = data.get("job") if isinstance(data, dict) and "job" in data else data if route == "jobs" else None
        if isinstance(result_job, dict) and result_job.get("error"):
            result_job = dict(result_job)
            err = dict(result_job["error"])
            err["message"] = "Annotation job failed: " + err["code"]
            result_job["error"] = err
            payload = {"ok": True, "data": {"job": result_job} if "job" in data else result_job}
        return status, payload
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise AnnotationError(502, "UPSTREAM_PROTOCOL", "Invalid annotation response schema") from None


class AnnotationContract:
    def __init__(self, grabber):
        self.grabber = grabber

    def get(self, route, query):
        params, _ = validate_request("GET", route, query=query)
        status, payload = self.grabber.annotation_request("GET", route, query=query)
        return validate_response("GET", route, status, payload, params)

    def post(self, route, body):
        validate_request("POST", route, body=body)
        status, payload = self.grabber.annotation_request("POST", route, body=body)
        return validate_response("POST", route, status, payload, body)

    def document(self, document_id):
        status, payload = self.get("documents", {"documentId": [document_id]})
        if status != 200:
            error = payload["error"]
            raise AnnotationError(status, error["code"], error["message"], error["details"], error["retryable"])
        return payload["data"]

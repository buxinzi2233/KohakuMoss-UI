"""Small, strict GBC wire contract. No Python expressions cross this boundary."""

import hashlib
import json
import re

from .annotations import strict_json

VERSION = "gbc-ui-v1"
MAX_BODY = 1_000_000


class GbcError(Exception):
    def __init__(self, status, code, message, details=None, retryable=False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = {"apiVersion": VERSION, "ok": False, "error": {
            "code": code, "message": message, "details": details or {}, "retryable": retryable}}


def fail(code, message, status=400):
    raise GbcError(status, code, message)


def envelope(data):
    return {"apiVersion": VERSION, "ok": True, "data": data}


def digest(data):
    raw = json.dumps(data, ensure_ascii=True, allow_nan=False, sort_keys=True,
                     separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        fail("INVALID_REQUEST", "Missing or unknown fields")
    return value


def text(value, name="value", maximum=4096):
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        fail("INVALID_REQUEST", "Invalid " + name)
    return value


def integer(value, low=0, high=1_000_000):
    if type(value) is not int or not low <= value <= high:
        fail("INVALID_REQUEST", "Integer outside allowed range")
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        fail("INVALID_REQUEST", "Invalid resource or request identity")
    return value


def confirmed(value):
    if value is not True:
        fail("INVALID_REQUEST", "Explicit confirmation required")


POST = {
    "settings": ({"expectedRevision"}, {"roots", "runtimes"}),
    "runtimes/check": ({"runtimeId", "confirmExecution"}, {"checkMode"}),
    "tasks": ({"settingsRevision", "inputRootId", "relativePath", "outputRootId", "runtimeId", "mode"}, {"timeoutSeconds", "sourceKind", "models"}),
    "tasks/check": ({"taskId", "expectedRevision", "confirmExecution"}, set()),
    "jobs/start": ({"taskId", "expectedRevision", "manifestHash", "validationId", "confirmRun"}, set()),
    "jobs/stop": ({"jobId", "expectedRevision", "force"}, set()),
    "imports": ({"sourceRootId", "relativePath", "outputRootId", "dataRuntimeId", "confirm"}, set()),
    "datasets/validate": ({"datasetId", "expectedRevision", "dataRuntimeId", "confirm"}, set()),
    "exports": ({"artifactId", "outputRootId", "dataRuntimeId", "confirm"}, set()),
    "training-drafts": ({"datasetId", "datasetRevision", "shards", "baseConfigId", "captionOptions", "confirmExecution"}, {"knobPatch"}),
    "training-drafts/check": ({"draftRef", "confirmExecution"}, set()),
    "captions/preview": ({"draftRef", "sampleId"}, {"seed", "variant", "target"}),
}
GET = {
    "capabilities": set(), "settings": set(), "tasks": {"taskId"}, "jobs": {"jobId"},
    "jobs/logs": {"jobId", "cursor", "maxBytes"}, "requests": {"requestId"},
    "datasets": {"datasetId"}, "datasets/shards": {"datasetId", "offset", "limit"},
    "datasets/page": {"datasetId", "dataRuntimeId", "offset", "limit"},
    "graphs": {"artifactId", "offset", "limit"},
    "datasets/media": {"datasetId", "dataRuntimeId", "shard", "key", "size"},
    "graphs/media": {"artifactId", "sampleId", "size"},
    "training-drafts": {"draftId", "revision"},
}


def request(route, body):
    if route not in POST:
        fail("UNSUPPORTED_CAPABILITY", "Unsupported operation", 409)
    required, optional = POST[route]
    fields(body, required | {"apiVersion", "requestId"}, optional)
    if body["apiVersion"] != VERSION:
        fail("INVALID_REQUEST", "Unsupported API version")
    identifier(body["requestId"])
    # Shared strict JSON also rejects nonfinite floats, lone surrogates, depth.
    strict_json(json.dumps(body, allow_nan=False).encode())
    return {key: value for key, value in body.items() if key not in {"apiVersion", "requestId"}}

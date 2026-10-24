"""Private, offline-first translation service. No source or sidecar writes.

Only explicit jobs/start sends provider requests. Construction and unused close
are state-free. All persistent changes use a request WAL and store owner lock.
"""
import copy
import hashlib
import hmac
import secrets
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from ..store.annotations import AnnotationStore, StoreError
from . import annotations as a
from .lexicon import Lexicon, LexiconError
from . import translation_records as r
from . import translation_schema as s
from .translation_schema import TranslationError

ACTIVE = {"preparing", "running", "stopping"}


class TranslationService:
    def __init__(self, annotation_contract):
        self.annotations = annotation_contract
        self.store = AnnotationStore()
        self.lexicon = Lexicon(self.store)
        self._initialized = False
        self._closed = False
        self._threads = {}
        self._events = {}
        self._active = {}

    def _read(self, kind, key):
        env = self.store.read_record(kind, key)
        return s.persisted(kind, key, env["record"]) if env else None

    def _write(self, kind, key, value):
        env = self.store.read_record(kind, key)
        if env:
            s.persisted(kind, key, env["record"])
        revision = env["revision"] if env else 0
        value = copy.deepcopy(value)
        if kind == "translations":
            value["revision"] = revision + 1
        s.persisted(kind, key, value)
        return self.store.compare_write(kind, key, revision, value)["record"]

    def _initialize(self):
        with self.store.lock:
            if self._closed:
                s.fail("BUSY", 409, True)
            if self._initialized:
                if r.now() >= self._next_prune:
                    self._prune()
                return
            self.store.acquire_owner()
            # Validate the complete recovery input before applying any effect.
            requests = self.store.list_records("requests")
            jobs = self.store.list_records("translation-jobs")
            for env in requests:
                s.persisted("requests", env["record"].get("requestId"), env["record"])
            for env in jobs:
                s.persisted("translation-jobs", env["record"].get("job", {}).get("jobId"), env["record"])
            # Complete local effects only. Never replay an HTTP operation.
            for env in requests:
                wal = env["record"]
                if wal["state"] != "reserved":
                    continue
                try:
                    if wal.get("credentialChange"):
                        credentials = self.store.read_credentials() or {}
                        if credentials.get("lastRequestId") != wal["requestId"]:
                            s.fail("RECOVERY_REQUIRED", 409)
                    self._effects(wal["effects"])
                    wal.update(state="completed", httpStatus=wal["receiptStatus"], response=wal["receipt"])
                except (TranslationError, StoreError):
                    wal["state"] = "uncertain"
                self._write("requests", wal["requestId"], wal)
            for env in self.store.list_records("translation-jobs"):
                record = env["record"]
                if record["job"]["status"] in ACTIVE:
                    record["job"].update(status="interrupted", finishedAt=r.now(), updatedAt=r.now(),
                                          error=self._error("RECOVERY_REQUIRED"))
                    self._write("translation-jobs", record["job"]["jobId"], record)
            self._prune()
            self._initialized = True

    def _prune(self):
        """Compact terminal receipts/jobs, never replay or expire translations.

        Small job receipts remain exactly replayable. Receipts containing source
        text are replaced by a reconciliation tombstone, not by fabricated data.
        Legacy WALs with no trustworthy timestamp start their retention now.
        """
        cutoff = r.now() - 30 * 24 * 60 * 60
        for env in self.store.list_records("translation-jobs"):
            record = env["record"]
            job = record["job"]
            s.persisted("translation-jobs", job["jobId"], record)
            if (not record.get("tombstone") and job["status"] not in ACTIVE and
                    job["finishedAt"] is not None and job["finishedAt"] <= cutoff):
                self._write("translation-jobs", job["jobId"], {"job": job, "tombstone": True})
        for env in self.store.list_records("requests"):
            wal = env["record"]
            s.persisted("requests", wal["requestId"], wal)
            if wal.get("tombstone") or wal["state"] == "reserved":
                continue
            if "createdAt" not in wal:
                receipt = wal["receipt"]["data"]
                stamp = receipt.get("job", receipt).get("createdAt")
                wal["createdAt"] = stamp if type(stamp) is int else r.now()
                self._write("requests", wal["requestId"], wal)
            if wal["createdAt"] > cutoff:
                continue
            if wal["resourceId"].startswith("job_"):
                related = self._read("translation-jobs", wal["resourceId"])
                if related and related["job"]["status"] in ACTIVE:
                    continue
            compact = {k: wal[k] for k in ("requestId", "route", "digest", "state", "resourceId",
                                           "httpStatus", "response", "createdAt")}
            compact["tombstone"] = True
            if wal["route"] == "settings":
                compact["inputDigest"] = wal.get("inputDigest") or s.hash_value(wal["input"])
            # Only job receipts have no source/prompt/path-bearing payload.
            if not (wal["response"] and set(wal["response"]["data"]) == {"job"}):
                compact.update(httpStatus=None, response=None)
            self._write("requests", wal["requestId"], compact)
        self._next_prune = r.now() + 3600

    @staticmethod
    def _error(code):
        return {"code": code, "message": code.replace("_", " ").capitalize(), "details": {},
                "retryable": code in ("REMOTE_TIMEOUT", "BUSY", "JOB_LIMIT")}

    @classmethod
    def _exception_error(cls, exc):
        if isinstance(exc, (TranslationError, StoreError, LexiconError, a.AnnotationError)):
            try:
                error = exc.payload["error"]
                s.error_object(error)
                allowed = a.ERROR_STATUSES.get(exc.status, set())
                a._require(error["code"] in allowed or (exc.status == 422 and error["code"] == "LIMIT_EXCEEDED"))
                return {**cls._error(error["code"]), "details": copy.deepcopy(error["details"]),
                        "retryable": error["retryable"]}
            except (ValueError, TypeError, KeyError, UnicodeError):
                pass
        return cls._error("INTERNAL")

    def get(self, route, query):
        return self._call("GET", route, query)

    def post(self, route, body):
        return self._call("POST", route, body)

    def _call(self, method, route, value):
        try:
            value = s.validate(method, route, value)
            self._initialize()
            with self.store.lock:
                if self._closed:
                    s.fail("BUSY", 409, True)
                if method == "POST":
                    status, payload = self._post(route, value)
                else:
                    status, payload = 200, {"ok": True, "data": self._get(route, value)}
                return s.validate_response(method, route, status, payload)
        except (TranslationError, StoreError, LexiconError, a.AnnotationError) as exc:
            error = self._exception_error(exc)
            return (500 if error["code"] == "INTERNAL" else exc.status), {"ok": False, "error": error}
        except Exception:
            return 500, {"ok": False, "error": self._error("INTERNAL")}

    def _settings(self):
        saved = self._read("settings", "current") or {"configVersion": None, **s.DEFAULTS}
        credentials = self.store.read_credentials() or {}
        key = credentials.get("apiKey", "")
        return {**saved, "hasKey": bool(key), "keyHint": "configured" if key else ""}

    def _document(self, document_id):
        document = self.annotations.document(document_id)
        try:
            a.document(document)
            a._require(document["documentId"] == document_id)
        except (ValueError, TypeError, KeyError, UnicodeError):
            s.fail("UPSTREAM_PROTOCOL", 502)
        return document

    def _get(self, route, query):
        if route == "settings":
            return self._settings()
        if route == "dictionary":
            return self.lexicon.status()
        if route == "view":
            return self._view(query)
        kind = "translation-plans" if route.startswith("plans") else "translation-jobs"
        key = query["planId" if route.startswith("plans") else "jobId"]
        record = self._read(kind, key)
        if record is None:
            s.fail("NOT_FOUND", 404)
        if route == "jobs":
            return record["job"]
        if route == "plans":
            return self._plan_view(record)
        if record.get("tombstone"):
            s.fail("NOT_FOUND", 404)
        if route.startswith("plans"):
            self._live_plan(record)
        items = record["targets"] if route.startswith("plans") else record["segments"]
        offset, limit = query["offset"], query["limit"]
        return {"planId" if route.startswith("plans") else "jobId": key,
                "total": len(items), "offset": offset, "limit": limit, "segments": items[offset:offset + limit]}

    def _view(self, query):
        doc = self._document(query["documentId"])
        settings = self._settings()
        source, target = query.get("sourceLanguage", settings["sourceLanguage"]), query.get("targetLanguage", settings["targetLanguage"])
        record = self._read("translations", (doc["libraryId"], doc["documentId"]))
        snapshot = r.choose_snapshot(record, doc, source, target)
        manifest = self.lexicon.manifest()
        version = manifest["dictionaryVersion"] if manifest else None
        return dict(documentId=doc["documentId"], sourceLanguage=source, targetLanguage=target,
                    sourceVersion=doc["sourceVersion"], generation=doc["generation"],
                    revision=record["revision"] if record else 0,
                    snapshotRevision=snapshot["revision"] if snapshot else None,
                    snapshotSourceVersion=snapshot["sourceVersion"] if snapshot else None,
                    snapshotGeneration=snapshot["generation"] if snapshot else None,
                    segments=[r.display(seg, doc, settings, version) for seg in snapshot["segments"]] if snapshot else [],
                    historyCount=len(record["history"]) if record else 0, history=record["history"] if record else [])

    def _effect(self, kind, key, record):
        env = self.store.read_record(kind, key)
        revision = env["revision"] if env else 0
        record = copy.deepcopy(record)
        if env:
            s.persisted(kind, key, env["record"])
        if kind == "translations":
            record["revision"] = revision + 1
        s.persisted(kind, key, record)
        return {"kind": kind, "key": key, "beforeRevision": revision, "afterRevision": revision + 1, "record": record}

    def _effects(self, effects):
        for effect in effects:
            kind, key = effect["kind"], effect["key"]
            if kind == "translations":
                key = tuple(key)
            env = self.store.read_record(kind, key)
            if env:
                s.persisted(kind, key, env["record"])
            s.persisted(kind, key, effect["record"])
            revision = env["revision"] if env else 0
            if revision == effect["afterRevision"] and env["record"] == effect["record"]:
                continue
            if revision != effect["beforeRevision"]:
                s.fail("RECOVERY_REQUIRED", 409)
            self.store.compare_write(kind, key, revision, effect["record"])

    def _post(self, route, body):
        credentials = self.store.read_credentials() or {}
        normalized = {k: v for k, v in body.items() if k != "requestId"}
        if route == "settings":
            normalized = {**{k: v for k, v in self._settings().items() if k in s.SETTINGS}, **normalized}
            # Partial settings retries must use the originally normalized body.
            existing = self._read("requests", body["requestId"])
            if existing and existing.get("tombstone"):
                if existing["route"] != route or existing.get("inputDigest") != s.hash_value(body):
                    s.fail("IDEMPOTENCY_CONFLICT", 409)
                s.fail("RECOVERY_REQUIRED", 409)
            if existing and existing.get("input") == body:
                normalized = existing["normalized"]
        raw = s.canonical({"namespace": "translation", "route": route, "body": normalized})
        if route == "credentials":
            if "hmacSalt" not in credentials:
                credentials["hmacSalt"] = secrets.token_hex(32)
                self.store.write_credentials(credentials)
            digest = "hmac-sha256:" + hmac.new(bytes.fromhex(credentials["hmacSalt"]), raw, hashlib.sha256).hexdigest()
        else:
            digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        existing = self._read("requests", body["requestId"])
        if existing:
            if existing["digest"] != digest or existing["route"] != route:
                s.fail("IDEMPOTENCY_CONFLICT", 409)
            if existing["state"] == "completed":
                if existing["response"] is None:
                    s.fail("RECOVERY_REQUIRED", 409)
                return existing["httpStatus"], existing["response"]
            s.fail("RECOVERY_REQUIRED" if existing["state"] == "uncertain" else "BUSY", 409)
        resource = s.uid("plan" if route == "plans" else "job" if route.startswith("dictionary/") or route == "jobs/start" else "cfg")
        status, data, effects, launch, secret = self._prepare(route, body, resource)
        receipt = {"ok": True, "data": data}
        s.validate_response("POST", route, status, receipt)
        wal = dict(requestId=body["requestId"], route=route, digest=digest, state="reserved", resourceId=resource,
                   httpStatus=None, response=None, effects=effects, receiptStatus=status, receipt=receipt,
                   credentialChange=secret is not None, createdAt=r.now())
        if route == "settings":
            wal.update(input=body, normalized=normalized, inputDigest=s.hash_value(body))
        s.persisted("requests", body["requestId"], wal)
        self.store.compare_write("requests", body["requestId"], 0, wal)
        if secret is not None:
            secret["lastRequestId"] = body["requestId"]
            self.store.write_credentials(secret)
        self._effects(effects)
        wal.update(state="completed", httpStatus=status, response=receipt)
        self._write("requests", body["requestId"], wal)
        if launch:
            self._launch(*launch)
        return status, receipt

    def _prepare(self, route, body, resource):
        if route in ("settings", "credentials"):
            return self._prepare_settings(route, body, resource)
        if route == "plans":
            record = self._plan(body, resource)
            return 201, record["plan"], [self._effect("translation-plans", resource, record)], None, None
        if route == "manual":
            data, effects = self._manual(body)
            return 200, data, effects, None, None
        if route == "jobs/start":
            return self._start(body, resource)
        if route == "jobs/stop":
            record = self._read("translation-jobs", body["jobId"])
            if not record:
                s.fail("NOT_FOUND", 404)
            if record["job"]["status"] in ACTIVE:
                record["job"].update(status="stopping", cancelRequested=True, updatedAt=r.now())
            return 200, {"job": record["job"]}, [self._effect("translation-jobs", body["jobId"], record)], ("stop", body["jobId"]), None
        if "dictionary" in self._active:
            s.fail("JOB_LIMIT", 429, True)
        if route == "dictionary/import":
            if not body["path"].startswith("/"):
                s.fail("UNSAFE_PATH")
        else:
            # URL pinning is also enforced by Lexicon before any network IO.
            p = urllib.parse.urlsplit(body["downloadUrl"])
            allowed = "/ffdkj/ffdkj-Danbooru_Tag-Chinese-English-Translation-Table/"
            if (p.scheme != "https" or p.hostname not in ("github.com", "raw.githubusercontent.com") or
                    p.username or p.password or p.query or p.fragment or not p.path.startswith(allowed)):
                s.fail("DICTIONARY_INVALID", 422)
        if route == "dictionary/update":
            manifest = self.lexicon.manifest()
            if not manifest or manifest["dictionaryVersion"] != body["expectedDictionaryVersion"]:
                s.fail("VERSION_CONFLICT", 409)
        job = self._job(resource, None, "dictionary", 1)
        record = {"job": job, "segments": [], "request": {"route": route, "body": body}}
        return 202, {"job": job}, [self._effect("translation-jobs", resource, record)], ("dictionary", resource), None

    def _prepare_settings(self, route, body, resource):
        current = self._settings()
        if current["configVersion"] != body["expectedConfigVersion"]:
            s.fail("SETTINGS_CHANGED", 409)
        saved = {k: v for k, v in current.items() if k in s.SETTINGS or k == "configVersion"}
        secret = None
        changed = False
        credentials = self.store.read_credentials() or {}
        if route == "settings":
            saved.update({k: v for k, v in body.items() if k in s.SETTINGS})
            changed = saved != {k: v for k, v in current.items() if k in saved}
            if saved["enabled"] and (not saved["endpoint"] or not saved["model"]):
                s.fail("INVALID_REQUEST")
        else:
            key = "" if body["clear"] else body["apiKey"] or credentials.get("apiKey", "")
            if any(ord(c) < 32 or ord(c) == 127 for c in key):
                s.fail("INVALID_REQUEST")
            changed = key != credentials.get("apiKey", "") or not credentials.get("explicitKey", False)
            secret = {**credentials, "apiKey": key, "explicitKey": True}
        if changed:
            saved["configVersion"] = resource
        effects = [self._effect("settings", "current", saved)] if changed else []
        key = (secret if secret is not None else credentials).get("apiKey", "")
        masked = dict(configVersion=saved["configVersion"], hasKey=bool(key), keyHint="configured" if key else "")
        return 200, ({**saved, **masked} if route == "settings" else masked), effects, None, secret

    def _snapshot(self, snapshot_id, offset=0):
        status, payload = self.annotations.get("snapshots", {"snapshotId": [snapshot_id], "offset": [str(offset)]})
        if status != 200:
            error = payload["error"]
            raise TranslationError(status, error["code"], error["message"], error["details"], error["retryable"])
        snapshot = payload["data"]
        try:
            a.snapshot(snapshot)
        except (ValueError, TypeError, KeyError):
            s.fail("UPSTREAM_PROTOCOL", 502)
        if snapshot["truncated"]:
            s.fail("SNAPSHOT_TRUNCATED", 409)
        return snapshot

    def _resolve(self, body):
        scope = body["scope"]
        first = self._snapshot(scope["snapshotId"])
        if scope["mode"] == "all_filtered":
            if first["total"] > 50:
                s.fail("LIMIT_EXCEEDED", 413)
            items = list(first["items"])
            for offset in range(first["limit"], first["total"], first["limit"]):
                page = self._snapshot(scope["snapshotId"], offset)
                if any(page[k] != first[k] for k in ("libraryId", "total", "limit", "snapshotId")):
                    s.fail("SNAPSHOT_CHANGED", 409)
                items.extend(page["items"])
        else:
            offset = scope["pageOffset"]
            if offset % first["limit"]:
                s.fail("INVALID_REQUEST")
            items = (first if offset == 0 else self._snapshot(scope["snapshotId"], offset))["items"]
            if scope["mode"] == "page_selected":
                wanted = set(scope["itemIds"])
                if not wanted <= {item["itemId"] for item in items}:
                    s.fail("SNAPSHOT_CHANGED", 409)
                items = [item for item in items if item["itemId"] in wanted]
        if len(items) > 50:
            s.fail("LIMIT_EXCEEDED", 413)
        selections = {x["itemId"]: x for x in body.get("selections", [])}
        if "selections" in body and (len(selections) != len(body["selections"]) or set(selections) != {i["itemId"] for i in items}):
            s.fail("INVALID_REQUEST")
        docs, targets = {}, {}
        for item in items:
            if item["annotationStatus"] != "confirmed" or not item["bindingId"]:
                s.fail("BINDING_STALE", 409)
            candidates = [c for c in item["candidates"] if item["bindingId"] in c["bindingIds"]]
            if len(candidates) != 1:
                s.fail("SNAPSHOT_CHANGED", 409)
            candidate = candidates[0]
            did = candidate["documentId"]
            if did not in docs:
                docs[did] = self._document(did)
            doc = docs[did]
            if doc["libraryId"] != first["libraryId"]:
                s.fail("SNAPSHOT_CHANGED", 409)
            binding = next((b for b in doc["bindings"] if b["bindingId"] == item["bindingId"]), None)
            if not binding or item["itemId"] not in binding["itemIds"]:
                s.fail("SNAPSHOT_CHANGED", 409)
            selection = selections.get(item["itemId"])
            pointers = {f["pointer"] for f in binding["spec"]["fields"]}
            records = set(binding["recordIds"])
            if selection:
                if selection["bindingId"] != binding["bindingId"] or not set(selection["pointers"]) <= pointers or not set(selection["recordIds"]) <= records:
                    s.fail("SNAPSHOT_CHANGED", 409)
                pointers, records = set(selection["pointers"]), set(selection["recordIds"])
            code = "BINDING_STALE" if binding["status"] != "confirmed" or doc["readOnly"] else None
            if candidate["sourceVersion"] != doc["sourceVersion"] or candidate["sourceHash"] != doc["sourceHash"]:
                code = "VERSION_CONFLICT"
            stored = self._read("translations", (doc["libraryId"], did))
            snapshot = r.exact_snapshot(stored, doc, body["sourceLanguage"], body["targetLanguage"])
            for field in doc["fields"]:
                if field["recordId"] not in records or field["pointer"] not in pointers:
                    continue
                for seg in field["segments"]:
                    sid = seg["segmentId"]
                    if sid in targets:
                        if item["itemId"] not in targets[sid]["itemIds"]:
                            targets[sid]["itemIds"].append(item["itemId"])
                        continue
                    old = r.find_segment(snapshot, sid)
                    target = dict(documentId=did, itemIds=[item["itemId"]], segment=seg,
                                  expectedTranslationRevision=stored["revision"] if stored else 0,
                                  expectedSegmentRevision=old["segmentRevision"] if old else 0, status="ready", code=None)
                    if code or (body["engine"] == "dictionary" and seg["kind"] != "tags"):
                        target.update(status="error", code=code or "INVALID_OPERATION")
                    elif old and old["manual"] and not body["replaceManual"]:
                        target.update(status="skip", code="MANUAL_PROTECTED")
                    targets[sid] = target
        if len(docs) > 50:
            s.fail("LIMIT_EXCEEDED", 413)
        if "segmentIds" in body:
            if not set(body["segmentIds"]) <= set(targets):
                s.fail("INVALID_REQUEST")
            targets = {sid: targets[sid] for sid in body["segmentIds"]}
        return docs, list(targets.values())

    def _cache_key(self, plan, segment, settings):
        return s.hash_value([plan["engine"], segment["text"], plan["sourceLanguage"], plan["targetLanguage"],
                             plan["dictionaryVersion"] or plan["configVersion"], plan["model"],
                             settings["temperature"] if plan["engine"] == "api" else None, 1])

    def _plan(self, body, plan_id):
        settings = self._settings()
        manifest = self.lexicon.manifest()
        dictionary = None
        if body["engine"] == "dictionary":
            if not manifest:
                s.fail("DICTIONARY_MISSING", 409)
            if (body["sourceLanguage"], body["targetLanguage"]) != ("en", "zh-CN"):
                s.fail("INVALID_REQUEST")
            dictionary = manifest["dictionaryVersion"]
        else:
            credentials = self.store.read_credentials() or {}
            loopback = urllib.parse.urlsplit(settings["endpoint"]).hostname in ("localhost", "127.0.0.1", "::1")
            if not settings["enabled"] or not settings["model"] or not settings["endpoint"] or not (settings["hasKey"] or loopback and credentials.get("explicitKey")):
                s.fail("INVALID_REQUEST")
        docs, targets = self._resolve(body)
        characters = sum(len(t["segment"]["text"]) for t in targets)
        if len(targets) > 200 or characters > 32768:
            s.fail("LIMIT_EXCEEDED", 413)
        if body["engine"] == "api" and any(len(t["segment"]["text"]) > 4096 for t in targets):
            s.fail("LIMIT_EXCEEDED", 422)
        plan = dict(planId=plan_id, requestId=body["requestId"], status="blocked" if any(t["status"] == "error" for t in targets) else "ready",
                    engine=body["engine"], scope=body["scope"], sourceLanguage=body["sourceLanguage"], targetLanguage=body["targetLanguage"],
                    dictionaryVersion=dictionary, configVersion=settings["configVersion"] if body["engine"] == "api" else None,
                    model=settings["model"] if body["engine"] == "api" else None, replaceManual=body["replaceManual"],
                    targetSegments=len(targets), sourceCharacters=characters, cacheHits=0, remoteBatches=0,
                    maximumOutputTokens=0, manifestHash=None, createdAt=r.now(), expiresAt=r.now() + 600)
        cache = self._read("cache", "current") or {"entries": []}
        cached, batches, batch, chars, bytes_count = {}, [], [], 0, 0
        for target in targets:
            if target["status"] != "ready":
                continue
            seg = target["segment"]
            key = self._cache_key(plan, seg, settings)
            hit = next((e for e in cache["entries"] if e["keyHash"] == key), None)
            if hit and plan["engine"] == "dictionary":
                # Older builds could cache an unversioned override under a
                # pinned key. Verify local hits without changing the cache wire.
                pinned = self.lexicon.lookup(seg["text"], dictionary_version=plan["dictionaryVersion"])
                if not pinned or pinned["targetText"] != hit["result"]["targetText"]:
                    hit = None
            if hit:
                cached[seg["segmentId"]] = copy.deepcopy(hit["result"])
                plan["cacheHits"] += 1
            elif plan["engine"] == "api":
                size, encoded = len(seg["text"]), len(seg["text"].encode("utf-8"))
                if batch and (len(batch) == 20 or chars + size > 8192 or bytes_count + encoded > 32768):
                    batches.append(batch)
                    batch, chars, bytes_count = [], 0, 0
                batch.append(seg["segmentId"])
                chars += size
                bytes_count += encoded
        if batch:
            batches.append(batch)
        plan.update(remoteBatches=len(batches), maximumOutputTokens=len(batches) * settings["maxOutputTokens"])
        plan["manifestHash"] = s.hash_value({"plan": plan, "targets": targets, "cached": cached, "batches": batches})
        return {"plan": plan, "targets": targets, "documents": docs, "cached": cached, "batches": batches,
                "settings": {k: v for k, v in settings.items() if k not in ("hasKey", "keyHint")}}

    @staticmethod
    def _plan_view(record):
        plan = copy.deepcopy(record["plan"])
        if plan["status"] in ("ready", "blocked") and r.now() >= plan["expiresAt"]:
            plan["status"] = "expired"
        return plan

    def _live_plan(self, record):
        plan = self._plan_view(record)
        if plan["status"] == "expired":
            s.fail("PLAN_EXPIRED", 410)
        return plan

    @staticmethod
    def _job(job_id, plan_id, kind, total):
        return dict(jobId=job_id, planId=plan_id, kind=kind, status="preparing", total=total, processed=0,
                    written=0, skipped=0, failed=0, currentDocumentId=None, cancelRequested=False,
                    createdAt=r.now(), updatedAt=r.now(), finishedAt=None, error=None, undoAvailable=False)

    def _start(self, body, job_id):
        record = self._read("translation-plans", body["planId"])
        if not record:
            s.fail("NOT_FOUND", 404)
        plan = self._live_plan(record)
        if plan["status"] == "consumed":
            s.fail("PLAN_CONSUMED", 409)
        if plan["status"] != "ready":
            s.fail("INVALID_OPERATION", 422)
        if body["manifestHash"] != plan["manifestHash"]:
            s.fail("VERSION_CONFLICT", 409)
        remote = plan["engine"] == "api"
        if body["confirmRemote"] is not remote or body["confirmCost"] is not remote or body["acceptMaximumOutputTokens"] != plan["maximumOutputTokens"]:
            s.fail("INVALID_REQUEST")
        if remote and self._settings()["configVersion"] != plan["configVersion"]:
            s.fail("SETTINGS_CHANGED", 409)
        if "translation" in self._active:
            s.fail("JOB_LIMIT", 429, True)
        effects = []
        # Validate every switch first, then WAL all local effects before a worker.
        ready_docs = {t["documentId"] for t in record["targets"] if t["status"] == "ready"}
        for did in ready_docs:
            doc = self._document(did)
            frozen = record["documents"][did]
            if not r.identity(doc, frozen):
                continue  # Worker will emit SOURCE_CHANGED; never attach old IDs.
            current = self._read("translations", (doc["libraryId"], did))
            if current and r.pair(current, plan["sourceLanguage"], plan["targetLanguage"]) and r.identity(current, doc):
                continue
            expected = next(t["expectedTranslationRevision"] for t in record["targets"] if t["documentId"] == did)
            if (current["revision"] if current else 0) != expected:
                s.fail("VERSION_CONFLICT", 409)
            base = r.base_record(current, doc, plan["sourceLanguage"], plan["targetLanguage"])
            effects.append(self._effect("translations", (doc["libraryId"], did), base))
        record["plan"]["status"] = "consumed"
        record["jobId"] = job_id
        effects.append(self._effect("translation-plans", plan["planId"], record))
        job = self._job(job_id, plan["planId"], "translation", len(record["targets"]))
        effects.append(self._effect("translation-jobs", job_id, {"job": job, "segments": []}))
        return 202, {"job": job}, effects, ("translation", job_id), None

    def _manual(self, body):
        doc = self._document(body["documentId"])
        if body["expectedSourceVersion"] != doc["sourceVersion"]:
            s.fail("TRANSLATION_STALE", 409)
        segment = next((seg for f in doc["fields"] for seg in f["segments"] if seg["segmentId"] == body["segmentId"]), None)
        if not segment or doc["readOnly"]:
            s.fail("TRANSLATION_STALE", 409)
        key = (doc["libraryId"], doc["documentId"])
        current = self._read("translations", key)
        if (current["revision"] if current else 0) != body["expectedRevision"]:
            s.fail("VERSION_CONFLICT", 409)
        source, target = body["sourceLanguage"], body["targetLanguage"]
        switch = current is not None and (not r.pair(current, source, target) or not r.identity(current, doc))
        if switch and "translation" in self._active:
            job = self._read("translation-jobs", self._active["translation"])["job"]
            plan = self._read("translation-plans", job["planId"])
            if doc["documentId"] in {t["documentId"] for t in plan["targets"]}:
                s.fail("BUSY", 409, True)
        if current and not r.pair(current, source, target) and not body["switchLanguagePair"]:
            s.fail("VERSION_CONFLICT", 409)
        base = r.base_record(current, doc, source, target)
        result = r.make_segment(segment, source, target, "manual", body["targetText"])
        saved, changed = r.merge(base, result)
        effects = [self._effect("translations", key, base)] if changed or switch else []
        revision = effects[0]["afterRevision"] if effects else current["revision"]
        return {"revision": revision, "segment": {**saved, "code": None}}, effects

    def _launch(self, kind, job_id):
        if kind == "stop":
            event = self._events.get(job_id)
            if event:
                event.set()
            return
        event = threading.Event()
        self._events[job_id] = event
        self._active[kind] = job_id
        thread = threading.Thread(target=self._worker, args=(kind, job_id, event), daemon=True,
                                  name="translation-" + job_id)
        self._threads[job_id] = thread
        try:
            thread.start()
        except Exception:
            record = self._read("translation-jobs", job_id)
            record["job"].update(status="error", error=self._error("INTERNAL"), finishedAt=r.now())
            self._write("translation-jobs", job_id, record)
            self._active.pop(kind, None)
            self._threads.pop(job_id, None)
            self._events.pop(job_id, None)

    def _worker(self, kind, job_id, event):
        try:
            with self.store.lock:
                record = self._read("translation-jobs", job_id)
                record["job"]["status"] = "stopping" if event.is_set() else "running"
                self._write("translation-jobs", job_id, record)
            if kind == "dictionary":
                request = record["request"]
                body = request["body"]
                if not event.is_set():
                    if request["route"] == "dictionary/import":
                        self.lexicon.import_file(body["path"], table_name=body.get("tableName"), cancel=event)
                    else:
                        self.lexicon.install(body["downloadUrl"], body["ref"], body["expectedSha256"],
                                             table_name=body.get("tableName"), cancel=event,
                                             expected_version=body.get("expectedDictionaryVersion"))
                    with self.store.lock:
                        record = self._read("translation-jobs", job_id)
                        record["job"].update(processed=1, written=1)
                        self._write("translation-jobs", job_id, record)
            else:
                self._translate(job_id, event)
            with self.store.lock:
                record = self._read("translation-jobs", job_id)
                record["job"].update(status="stopped" if event.is_set() else "done", finishedAt=r.now(), updatedAt=r.now(), currentDocumentId=None)
                self._write("translation-jobs", job_id, record)
        except Exception as exc:
            with self.store.lock:
                try:
                    record = self._read("translation-jobs", job_id)
                    error = self._exception_error(exc)
                    record["job"].update(status="stopped" if event.is_set() else "error", finishedAt=r.now(), updatedAt=r.now(),
                                          error=None if event.is_set() else error, currentDocumentId=None)
                    self._write("translation-jobs", job_id, record)
                except Exception:
                    pass  # Durable active job is recovered as interrupted, never done.
        finally:
            with self.store.lock:
                self._active.pop(kind, None)
                self._events.pop(job_id, None)

    def _translate(self, job_id, event):
        with self.store.lock:
            job = self._read("translation-jobs", job_id)["job"]
            record = self._read("translation-plans", job["planId"])
            credentials = self.store.read_credentials() or {}
        plan = record["plan"]
        targets = {t["segment"]["segmentId"]: t for t in record["targets"]}
        for sid, target in targets.items():
            if event.is_set():
                break
            if target["status"] == "skip":
                self._result(job_id, record, target, None, target["code"], event)
            elif sid in record["cached"] and plan["engine"] == "api":
                self._result(job_id, record, target, record["cached"][sid]["targetText"], None, event)
            elif plan["engine"] == "dictionary":
                hit = self.lexicon.lookup(target["segment"]["text"], dictionary_version=plan["dictionaryVersion"])
                self._result(job_id, record, target, hit["targetText"] if hit else None, None if hit else "MISSING_TRANSLATION", event)
        if plan["engine"] != "api":
            return
        def execute(ids):
            if event.is_set():
                return []
            valid = []
            for sid in ids:
                target = targets[sid]
                doc = self._document(target["documentId"])
                if not r.identity(doc, target["segment"]):
                    self._result(job_id, record, target, None, "SOURCE_CHANGED", event)
                else:
                    valid.append(sid)
            if not valid or event.is_set():
                return []
            try:
                output = s.remote(record["settings"], credentials.get("apiKey", ""),
                                  [targets[sid]["segment"] for sid in valid], plan["sourceLanguage"], plan["targetLanguage"])
                return [(sid, output[sid], None) for sid in valid]
            except TranslationError as exc:
                return [(sid, None, exc.code) for sid in valid]
        # Submit at most concurrency tasks; no queued future can start after stop.
        with ThreadPoolExecutor(max_workers=record["settings"]["concurrency"]) as pool:
            batches = record["batches"]
            width = record["settings"]["concurrency"]
            for offset in range(0, len(batches), width):
                if event.is_set():
                    break
                futures = [pool.submit(execute, ids) for ids in batches[offset:offset + width]]
                for future in futures:
                    for sid, text, code in future.result():
                        self._result(job_id, record, targets[sid], text, code, event)

    def _result(self, job_id, plan_record, target, text, code, event):
        plan, segment = plan_record["plan"], target["segment"]
        doc = self._document(target["documentId"])
        with self.store.lock:
            key = (doc["libraryId"], doc["documentId"])
            current = self._read("translations", key)
            snapshot = r.exact_snapshot(current, segment, plan["sourceLanguage"], plan["targetLanguage"])
            old = r.find_segment(snapshot, segment["segmentId"])
            result = r.make_segment(segment, plan["sourceLanguage"], plan["targetLanguage"], plan["engine"], text,
                                    dictionary=plan["dictionaryVersion"], config=plan["configVersion"], model=plan["model"], code=code)
            count = "written"
            if event.is_set():
                code = "CANCELLED"
            elif not r.identity(doc, segment):
                code = "SOURCE_CHANGED"
            elif code == "MANUAL_PROTECTED" and old:
                pass  # A pre-skipped historical pair must not switch the active record.
            elif not current or not r.pair(current, plan["sourceLanguage"], plan["targetLanguage"]) or not r.identity(current, doc):
                code = "MANUAL_CONFLICT"
            elif code != "MANUAL_PROTECTED" and (old["segmentRevision"] if old else 0) != target["expectedSegmentRevision"]:
                code = "MANUAL_CONFLICT"
            if code in ("CANCELLED", "SOURCE_CHANGED", "MANUAL_CONFLICT", "MANUAL_PROTECTED"):
                count = "skipped"
                if old:
                    result = copy.deepcopy(old)
                else:
                    result["status"] = "stale" if code == "SOURCE_CHANGED" else "missing"
                    result["targetText"] = result["sourceText"]
                if code == "SOURCE_CHANGED":
                    result.update(status="stale", staleReason="source_changed")
            else:
                if code == "MISSING_TRANSLATION":
                    result["status"] = "missing"
                elif code:
                    result["status"] = "failed"
                    count = "failed"
                result, changed = r.merge(current, result)
                if changed:
                    self._write("translations", key, current)
                if not code:
                    self._cache_save(plan, segment, result, plan_record["settings"])
            result["code"] = code
            job_record = self._read("translation-jobs", job_id)
            job_record["segments"].append(result)
            job_record["job"]["processed"] += 1
            job_record["job"][count] += 1
            job_record["job"].update(updatedAt=r.now(), currentDocumentId=doc["documentId"])
            self._write("translation-jobs", job_id, job_record)

    def _cache_save(self, plan, segment, result, settings):
        if result["manual"] or result["status"] != "translated":
            return
        key = self._cache_key(plan, segment, settings)
        cache = self._read("cache", "current") or {"entries": []}
        value = {k: result[k] for k in ("engine", "targetText", "dictionaryVersion", "configVersion", "model", "sourceLanguage", "targetLanguage")}
        entries = [e for e in cache["entries"] if e["keyHash"] != key]
        entries.append({"keyHash": key, "result": value, "lastUsedAt": r.now()})
        entries.sort(key=lambda e: e["lastUsedAt"])
        while len(entries) > 10000 or len(s.canonical({"entries": entries})) > 10 * 1024 * 1024 - 128:
            entries.pop(0)
        self._write("cache", "current", {"entries": entries})

    def close(self):
        with self.store.lock:
            self._closed = True
            for event in self._events.values():
                event.set()
            threads = list(self._threads.values())
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join()
        self.store.close()

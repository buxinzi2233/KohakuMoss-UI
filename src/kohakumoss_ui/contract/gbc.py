"""GBC v1 orchestration: trusted runtimes, frozen inputs, explicit execution.

Only reviewed declarative replay and local Qwen3.5 profiles are enabled.
No API accepts Python source, arbitrary argv, engine factories or remote credentials.
"""

import copy
import hashlib
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

from ..store.gbc import GbcStore
from . import gbc_storage_files as dataset_files
from .annotations import strict_json
from .gbc_lease import Lease
from .gbc_paths import read_file, runtime_path, safe_path, within, write_new
from .gbc_process import ManagedProcess
from .gbc_schema import (
    GET,
    GbcError,
    confirmed,
    digest,
    envelope,
    fail,
    fields,
    identifier,
    integer,
    request,
    text,
)

ACTIVE = {"preparing", "running", "stopping"}


def new_id(prefix):
    return prefix + "_" + uuid.uuid4().hex


def public(value):
    """Internal paths and runtime identity never become graph/resource URLs."""
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


class GbcService:
    def __init__(self, root, state=None):
        self.root = root
        self.store = GbcStore(state)
        self.lock = threading.RLock()
        self.processes = {}
        self.closed = False
        self.db = None
        self._active_request = None
        self._drafts = None

    @property
    def drafts(self):
        self._load()
        if self._drafts is None:
            from .training_drafts import DraftService, register_launch_service
            service = DraftService(self.root, self.store.workdir("drafts"),
                                   dataset_resolver=self.resolve_dataset, coordination_lock=self.lock)
            register_launch_service(service)
            self._drafts = service
        return self._drafts

    def _draft_call(self, method, *args, **kwargs):
        from .training_drafts import DraftError
        try:
            result = getattr(self.drafts, method)(*args, **kwargs)
        except DraftError as exc:
            status = {"INVALID_REQUEST": 400, "UNSAFE_PATH": 403, "NOT_FOUND": 404,
                      "LIMIT_EXCEEDED": 413, "WORKER_TIMEOUT": 504,
                      "DEPENDENCY_MISSING": 503}.get(exc.code, 409)
            raise GbcError(status, exc.code, str(exc)) from None
        result = copy.deepcopy(result)
        result.pop("datasetSnapshot", None)
        # Keep the full typed knobs; dataset paths are server-only authorities.
        for entry in result.get("values", {}).get("DATASETS", []):
            entry.pop("path", None)
            entry["datasetId"] = result["datasetRef"]["datasetId"]
        return result

    def _reserve_resource(self, resource_id):
        if self._active_request:
            self.db["requests"][self._active_request]["resourceId"] = resource_id
            self._save()

    def _load(self):
        if self.closed:
            fail("WORKER_UNAVAILABLE", "Service is closing", 503)
        if self.db is not None:
            return
        self.store.acquire_owner()
        loaded = self.store.read()
        self.db = loaded if loaded is not None else {
            "settings": {"revision": 0, "roots": [], "runtimes": []},
            "tasks": {}, "jobs": {}, "datasets": {}, "artifacts": {}, "requests": {}}
        if (set(self.db) != {"settings", "tasks", "jobs", "datasets", "artifacts", "requests"}
                or any(not isinstance(value, dict) for value in self.db.values())):
            self.db = None
            fail("RECOVERY_REQUIRED", "Private GBC state schema is invalid", 409)
        if any(not isinstance(job, dict) or not {"jobId", "status", "kind", "revision"} <= set(job)
               for job in self.db["jobs"].values()):
            self.db = None
            fail("RECOVERY_REQUIRED", "Private job record is invalid", 409)
        for job in self.db["jobs"].values():
            if job["status"] in ACTIVE:
                job.update(status="recovery_required", error={"code": "RECOVERY_REQUIRED",
                           "message": "Previous process ownership is unconfirmed; no automatic resume or PID kill"})
                job["revision"] += 1
        self._save()

    def _save(self):
        self.store.write(self.db)

    def _find(self, kind, key):
        identifier(key)
        value = self.db[kind].get(key)
        if value is None:
            fail("NOT_FOUND", "Unknown resource", 404)
        return value

    @staticmethod
    def _cas(record, expected):
        integer(expected)
        if record["revision"] != expected:
            fail("VERSION_CONFLICT", "Resource revision changed", 409)

    def _root(self, key, purpose):
        for item in self.db["settings"]["roots"]:
            if item["rootId"] == key and item["purpose"] == purpose:
                path, fp = safe_path(item["path"])
                if fp != item["fingerprint"]:
                    fail("SOURCE_CHANGED", "Registered root identity changed", 409)
                return path
        fail("NOT_FOUND", "Root not registered for this purpose", 404)

    def _runtime(self, key, role):
        for item in self.db["settings"]["runtimes"]:
            if item["runtimeId"] == key and item["role"] == role:
                path, fp = runtime_path(item["python"])
                if fp != item["fingerprint"]:
                    fail("SOURCE_CHANGED", "Runtime executable changed", 409)
                return path
        fail("DEPENDENCY_MISSING", "Register an explicitly trusted runtime first", 503)

    def _output(self, root_id, name):
        base = self._root(root_id, "output")
        target = base / name
        for item in self.db["settings"]["roots"]:
            if item["purpose"] in {"input", "model"}:
                source = Path(item["path"])
                if target == source or source in target.parents or target in source.parents:
                    fail("UNSAFE_PATH", "Output overlaps an input root")
        runs = self.root.runs_dir.resolve()
        if target == runs or runs in target.parents or target in runs.parents:
            fail("UNSAFE_PATH", "Output overlaps training runs")
        if target.exists() or target.is_symlink():
            fail("SOURCE_CHANGED", "Output must be a new directory", 409)
        return target

    def get(self, route, query=None):
        with self.lock:
            self._load()
            query = query or {}
            if route not in GET:
                fail("UNSUPPORTED_CAPABILITY", "Unsupported read operation", 409)
            fields(query, (), GET[route])
            q = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in query.items()}
            for key in {"cursor", "offset", "limit", "maxBytes", "revision"} & set(q):
                if not str(q[key]).isascii() or not str(q[key]).isdigit():
                    fail("INVALID_REQUEST", "Pagination values must be nonnegative integers")
            if route == "capabilities":
                _ = self.drafts  # Wire the trusted launch adapter before advertising it.
                data = {"features": {"replay": sys.platform == "linux", "remoteApi": False,
                        "localModels": sys.platform == "linux", "captionPreview": True, "imports": True,
                        "exports": True, "trainingDrafts": True, "media": True},
                        "limits": {"bodyBytes": 1_000_000, "snapshotBytes": 268435456,
                                   "concurrentJobs": 1, "logBytes": 1048576,
                                   "datasetBytes": dataset_files.MAX_TOTAL, "datasetFileBytes": dataset_files.MAX_FILE,
                                   "datasetIoChunkBytes": dataset_files.CHUNK},
                        "supportedFormats": [{"format": "kohakugbc-dataset", "version": 1}],
                        "localProfiles": [{"id": "qwen35-local-v1", "supported": sys.platform == "linux",
                            "runtimeAvailable": None, "inferenceVerified": False,
                            "availabilityBasis": "Unknown until explicit task config check; successful check is not inference",
                            "modelSlots": {"vlm": "qwen3_5", "detect": "owlv2", "embed": "gemma3_text"},
                            "warning": "Official profile targets >=24 GiB, not verified for 8 GiB; no automatic download"}],
                        "limitations": ["Replay requires a registered installed GBC environment and recorded trace; not new-image inference",
                                        "Data imports/exports are synchronous bounded operations; failed requests are never replayed automatically"]}
            elif route == "settings":
                data = {"settings": self.db["settings"]}
            elif route == "training-drafts":
                revision = integer(int(q.get("revision", 1)), 1)
                data = {"draft": self._draft_call("get", q.get("draftId"), revision)}
            elif route in {"tasks", "jobs", "datasets"}:
                singular = {"tasks": "task", "jobs": "job", "datasets": "dataset"}[route]
                key = q.get(singular + "Id")
                if route == "jobs":
                    observed = [self._find(route, key)] if key else list(self.db["jobs"].values())[-100:]
                    for job in observed:
                        self._observe(job)
                data = {singular: public(self._find(route, key))} if key else {route: public(list(self.db[route].values())[-100:])}
            elif route == "requests":
                data = {"request": self._find("requests", q.get("requestId"))}
            elif route == "datasets/shards":
                shards = self._find("datasets", q.get("datasetId"))["shards"]
                offset = integer(int(q.get("offset", 0)), 0, 10000)
                limit = integer(int(q.get("limit", 100)), 1, 100)
                data = {"shards": shards[offset:offset + limit],
                        "nextOffset": offset + limit if offset + limit < len(shards) else None}
            elif route == "datasets/page":
                dataset = self.resolve_dataset(q.get("datasetId"), deep=False)
                data = self._data(q.get("dataRuntimeId"), {"action": "page", "path": dataset["path"],
                    "offset": integer(int(q.get("offset", 0)), 0, 10000),
                    "limit": integer(int(q.get("limit", 20)), 1, 100)})
                dataset_files.guard(dataset["path"], dataset["_identities"])
            elif route in {"datasets/media", "graphs/media"}:
                data = {"media": self._media(route, q)}
            elif route == "jobs/logs":
                job = self._find("jobs", q.get("jobId"))
                cursor = integer(int(q.get("cursor", 0)), 0, 1048576)
                maximum = integer(int(q.get("maxBytes", 65536)), 1, 65536)
                proc = self.processes.get(job["jobId"])
                if proc:
                    data = proc.logs(cursor, maximum)
                else:
                    raw = read_file(Path(job["_log"]), 1048576) if job.get("_log") else b""
                    part = raw[cursor:cursor + maximum]
                    data = {"text": part.decode("utf-8", "replace"), "nextCursor": cursor + len(part),
                            "dropped": job.get("logDropped", 0), "terminal": job["status"] not in ACTIVE}
            else:
                artifact = self._find("artifacts", q.get("artifactId"))
                runtime = next((r for r in self.db["settings"]["runtimes"] if r["role"] == "data"), None)
                if runtime is None:
                    fail("DEPENDENCY_MISSING", "Register a data runtime to preview graphs", 503)
                data = self._data(runtime["runtimeId"], {"action": "graph_page", "path": artifact["_graphs"],
                    "offset": integer(int(q.get("offset", 0)), 0, 268435456),
                    "limit": integer(int(q.get("limit", 20)), 1, 100)})
                mapping = {r["image"]: r["key"] for r in artifact["_records"]}
                for row in data["items"]:
                    image = row.pop("img_path", None)
                    if image not in mapping:
                        fail("INVALID_GRAPH", "Graph does not refer to a frozen input", 422)
                    row["sampleId"] = mapping[image]
                    row["artifactId"] = artifact["artifactId"]
            return 200, envelope(copy.deepcopy(data))

    def post(self, route, body):
        args = request(route, body)
        with self.lock:
            self._load()
            req_id = body["requestId"]
            hashed = digest({"route": route, "body": body})
            previous = self.db["requests"].get(req_id)
            if previous:
                if previous["hash"] != hashed:
                    fail("IDEMPOTENCY_CONFLICT", "requestId was used for another payload", 409)
                if previous["state"] != "completed":
                    fail("RECOVERY_REQUIRED", "Request outcome uncertain; reconcile existing resources", 409)
                return previous["status"], copy.deepcopy(previous["receipt"])
            if len(self.db["requests"]) >= 2000:
                fail("LIMIT_EXCEEDED", "Request ledger is full; archive this workspace locally", 413)
            intent = {"requestId": req_id, "hash": hashed, "state": "reserved"}
            self.db["requests"][req_id] = intent
            self._save()
            self._active_request = req_id
            try:
                result = self._post(route, args)
                status, receipt = 200, envelope(public(result))
            except GbcError as exc:
                status, receipt = exc.status, exc.payload
            except (OSError, ValueError, KeyError, TypeError) as exc:
                status = 400 if isinstance(exc, (ValueError, KeyError, TypeError)) else 503
                receipt = GbcError(status, "INVALID_REQUEST" if status == 400 else "WORKER_UNAVAILABLE",
                                   "Operation failed; inspect registered paths and runtime dependencies").payload
            finally:
                self._active_request = None
            intent.update(state="completed", status=status, receipt=receipt)
            self._save()
            return status, copy.deepcopy(receipt)

    def _post(self, route, args):
        if route == "training-drafts":
            confirmed(args["confirmExecution"])
            payload = {k: v for k, v in args.items() if k != "confirmExecution"}
            return {"draft": self._draft_call("create", payload)}
        if route == "training-drafts/check":
            confirmed(args["confirmExecution"])
            return {"check": self._draft_call("check", args["draftRef"])}
        if route == "captions/preview":
            return {"preview": self._draft_call("preview", args["draftRef"], sample_id=args["sampleId"],
                    seed=args.get("seed", 0), variant=args.get("variant", 0), target=args.get("target"))}
        if route == "settings":
            settings = self.db["settings"]
            self._cas(settings, args["expectedRevision"])
            new = copy.deepcopy(settings)
            for value in args.get("roots", []):
                fields(value, {"path", "purpose", "confirmTrust"})
                confirmed(value["confirmTrust"])
                if value["purpose"] not in {"input", "output", "model"}:
                    fail("INVALID_REQUEST", "Unsupported root purpose")
                path, fp = safe_path(value["path"])
                new["roots"].append({"rootId": new_id("root"), "path": str(path), "purpose": value["purpose"], "fingerprint": fp})
            for value in args.get("runtimes", []):
                fields(value, {"python", "role", "confirmExecute"})
                confirmed(value["confirmExecute"])
                if value["role"] not in {"generator", "data"}:
                    fail("INVALID_REQUEST", "Unsupported runtime role")
                path, fp = runtime_path(value["python"])
                if not os.access(path, os.X_OK):
                    fail("PERMISSION_DENIED", "Runtime must be executable", 403)
                new["runtimes"].append({"runtimeId": new_id("runtime"), "python": str(path), "role": value["role"], "fingerprint": fp})
            if len(new["roots"]) > 32 or len(new["runtimes"]) > 16:
                fail("LIMIT_EXCEEDED", "Too many registered paths", 413)
            new["revision"] += 1
            self.db["settings"] = new
            return {"settings": new}
        if route == "runtimes/check":
            confirmed(args["confirmExecution"])
            python = self._runtime(args["runtimeId"], "generator")
            mode = args.get("checkMode", "version")
            if mode not in {"version", "doctor"}:
                fail("INVALID_REQUEST", "checkMode must be version or doctor")
            return {"job": self._spawn(None, python, mode, 30)}
        if route == "tasks":
            return {"task": self._task(args)}
        if route in {"tasks/check", "jobs/start"}:
            task = self._find("tasks", args["taskId"])
            self._cas(task, args["expectedRevision"])
            confirmed(args["confirmExecution"] if route == "tasks/check" else args["confirmRun"])
            if route == "jobs/start" and (not task.get("validationId") or
                    args["manifestHash"] != task["inputManifestHash"] or
                    args["validationId"] != task.get("validationId")):
                fail("VERSION_CONFLICT", "A matching completed check is required", 409)
            self._verify_task(task)
            if route == "tasks/check":
                task["validationId"] = None
                task["runtimeAvailable"] = None
                task["revision"] += 1
            return {"job": self._spawn(task, self._runtime(task["runtimeId"], "generator"),
                                      "check" if route == "tasks/check" else "run",
                                      60 if route == "tasks/check" else task["timeoutSeconds"])}
        if route == "jobs/stop":
            job = self._find("jobs", args["jobId"])
            self._cas(job, args["expectedRevision"])
            if type(args["force"]) is not bool:
                fail("INVALID_REQUEST", "force must be a boolean")
            if job["status"] == "recovery_required":
                fail("RECOVERY_REQUIRED", "Cannot signal a process from another service lifetime", 409)
            if job["status"] not in ACTIVE:
                return {"job": job}
            job.update(status="stopping", cancelRequested=True, stopMethod="kill" if args["force"] else "term")
            job["revision"] += 1
            self._save()
            proc = self.processes.get(job["jobId"])
            if proc is None:
                fail("RECOVERY_REQUIRED", "No live process handle", 409)
            proc.stop(force=args["force"])
            return {"job": job}
        if route == "datasets/validate":
            confirmed(args["confirm"])
            dataset = self._find("datasets", args["datasetId"])
            self._cas(dataset, args["expectedRevision"])
            manifest, identities = dataset_files.snapshot(Path(dataset["_path"]))
            if digest(manifest) != dataset["manifestHash"]:
                fail("SOURCE_CHANGED", "Registered dataset content changed; import a new immutable registration", 409)
            result = self._data(args["dataRuntimeId"], {"action": "validate", "path": dataset["_path"]})
            revision = dataset["revision"] + 1
            checked = self._register_dataset(dataset["datasetId"], Path(dataset["_path"]), manifest, result, identities)
            checked["revision"] = revision
            return {"dataset": checked}
        if route == "imports":
            confirmed(args["confirm"])
            source = within(self._root(args["sourceRootId"], "input"), args["relativePath"])
            dataset_id = new_id("dataset")
            target = self._output(args["outputRootId"], dataset_id)
            self._reserve_resource(dataset_id)
            # Copy is a bounded frozen snapshot; the source never gets modified.
            manifest, identities = dataset_files.copy_dataset(source, target)
            result = self._data(args["dataRuntimeId"], {"action": "validate", "path": str(target)})
            return {"dataset": self._register_dataset(dataset_id, target, manifest, result, identities)}
        if route == "exports":
            confirmed(args["confirm"])
            artifact = self._find("artifacts", args["artifactId"])
            dataset_id = new_id("dataset")
            target = self._output(args["outputRootId"], dataset_id)
            self._reserve_resource(dataset_id)
            result = self._data(args["dataRuntimeId"], {"action": "pack", "graphs": artifact["_graphs"],
                "imageRoot": artifact["_imageRoot"], "output": str(target), "records": artifact["_records"],
                "perShard": 128, "shardBytes": 67108864})
            manifest, identities = dataset_files.snapshot(target)
            return {"dataset": self._register_dataset(dataset_id, target, manifest, result["inspection"], identities)}
        fail("UNSUPPORTED_CAPABILITY", "Unsupported operation", 409)

    def _media(self, route, q):
        from .gbc_data_common import flat_name

        size = q.get("size", "thumb")
        if not isinstance(size, str) or size not in {"thumb", "detail"}:
            fail("INVALID_REQUEST", "Unknown preview size")
        if route == "datasets/media":
            fields(q, {"datasetId", "dataRuntimeId", "shard", "key"}, {"size"})
            if not flat_name(q["shard"]) or not flat_name(q["key"]):
                fail("INVALID_REQUEST", "Invalid sample reference")
            dataset = self.resolve_dataset(q["datasetId"], deep=False)
            if q["shard"] not in {s["name"] for s in dataset["shards"]}:
                fail("NOT_FOUND", "Unknown registered shard", 404)
            result = self._data(q["dataRuntimeId"], {"action": "dataset_media", "path": dataset["path"],
                                "shard": q["shard"], "key": q["key"], "size": size})
            dataset_files.guard(dataset["path"], dataset["_identities"])
            return result
        fields(q, {"artifactId", "sampleId"}, {"size"})
        identifier(q["sampleId"])
        artifact = self._find("artifacts", q["artifactId"])
        matches = [r for r in artifact["_records"] if r["key"] == q["sampleId"]]
        if len(matches) != 1:
            fail("NOT_FOUND", "Unknown frozen sample", 404)
        record = matches[0]
        job = self._find("jobs", artifact["jobId"])
        task = self._find("tasks", job["taskId"])
        if record not in task["_records"]:
            fail("SOURCE_CHANGED", "Artifact mapping differs from frozen task", 409)
        image, before = safe_path(record["image"], directory=False)
        staging = Path(task["_staging"])
        if staging not in image.parents or image.parent != Path(artifact["_imageRoot"]):
            fail("UNSAFE_PATH", "Image is outside the frozen input mapping")
        relative = str(image.relative_to(staging))
        frozen = [r for r in task["_manifest"] if r["path"] == relative]
        if len(frozen) != 1:
            fail("SOURCE_CHANGED", "Frozen image manifest is missing", 409)
        identity = image.stat()
        runtime = next((r for r in self.db["settings"]["runtimes"] if r["role"] == "data"), None)
        if runtime is None:
            fail("DEPENDENCY_MISSING", "Register a data runtime to preview images", 503)
        result = self._data(runtime["runtimeId"], {"action": "frozen_media", "path": str(image),
                            "hash": frozen[0]["hash"], "size": size})
        _, after = safe_path(image, directory=False)
        if before != after or identity.st_ctime_ns != image.stat().st_ctime_ns:
            fail("SOURCE_CHANGED", "Frozen image changed during preview", 409)
        return result

    def _data(self, runtime_id, payload):
        from .gbc_data import DataError, call
        media = payload.get("action") in {"dataset_media", "frozen_media"}
        try:
            return call(self._runtime(runtime_id, "data"), payload, timeout=30 if media else 120)
        except DataError as exc:
            if media:
                code, status = {"missing_dependency": ("DEPENDENCY_MISSING", 503),
                                "timeout": ("WORKER_TIMEOUT", 504), "unsafe_path": ("UNSAFE_PATH", 400),
                                "source_changed": ("SOURCE_CHANGED", 409), "not_found": ("NOT_FOUND", 404),
                                "limit_exceeded": ("LIMIT_EXCEEDED", 413)}.get(exc.code, ("VALIDATION_FAILED", 422))
            else:
                code = {"missing_dependency": "DEPENDENCY_MISSING", "timeout": "WORKER_TIMEOUT",
                        "unsafe_path": "UNSAFE_PATH"}.get(exc.code, "VALIDATION_FAILED")
                status = 503 if code == "DEPENDENCY_MISSING" else 422
            raise GbcError(status, code, "Data worker rejected this operation", {"workerCode": exc.code}) from None

    def _register_dataset(self, key, path, manifest, result, identities):
        if not result.get("complete") or result.get("validatedSamples") is None:
            fail("VALIDATION_FAILED", "Dataset validation was incomplete", 422)
        dataset_files.guard(path, identities)
        dataset = {"datasetId": key, "revision": 1, "manifestHash": digest(manifest),
                   "format": "kohakugbc-dataset", "formatVersion": 1, **result,
                   "validation": {"state": "valid", "complete": True},
                   "_path": str(path), "_manifest": manifest, "_identities": identities}
        self.db["datasets"][key] = dataset
        return dataset

    def resolve_dataset(self, key, *, deep=False):
        with self.lock:
            self._load()
            dataset = self._find("datasets", key)
            if deep:
                manifest, _ = dataset_files.snapshot(Path(dataset["_path"]))
                if digest(manifest) != dataset["manifestHash"]:
                    fail("SOURCE_CHANGED", "Dataset changed since validation", 409)
            else:
                dataset_files.guard(dataset["_path"], dataset.get("_identities"))
            return {**dataset, "path": dataset["_path"]}

    @staticmethod
    def _tree_manifest(path):
        records = []
        total = 0
        for base, dirs, files in os.walk(safe_path(path)[0], followlinks=False):
            for name in dirs:
                safe_path(Path(base) / name)
            for name in files:
                target = Path(base) / name
                data = read_file(target)
                total += len(data)
                if total > 268435456 or len(records) >= 10000:
                    fail("LIMIT_EXCEEDED", "Snapshot exceeds v1 bounds", 413)
                records.append({"path": str(target.relative_to(path)), "hash": "sha256:" + hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        return sorted(records, key=lambda r: r["path"])

    def _task(self, args):
        self._cas(self.db["settings"], args["settingsRevision"])
        if args["mode"] == "qwen35-local-v1":
            from .gbc_profiles import create_local
            self._runtime(args["runtimeId"], "generator")
            self._root(args["outputRootId"], "output")
            if "models" not in args:
                fail("INVALID_REQUEST", "Local profile requires three registered model references")
            key = new_id("task")
            self._reserve_resource(key)
            return create_local(self, args, key)
        if args["mode"] != "replay":
            fail("UNSUPPORTED_CAPABILITY", "Unknown local profile; remote API is unsupported", 409)
        if "models" in args or "sourceKind" in args:
            fail("INVALID_REQUEST", "Replay does not accept local profile fields")
        self._runtime(args["runtimeId"], "generator")
        source_root = self._root(args["inputRootId"], "input")
        trace = within(source_root, args["relativePath"])
        self._root(args["outputRootId"], "output")
        timeout = integer(args.get("timeoutSeconds", 600), 1, 86400)
        key = new_id("task")
        self._reserve_resource(key)
        staging = self.store.workdir(key)
        frozen = staging / "trace"
        frozen.mkdir(mode=0o700)
        images = staging / "images"
        images.mkdir(mode=0o700)
        meta = strict_json(read_file(trace / "meta.json", 1_000_000))
        if not isinstance(meta, dict) or meta.get("arm") not in {"text", "tags", "query", "image_text", "batch4", "leafcap"}:
            fail("INVALID_REQUEST", "Unsupported trace arm")
        jobs_raw = read_file(trace / "jobs.jsonl", 16 * 1024 * 1024)
        jobs = [strict_json(line) for line in jobs_raw.splitlines() if line.strip()]
        if not jobs or len(jobs) > 1000:
            fail("LIMIT_EXCEEDED", "Replay requires 1..1000 frozen jobs", 413)
        seen = set()
        records = []
        snapshot_bytes = len(jobs_raw)
        for index, job in enumerate(jobs):
            original_key = text(job.get("key"), "trace key")
            if original_key in seen:
                fail("INVALID_REQUEST", "Duplicate trace job key")
            seen.add(original_key)
            image = text(job.get("image"), "trace image")
            target = Path(os.path.normpath(str(trace / str(meta.get("image_root", ".")) / image)))
            if target == source_root or source_root not in target.parents:
                fail("UNSAFE_PATH", "Trace image escapes its authorized root")
            data = read_file(target, 32 * 1024 * 1024)
            snapshot_bytes += len(data)
            if snapshot_bytes > 268435456:
                fail("LIMIT_EXCEEDED", "Frozen input exceeds 256 MiB", 413)
            suffix = target.suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                fail("INVALID_REQUEST", "Unsupported image format")
            output = images / (str(index) + suffix)
            write_new(output, data)
            job["image"] = output.name
            records.append({"key": "sample_" + hashlib.sha256(original_key.encode()).hexdigest()[:32],
                            "image": str(output), "meta": {"sourceKey": original_key}})
        write_new(frozen / "meta.json", json.dumps({"arm": meta["arm"], "image_root": str(images)}).encode())
        write_new(frozen / "jobs.jsonl", ("\n".join(json.dumps(j, ensure_ascii=True) for j in jobs) + "\n").encode())
        for name in ("vlm", "detect", "embed", "same"):
            data = read_file(trace / (name + ".jsonl"), 32 * 1024 * 1024)
            snapshot_bytes += len(data)
            if snapshot_bytes > 268435456:
                fail("LIMIT_EXCEEDED", "Frozen input exceeds 256 MiB", 413)
            for line in data.splitlines():
                if line.strip():
                    strict_json(line)
            write_new(frozen / (name + ".jsonl"), data)
        manifest = self._tree_manifest(staging)
        task = {"taskId": key, "revision": 1, "mode": "replay", "runtimeId": args["runtimeId"],
                "outputRootId": args["outputRootId"], "inputManifestHash": digest(manifest),
                "total": len(jobs), "timeoutSeconds": timeout, "validationId": None,
                "_staging": str(staging), "_trace": str(frozen), "_records": records, "_manifest": manifest}
        self.db["tasks"][key] = task
        return task

    def _verify_task(self, task):
        manifest = self._tree_manifest(Path(task["_staging"]))
        if task["mode"] == "qwen35-local-v1":
            from .gbc_profiles import model_snapshot
            models = {slot: model_snapshot(Path(ref["path"]), slot)
                      for slot, ref in task["_models"].items()}
            actual = digest({"files": manifest, "models": models})
        else:
            actual = digest(manifest)
        if actual != task["inputManifestHash"]:
            fail("SOURCE_CHANGED", "Frozen task input or local models changed", 409)

    def _spawn(self, task, python, action, timeout):
        if sys.platform != "linux":
            fail("UNSUPPORTED_CAPABILITY", "Process supervision requires Linux", 409)
        if any(j["status"] in ACTIVE | {"recovery_required"} for j in self.db["jobs"].values()):
            fail("BUSY", "A generation job is active or requires recovery", 409)
        from . import state
        for name in state.all_entries():
            training = state.status(name) or {}
            if training.get("recoveryRequired"):
                fail("RECOVERY_REQUIRED", "A training process identity requires manual recovery", 409)
            if training.get("alive"):
                fail("BUSY", "A managed training process is alive", 409)
        key = new_id("job")
        self._reserve_resource(key)
        lease = Lease(key, self.store._state)
        job = {"jobId": key, "taskId": task["taskId"] if task else None,
               "kind": "generate" if action == "run" else "check", "revision": 1,
               "status": "preparing", "cancelRequested": False, "stopMethod": None,
               "exitCode": None, "resultArtifactId": None, "error": None,
               "counts": {"total": task["total"] if task else 1, "ok": 0, "failed": 0,
                          "observedRecords": 0, "invalidRecords": 0, "validated": None},
               "createdAt": int(time.time() * 1000), "_action": action}
        self.db["jobs"][key] = job
        proc = None
        try:
            self._save()
            work = self.store.workdir(key)
            output = self._output(task["outputRootId"], key) if task else work / "probe"
            output.mkdir(mode=0o700)
            config = None
            if task:
                trace = task.get("_trace")
                values = {"engines": {"vlm": {"entry": "replay", "trace": trace, "capacity": 4},
                          "detect": {"entry": "replay", "trace": trace, "capacity": 1},
                          "embed": {"entry": "replay", "trace": trace}, "cpu": {"entry": "python", "workers": 2}},
                          "source": {"kind": "trace", "path": trace},
                          "sink": {"kind": "graphs", "path": str(output / "graphs.jsonl")},
                          "machine": {"vlm_waiting": 4, "max_jobs": 8}}
                if task["mode"] == "qwen35-local-v1":
                    from .gbc_profiles import local_config
                    values = local_config(task, output)
                config = work / "config.py"
                # repr of validated builtin data is a literal, never user code.
                write_new(config, ("\n".join(k + " = " + ascii(v) for k, v in values.items()) + "\n").encode("ascii"))
            job.update(_output=str(output), _log=str(work / "stdout.log"))
            proc = ManagedProcess(python, config, action, work, timeout, job["_log"], lease,
                                  lambda process, clean: self._finished(key, process, clean), output=output)
            self.processes[key] = proc
            job.update(status="running", _pid=proc.process.pid, _identity=proc.identity)
            proc.start_monitor()
            self._save()
            return job
        except BaseException:
            if proc is not None:
                proc.close()
            else:
                lease.release()
            job.update(status="failed", error={"code": "PROCESS_START_FAILED", "message": "Could not start registered runtime"})
            self._save()
            raise

    def _observe(self, job):
        if job["kind"] == "generate" and job.get("_output"):
            from .gbc_observe import observe
            records = self.db["tasks"][job["taskId"]]["_records"]
            job["counts"] = observe(job["_output"], records, terminal=job["status"] not in ACTIVE)
            job["countBasis"] = "frozen-input+file-observation"
            job["observedAt"] = int(time.time() * 1000)

    def _finished(self, key, proc, clean):
        with self.lock:
            job = self.db["jobs"][key]
            job.update(exitCode=proc.exit_code, logDropped=proc.dropped,
                       finishedAt=int(time.time() * 1000))
            job["revision"] += 1
            if not clean:
                job["status"] = "recovery_required"
            elif job["cancelRequested"]:
                job["status"] = "stopped"
            elif proc.exit_code != 0:
                log = proc.logs()["text"]
                missing = "No module named" in log or "ModuleNotFoundError" in log
                timed_out = time.monotonic() - proc.started >= proc.timeout
                code = "DEPENDENCY_MISSING" if missing else "WORKER_TIMEOUT" if timed_out else "WORKER_FAILED"
                if job["kind"] == "check" and job["taskId"] and missing:
                    self.db["tasks"][job["taskId"]]["runtimeAvailable"] = False
                job.update(status="failed", error={"code": code,
                           "message": "Registered runtime needs dependencies or failed; inspect bounded logs; no install attempted"})
            elif job["kind"] == "check":
                job["status"] = "done"
                if job["taskId"]:
                    task = self.db["tasks"][job["taskId"]]
                    task["validationId"] = new_id("validation")
                    task["runtimeAvailable"] = True
                    task["runtimeAvailabilityBasis"] = "config-check-only; inference unverified"
                    task["revision"] += 1
                    job["validationId"] = task["validationId"]
            else:
                # Exit zero is not proof of semantic completeness or image coverage.
                job["status"] = "partial"
            if job["kind"] == "generate":
                task = self.db["tasks"][job["taskId"]]
                graphs = Path(job["_output"]) / "graphs.jsonl"
                if graphs.is_file() and not graphs.is_symlink():
                    artifact_id = new_id("artifact")
                    self.db["artifacts"][artifact_id] = {"artifactId": artifact_id, "revision": 1,
                        "jobId": key, "state": "unchecked", "_graphs": str(graphs),
                        "_records": task["_records"], "_imageRoot": str(Path(task["_staging"]) / "images")}
                    job["resultArtifactId"] = artifact_id
            self._observe(job)
            self._save()

    def close(self):
        with self.lock:
            self.closed = True
            processes = list(self.processes.values())
        for proc in processes:
            proc.close()
        if self._drafts is not None:
            self._drafts.close()
        self.store.close()

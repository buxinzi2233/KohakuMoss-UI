"""Typed, immutable GBC training drafts and checked effective config references.

Only this contract resolves a draftRef to a private file; never accept such a file
via the legacy config-path API. No trainer imports or training occur here.
"""

import ast
import copy
import re
import threading
import uuid
from pathlib import Path

from .root import TrainerRoot
from .training_drafts_io import DraftStore, digest, file_hash, worker
from .training_drafts_schema import (
    DraftError,
    canonical,
    caption,
    fail,
    geometry,
    integer,
    no_dynamic_imports,
    obj,
    strict_json,
    strings,
    text,
)

ID = re.compile(r"draft-[0-9a-f]{32}\Z")
HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
SHARD = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
SELECTORS = {
    "RESOLUTION_MODE": {"resize", "bucket", "square_crop"},
    "LOSS": {"mse", "huber", "l1", "masked_mse"},
    "TIMESTEP": {"moss", "uniform", "logit_normal"},
    "OPTIMIZER": {
        "adamw",
        "adamw16",
        "adamw8bit",
        "adamw8bit_kernel",
        "muon",
        "prodigy",
        "schedule_free",
    },
    "WEIGHTING": {"none", "x0", "eps"},
    "PARALLEL": {"none", "ddp", "fsdp", "pipeline", "threaded"},
    "DRIVER": {"native", "lightning"},
    "LOGGER": {"console"},
    "BATCHING": {"tokens", "tokens_ffd", "count"},
    "ALGO": {
        "lora",
        "locon",
        "loha",
        "lokr",
        "oft",
        "boft",
        "glora",
        "ia3",
        "dylora",
        "tlora",
        "full",
    },
}
# Keys whose empty/None defaults do not describe a safe input type. Fail closed
# on the remaining polymorphic extension knobs rather than accept code factories.
OPTIONAL_TYPES = {
    "METRICS_EVERY": int,
    "STREAM_RESIDENT": int,
    "CHECKPOINT": str,
    "STREAM": str,
    "OPTIM_STATE": str,
    "BASE_PRECISION": str,
    "FP8_RECIPE": str,
    "FP8_DGRAD": str,
    "INT8_DGRAD": str,
    "PINNED": bool,
    "ACT_OFFLOAD": bool,
    "GRAD_OFFLOAD": bool,
    "FUSED_STEP": bool,
    "INT8_COMPUTE": bool,
}


def _same_type(value, base, key):
    if base is None:
        if value is not None and type(value) is not OPTIONAL_TYPES.get(key):
            fail("Unsupported optional knob type: " + key)
    elif type(base) is float:
        if type(value) not in (int, float):
            fail("Expected numeric knob: " + key)
    elif type(value) is not type(base):
        fail("Wrong knob type: " + key)
    elif type(base) is dict:
        if set(value) - set(base):
            fail("Unknown nested knob fields: " + key)
        for k, v in value.items():
            _same_type(v, base[k], key + "." + k)
    elif type(base) is list:
        if base:
            for v in value:
                _same_type(v, base[0], key)
        elif value:
            # Known variable-length primitive lists only; no factory dictionaries.
            expected = (
                int
                if key in {"ENCODE_DEVICES", "TRACE_STEPS", "PROFILE_STEPS"}
                else str
                if key == "SAMPLE_PROMPTS"
                else None
            )
            if expected is None or any(type(v) is not expected for v in value):
                fail("Unsupported list knob: " + key)


def render_effective(values):
    """Python source consists solely of a fixed docstring and JSON-derived literals."""
    values = strict_json(canonical(values))
    no_dynamic_imports(values)
    for key, allowed in SELECTORS.items():
        if key in values and values[key] not in allowed:
            fail("Unregistered effective knob choice: " + key)
    lines = ['"""Managed GBC draft for scripts/train/moss.py."""']
    for key, value in sorted(values.items()):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            fail("Invalid knob name")
        literal = ascii(value)
        if ast.literal_eval(literal) != value:
            fail("Value cannot round-trip as a literal")
        lines.append(key + " = " + literal)
    return ("\n".join(lines) + "\n").encode("ascii")


_LAUNCH_SERVICES = {}


def register_launch_service(service):
    """Explicit trusted service wiring for launch.start's draft_ref branch."""
    key = str(service.root.root.resolve())
    existing = _LAUNCH_SERVICES.get(key)
    if existing is not None and existing is not service:
        fail("A draft launch service is already registered for this trainer", "BUSY")
    _LAUNCH_SERVICES[key] = service


def resolve_for_launch(root, draft_ref):
    """Return the exact checked Path; never derive state paths from client input."""
    service = _LAUNCH_SERVICES.get(str(root.root.resolve()))
    if service is None:
        fail("Draft launch service has not been wired", "UNSUPPORTED_CAPABILITY")
    return Path(service.resolve_launch(draft_ref)["config"])


class DraftService:
    """dataset_resolver is trusted service code, NEVER a client-supplied record.

    It must return only authorized, fully validated registrations, and re-read
    current state each call. Records require datasetId/revision/manifestHash/path/
    shards. This layer additionally hashes all four files of selected shards.
    """

    def __init__(
        self, root, state_dir, *, dataset_resolver, trusted_configs=None, python=None,
        coordination_lock=None,
    ):
        self.root = (
            root
            if isinstance(root, TrainerRoot)
            else TrainerRoot(Path(root).absolute())
        )
        self.python = Path(python or self.root.python).absolute()
        self.dataset_resolver = dataset_resolver
        # Every public store transaction, including direct/registered launch,
        # acquires the owning service lock BEFORE the draft store lock.
        self._coordination_lock = coordination_lock if coordination_lock is not None else threading.RLock()
        self.store = DraftStore(state_dir)
        if trusted_configs is None:
            trusted_configs = [
                p.relative_to(self.root.root).as_posix()
                for group in ("moss", "user")
                for p in (self.root.root / "configs" / group).rglob("*.py")
                if "__pycache__" not in p.parts
            ]
        self.trusted_configs = frozenset(trusted_configs)
        for name in self.trusted_configs:
            self._base(name)

    def close(self):
        with self._coordination_lock:
            key = str(self.root.root.resolve())
            if _LAUNCH_SERVICES.get(key) is self:
                del _LAUNCH_SERVICES[key]
            self.store.close()

    def _base(self, config_id):
        text(config_id, "baseConfigId")
        path = Path(config_id)
        if (
            config_id not in self.trusted_configs
            or path.is_absolute()
            or ".." in path.parts
            or path.parts[0] != "configs"
            or path.suffix != ".py"
        ):
            fail("Base config is not allowlisted under root configs", "UNSAFE_PATH")
        full = self.root.root / path
        file_hash(full, 4 * 1024 * 1024)
        return full

    def _sources(self):
        files = {}
        # Snapshot is flattened; conservatively invalidate any local config or
        # trainer implementation change, including changes in use_config layers.
        for directory in ("configs", "src", "scripts"):
            for path in sorted((self.root.root / directory).rglob("*.py")):
                if "__pycache__" not in path.parts:
                    files[path.relative_to(self.root.root).as_posix()] = file_hash(
                        path, 4 * 1024 * 1024
                    )
                    if len(files) > 20000:
                        fail("Trainer source count limit", "LIMIT_EXCEEDED")
        files["@python"] = file_hash(self.python.resolve(), 128 * 1024 * 1024)
        for path in sorted(Path(__file__).parent.glob("training_drafts*.py")):
            files["@adapter/" + path.name] = file_hash(path, 4 * 1024 * 1024)
        # Installed distributions: content, not just mtime; editable trainer code
        # is already covered above. Native binaries are covered by wheel RECORD.
        for lib in (self.root.root / ".venv" / "lib").glob("python*/site-packages"):
            for path in sorted(lib.glob("*.dist-info/RECORD")):
                files["@runtime/" + path.parent.name] = file_hash(
                    path, 16 * 1024 * 1024
                )
        return digest(canonical(files))

    def _dataset(self, dataset_id, revision, shards):
        text(dataset_id, "datasetId")
        integer(revision, "datasetRevision", 1)
        strings(shards, "shards", True)
        if (
            len(shards) > 256
            or len(shards) != len(set(shards))
            or any(not SHARD.fullmatch(s) for s in shards)
        ):
            fail("Invalid or duplicate shard names")
        record = copy.deepcopy(self.dataset_resolver(dataset_id))
        if (
            type(record) is not dict
            or record.get("datasetId") != dataset_id
            or record.get("revision") != revision
        ):
            fail("Dataset registration changed", "VERSION_CONFLICT")
        if "validation" in record and (
            record["validation"].get("state") != "valid"
            or record["validation"].get("complete") is not True
        ):
            fail("Dataset must be fully validated", "VALIDATION_FAILED")
        if not HASH.fullmatch(str(record.get("manifestHash", ""))):
            fail("Invalid dataset manifestHash")
        names = [s["name"] if isinstance(s, dict) else s for s in record["shards"]]
        if set(shards) - set(names):
            fail("Selected shard not in validated dataset")
        path = Path(text(record["path"], "registered path"))
        if not path.is_absolute() or ".." in path.parts:
            fail("Dataset path is not absolute", "UNSAFE_PATH")
        raw_manifest = file_hash(
            path / "dataset.json", 4 * 1024 * 1024, return_bytes=True
        )
        files = {"dataset.json": digest(raw_manifest)}
        manifest = strict_json(raw_manifest)
        if file_hash(path / "dataset.json", 4 * 1024 * 1024) != files["dataset.json"]:
            fail("Dataset manifest changed", "SOURCE_CHANGED")
        if (
            manifest.get("format") != "kohakugbc-dataset"
            or manifest.get("version") != 1
        ):
            fail("Dataset format unsupported", "FORMAT_UNSUPPORTED")
        declared = [s["name"] for s in manifest.get("shards", [])]
        if set(shards) - set(declared):
            fail("Selected shard missing from manifest", "SOURCE_CHANGED")
        total = 0
        for shard in shards:
            for name in ("manifest.parquet", "graphs.db", "meta.db", "images.zip"):
                rel = "shards/" + shard + "/" + name
                files[rel] = file_hash(path / rel)
                total += (path / rel).stat().st_size
                if total > 20 * 1024**3:
                    fail(
                        "Selected dataset exceeds 20 GiB verification limit",
                        "LIMIT_EXCEEDED",
                    )
        return {
            "datasetId": dataset_id,
            "revision": revision,
            "manifestHash": record["manifestHash"],
            "path": str(path),
            "contentHash": digest(canonical(files)),
            "shards": shards,
        }

    def _entries(self, entries, dataset):
        if type(entries) is not list or not entries or len(entries) > 64:
            fail("DATASETS must be a nonempty list")
        for entry in entries:
            obj(
                entry,
                {"source", "path", "shards", "caption", "weight", "loss_weight"},
                {"source", "path", "shards", "caption"},
            )
            if entry["source"] != "gbc" or entry["path"] != dataset["path"]:
                fail("DATASETS must use the validated GBC registration", "UNSAFE_PATH")
            selected = strings(entry["shards"], "entry shards", True)
            if len(selected) != len(set(selected)) or set(selected) - set(
                dataset["shards"]
            ):
                fail("DATASETS has unselected or duplicate shards")
            caption(entry["caption"])
            from .training_drafts_schema import number

            for key in ("weight", "loss_weight"):
                if key in entry:
                    number(entry[key], key, 1e-12, 1e12)

    def create(self, payload):
        obj(
            payload,
            {
                "datasetId",
                "datasetRevision",
                "shards",
                "baseConfigId",
                "captionOptions",
                "knobPatch",
            },
            {
                "datasetId",
                "datasetRevision",
                "shards",
                "baseConfigId",
                "captionOptions",
            },
        )
        payload = strict_json(canonical(payload))
        base = self._base(payload["baseConfigId"])
        cap = caption(payload["captionOptions"])
        dataset = self._dataset(
            payload["datasetId"], payload["datasetRevision"], payload["shards"]
        )
        sources = self._sources()
        loaded = worker(
            self.root.root,
            self.python,
            {"op": "load", "path": str(base), "root": str(self.root.root)},
        )
        obj(
            loaded, {"values", "defaults", "runtime"}, {"values", "defaults", "runtime"}
        )
        values, defaults = loaded["values"], loaded["defaults"]
        if set(values) - set(defaults):
            fail("Base contains unknown training knobs")
        patch = payload.get("knobPatch", {})
        if type(patch) is not dict or set(patch) - set(defaults):
            fail("Unknown knobPatch keys")
        for key, value in patch.items():
            if key == "DATASETS":
                self._entries(value, dataset)
            elif key == "CAPTION":
                caption(value)
            else:
                _same_type(value, defaults[key], key)
                if key in SELECTORS and value not in SELECTORS[key]:
                    fail("Unregistered knob choice: " + key)
                if (
                    key
                    in {
                        "VALIDATION",
                        "KERNELS",
                        "PLANNER_KWARGS",
                        "MEMORY_OVERRIDES",
                        "ARCH_OVERRIDES",
                    }
                    and value
                ):
                    fail("Extension dictionaries are not exposed by draft v1: " + key)
        values.update(patch)
        if "DATASETS" not in patch:
            values["DATASETS"] = [
                {
                    "source": "gbc",
                    "path": dataset["path"],
                    "shards": dataset["shards"],
                    "caption": cap,
                }
            ]
        self._entries(values["DATASETS"], dataset)
        geometry(values)
        effective = render_effective(values)
        if (
            self._sources() != sources
            or self._dataset(
                dataset["datasetId"], dataset["revision"], dataset["shards"]
            )
            != dataset
        ):
            fail("Sources changed while creating draft", "SOURCE_CHANGED")
        draft_id = "draft-" + uuid.uuid4().hex
        ref = {"draftId": draft_id, "revision": 1, "effectiveHash": digest(effective)}
        draft = {
            "draftRef": ref,
            "datasetRef": {
                k: dataset[k] for k in ("datasetId", "revision", "manifestHash")
            },
            "shards": dataset["shards"],
            "baseConfigRef": {
                "configId": payload["baseConfigId"],
                "dependencyHash": sources,
            },
            "values": values,
            "captionOptions": cap,
            "knobPatch": patch,
            "datasetSnapshot": dataset,
            "runtime": loaded["runtime"],
            "validation": {
                "status": "unchecked",
                "validationId": None,
                "effectiveHash": ref["effectiveHash"],
            },
        }
        with self._coordination_lock, self.store.transaction():
            self.store.write(draft_id + "-1.py", effective)
            self.store.write(draft_id + "-1.json", canonical(draft))
        return copy.deepcopy(draft)

    def _read(self, draft_id, revision):
        if type(draft_id) is not str or not ID.fullmatch(draft_id):
            fail("Invalid draftId")
        integer(revision, "revision", 1)
        try:
            draft = strict_json(self.store.read(f"{draft_id}-{revision}.json"))
        except FileNotFoundError as exc:
            raise DraftError("NOT_FOUND", "Draft revision not found") from exc
        ref = draft["draftRef"]
        if ref["draftId"] != draft_id or ref["revision"] != revision:
            fail("Draft state identity mismatch", "SOURCE_CHANGED")
        effective = self.store.read(f"{draft_id}-{revision}.py")
        if (
            digest(effective) != ref["effectiveHash"]
            or render_effective(draft["values"]) != effective
        ):
            fail("Effective config was modified", "SOURCE_CHANGED")
        return draft

    def get(self, draft_id, revision=None):
        # Immutable v1 drafts: edits create a new ID. Never silently use latest.
        with self._coordination_lock, self.store.transaction():
            draft = self._read(draft_id, 1 if revision is None else revision)
            try:
                self._fresh(draft)
            except DraftError as exc:
                draft["validation"] = {
                    **draft["validation"],
                    "status": "stale",
                    "reason": exc.code,
                }
            return draft

    def _fresh(self, draft):
        self._base(draft["baseConfigRef"]["configId"])
        if self._sources() != draft["baseConfigRef"]["dependencyHash"]:
            fail(
                "Base, trainer or runtime changed; create and check a new draft",
                "SOURCE_CHANGED",
            )
        ds = draft["datasetSnapshot"]
        if self._dataset(ds["datasetId"], ds["revision"], ds["shards"]) != ds:
            fail("Dataset changed since draft creation", "SOURCE_CHANGED")
        self._entries(draft["values"]["DATASETS"], ds)
        geometry(draft["values"])

    def _resolve(self, ref):
        obj(
            ref,
            {"draftId", "revision", "effectiveHash"},
            {"draftId", "revision", "effectiveHash"},
        )
        draft = self._read(ref["draftId"], ref["revision"])
        if ref != draft["draftRef"]:
            fail("Draft reference mismatch", "VERSION_CONFLICT")
        self._fresh(draft)
        path = self.store.path / f"{ref['draftId']}-{ref['revision']}.py"
        return draft, path

    def check(self, draft_ref):
        with self._coordination_lock, self.store.transaction():
            draft, path = self._resolve(draft_ref)
            result = worker(
                self.root.root,
                self.python,
                {"op": "check", "path": str(path), "root": str(self.root.root)},
                timeout=300,
            )
            obj(
                result,
                {"ok", "output", "exitCode", "effectiveHash", "runtime"},
                {"ok", "output", "exitCode", "effectiveHash", "runtime"},
            )
            if (
                type(result["ok"]) is not bool
                or type(result["exitCode"]) is not int
                or type(result["output"]) is not str
                or result["ok"] != (result["exitCode"] == 0)
            ):
                fail("Invalid check response", "WORKER_PROTOCOL")
            self._resolve(draft_ref)
            if (
                result["effectiveHash"] != draft_ref["effectiveHash"]
                or result["runtime"] != draft["runtime"]
            ):
                fail("Checked file/runtime mismatch", "SOURCE_CHANGED")
            draft["validation"] = {
                "status": "valid" if result["ok"] else "invalid",
                "validationId": "validation-" + uuid.uuid4().hex,
                "effectiveHash": draft_ref["effectiveHash"],
                "trainerFingerprint": draft["baseConfigRef"]["dependencyHash"],
                "exitCode": result["exitCode"],
                "output": result["output"],
            }
            self.store.write(
                f"{draft_ref['draftId']}-{draft_ref['revision']}.json", canonical(draft)
            )
            return {**result, "draftRef": draft_ref, "validation": draft["validation"]}

    def resolve_launch(self, draft_ref):
        with self._coordination_lock, self.store.transaction():
            draft, path = self._resolve(draft_ref)
            validation = draft["validation"]
            if (
                validation["status"] != "valid"
                or validation["effectiveHash"] != draft_ref["effectiveHash"]
                or validation.get("trainerFingerprint")
                != draft["baseConfigRef"]["dependencyHash"]
            ):
                fail(
                    "Draft must pass an exact effective-config check before launch",
                    "VALIDATION_FAILED",
                )
            return {
                "config": str(path),
                "overrides": {},
                "draftRef": copy.deepcopy(draft_ref),
            }

    def preview(self, draft_ref, *, sample_id, seed=0, variant=0, target=None):
        text(sample_id, "sampleId")
        integer(seed, "seed", 0, 2**32 - 1)
        integer(variant, "variant", 0, 1000000)
        if target is not None:
            obj(target, {"width", "height"}, {"width", "height"})
            integer(target["width"], "target.width", 16, 16384)
            integer(target["height"], "target.height", 16, 16384)
        with self._coordination_lock, self.store.transaction():
            draft, _path = self._resolve(draft_ref)
            result = worker(
                self.root.root,
                self.python,
                {
                    "op": "preview",
                    "values": draft["values"],
                    "sampleId": sample_id,
                    "seed": seed,
                    "variant": variant,
                    "target": target,
                },
                timeout=60,
            )
            fields = {
                "sampleId",
                "seed",
                "variant",
                "text",
                "chosenFormat",
                "chosenTagSource",
                "geometry",
                "budget",
                "provenance",
                "warnings",
            }
            obj(result, fields, fields)
            if (
                result["seed"] != seed
                or result["variant"] != variant
                or type(result["text"]) is not str
            ):
                fail("Preview identity mismatch", "WORKER_PROTOCOL")
            if any(
                type(result[key]) is not dict
                for key in ("geometry", "budget", "provenance")
            ):
                fail("Invalid preview structure", "WORKER_PROTOCOL")
            if result["budget"].get("hardTokenLimit") is not False:
                fail("Invalid preview budget guarantee", "WORKER_PROTOCOL")
            self._resolve(draft_ref)
            result["provenance"]["trainerSourceHash"] = draft["baseConfigRef"][
                "dependencyHash"
            ]
            return {
                **result,
                "draftRef": copy.deepcopy(draft_ref),
                "datasetRef": draft["datasetRef"],
            }

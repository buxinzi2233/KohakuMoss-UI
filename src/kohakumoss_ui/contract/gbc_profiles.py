"""Reviewed finite local profile; config data never selects an import or factory.

Based on official configs/machine/qwen35_local.py: qwen35 VLM, torch OWLv2,
torch SentenceEmbedder. Local real safetensors folders only, no hub identifiers.
"""

import hashlib
import json
import os
from pathlib import Path

from .annotations import strict_json
from .gbc_paths import read_file, safe_path, within, write_new
from .gbc_schema import digest, fail, fields, integer, text

PROFILE = "qwen35-local-v1"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MODULE_TYPES = {"sentence_transformers.models.Transformer", "sentence_transformers.models.Pooling",
                "sentence_transformers.models.Normalize", "sentence_transformers.models.Dense"}


def _json_policy(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"auto_map", "custom_pipelines", "trust_remote_code", "code_revision", "quantization_config"}:
                fail("INVALID_REQUEST", "Custom model code/quantization is not supported")
            if key in {"model_name_or_path", "tokenizer_name_or_path", "config_name", "cache_dir",
                       "tokenizer_file", "vocab_file", "merges_file", "model_args", "tokenizer_args",
                       "config_args", "model_kwargs", "tokenizer_kwargs"} and item:
                fail("INVALID_REQUEST", "Model loader redirects/overrides are not supported")
            if key == "activation_function" and item not in {
                    "torch.nn.modules.activation.Tanh", "torch.nn.modules.linear.Identity",
                    "torch.nn.modules.activation.GELU", "torch.nn.modules.activation.ReLU"}:
                fail("INVALID_REQUEST", "Unapproved dense activation factory")
            _json_policy(item)
    elif isinstance(value, list):
        for item in value:
            _json_policy(item)


def model_snapshot(path, slot):
    path = safe_path(path)[0]
    if slot == "detect" and "sam3" in str(path).lower():
        fail("INVALID_REQUEST", "OWLv2 profile path must not select the SAM3 factory")
    config = strict_json(read_file(path / "config.json", 4 * 1024 * 1024))
    expected = {"vlm": {"qwen3_5"}, "detect": {"owlv2"}, "embed": {"gemma3_text"}}
    if not isinstance(config, dict) or config.get("model_type") not in expected[slot]:
        fail("INVALID_REQUEST", "Model config does not match the fixed profile slot")
    if slot == "embed":
        modules = strict_json(read_file(path / "modules.json", 1_000_000))
        if not isinstance(modules, list) or not modules:
            fail("INVALID_REQUEST", "SentenceTransformer module manifest required")
        for module in modules:
            fields(module, {"idx", "name", "path", "type"})
            if module["type"] not in MODULE_TYPES:
                fail("INVALID_REQUEST", "Unapproved SentenceTransformer module")
            relative = module["path"]
            if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
                fail("UNSAFE_PATH", "Model module path escapes its folder")
            safe_path(path / relative)
    files = []
    total = 0
    weights = False
    for base, dirs, names in os.walk(path, followlinks=False):
        if len(Path(base).relative_to(path).parts) > 12:
            fail("LIMIT_EXCEEDED", "Model directory depth exceeds limit", 413)
        for name in dirs:
            safe_path(Path(base) / name)
        for name in sorted(names):
            target, identity = safe_path(Path(base) / name, directory=False)
            if target.suffix.lower() in {".py", ".pyc", ".so", ".dll", ".bin", ".pt", ".pth", ".pkl", ".pickle", ".onnx"}:
                fail("INVALID_REQUEST", "Only local data/safetensors model files are allowed")
            info = target.stat()
            total += info.st_size
            if total > 64 * 1024**3 or len(files) >= 10000:
                fail("LIMIT_EXCEEDED", "Model snapshot exceeds limit", 413)
            weights |= target.suffix == ".safetensors"
            item = {"path": str(target.relative_to(path)), "identity": identity}
            if target.suffix == ".json":
                raw = read_file(target, 32 * 1024 * 1024)
                parsed = strict_json(raw)
                _json_policy(parsed)
                item["jsonHash"] = "sha256:" + hashlib.sha256(raw).hexdigest()
                if target.name.endswith(".safetensors.index.json"):
                    for part in parsed.get("weight_map", {}).values():
                        if not isinstance(part, str) or Path(part).name != part or not part.endswith(".safetensors"):
                            fail("UNSAFE_PATH", "Unsafe weight-map reference")
                        safe_path(target.parent / part, directory=False)
            files.append(item)
    if not weights:
        fail("DEPENDENCY_MISSING", "Local safetensors model weights are missing", 503)
    files.sort(key=lambda row: row["path"])
    return {"path": str(path), "files": files, "bytes": total, "fingerprint": digest(files)}


def local_config(task, output):
    models = task["_models"]
    return {
        "engines": {
            "vlm": {"entry": "qwen35", "model": models["vlm"]["path"], "device": "cuda:0",
                    "memory": 0.65, "state_dtype": "bfloat16", "max_seqs": 4},
            "detect": {"entry": "torch", "model": models["detect"]["path"],
                       "devices": ["cuda:0"], "max_batch": 1, "wait_ms": 5.0},
            "embed": {"entry": "torch", "model": models["embed"]["path"],
                      "devices": ["cpu"], "max_batch": 16},
            "cpu": {"entry": "python", "workers": 2}},
        "source": {"kind": "jsonl", "path": task["_source"]},
        "sink": {"kind": "graphs", "path": str(output / "graphs.jsonl")},
        "machine": {"vlm_waiting": 4, "max_jobs": 8},
        "controls": {"root": "query"},
    }


def create_local(service, args, task_id):
    fields(args["models"], {"vlm", "detect", "embed"})
    models = {}
    for slot, ref in args["models"].items():
        fields(ref, {"rootId", "relativePath"})
        path = within(service._root(ref["rootId"], "model"), ref["relativePath"])
        models[slot] = model_snapshot(path, slot)
    source_root = service._root(args["inputRootId"], "input")
    relative = Path(text(args["relativePath"], "relativePath"))
    if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative):
        fail("UNSAFE_PATH", "Input must stay under its registered root")
    kind = args.get("sourceKind", "folder")
    if kind not in {"folder", "jsonl"}:
        fail("INVALID_REQUEST", "Source kind must be folder or jsonl")
    source = source_root / relative
    rows = []
    if kind == "folder":
        safe_path(source)
        visited = 0
        for base, dirs, names in os.walk(source, followlinks=False):
            visited += len(dirs) + len(names)
            if visited > 10000 or len(Path(base).relative_to(source).parts) > 16:
                fail("LIMIT_EXCEEDED", "Image scan exceeds limits", 413)
            for name in dirs:
                safe_path(Path(base) / name)
            for name in sorted(names):
                target = Path(base) / name
                safe_path(target, directory=False)
                if target.suffix.lower() in IMAGE_EXTENSIONS:
                    rows.append({"key": str(target.relative_to(source)), "image": str(target)})
    else:
        for line in read_file(source, 16 * 1024 * 1024).splitlines():
            if not line.strip():
                continue
            row = strict_json(line)
            fields(row, {"image"}, {"key", "captions", "tags"})
            image = Path(text(row["image"], "image"))
            target = Path(os.path.normpath(str(source.parent / image)))
            if source_root not in target.parents:
                fail("UNSAFE_PATH", "JSONL image escapes registered input root")
            row["image"] = str(target)
            row.setdefault("key", str(target.relative_to(source_root)))
            if "captions" in row:
                fields(row["captions"], (), {"detail", "short"})
                for value in row["captions"].values():
                    text(value, "caption", 32768)
            if "tags" in row:
                fields(row["tags"], {"general"})
                if not isinstance(row["tags"]["general"], dict):
                    fail("INVALID_REQUEST", "general tags must be confidence mapping")
                for tag, confidence in row["tags"]["general"].items():
                    text(tag, "tag", 256)
                    if type(confidence) not in {int, float} or not 0 <= confidence <= 1:
                        fail("INVALID_REQUEST", "Invalid tag confidence")
            rows.append(row)
    if not 1 <= len(rows) <= 1000:
        fail("LIMIT_EXCEEDED", "Local generation requires 1..1000 images", 413)
    staging = service.store.workdir(task_id)
    images = staging / "images"
    images.mkdir(mode=0o700)
    records, frozen, keys = [], [], set()
    total = 0
    for index, row in enumerate(rows):
        key = text(row["key"], "sample key")
        if key in keys:
            fail("INVALID_REQUEST", "Duplicate input key")
        keys.add(key)
        target = Path(row["image"])
        if target.suffix.lower() not in IMAGE_EXTENSIONS:
            fail("INVALID_REQUEST", "Unsupported source image type")
        raw = read_file(target, 32 * 1024 * 1024)
        total += len(raw)
        if total > 256 * 1024 * 1024:
            fail("LIMIT_EXCEEDED", "Frozen images exceed 256 MiB", 413)
        destination = images / (str(index) + target.suffix.lower())
        write_new(destination, raw)
        row = {**row, "image": str(destination)}
        frozen.append(row)
        records.append({"key": "sample_" + hashlib.sha256(key.encode()).hexdigest()[:32],
                        "image": str(destination), "meta": {"sourceKey": key}})
    source_file = staging / "source.jsonl"
    write_new(source_file, ("\n".join(json.dumps(row, ensure_ascii=True) for row in frozen) + "\n").encode())
    manifest = service._tree_manifest(staging)
    task = {"taskId": task_id, "revision": 1, "mode": PROFILE, "runtimeId": args["runtimeId"],
            "outputRootId": args["outputRootId"], "total": len(rows), "validationId": None,
            "timeoutSeconds": integer(args.get("timeoutSeconds", 600), 1, 86400),
            "_staging": str(staging), "_source": str(source_file), "_records": records,
            "_models": models, "_manifest": manifest, "inferenceVerified": False,
            "runtimeAvailable": None,
            "inputManifestHash": digest({"files": manifest, "models": models})}
    service.db["tasks"][task_id] = task
    return task

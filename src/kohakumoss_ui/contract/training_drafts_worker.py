"""Isolated trainer adapter. Invoke only with the designated trainer Python -I -B.

Stdout is one strict JSON envelope. Imports and the official CLI's human output
are redirected to stderr (bounded by the parent); no models or scans are run.
"""

import contextlib
import hashlib
import importlib.metadata
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

# -I intentionally excludes the UI package; expose only our adjacent pure schema.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from training_drafts_schema import (
    DraftError,
    canonical,
    caption,
    fail,
    geometry,
    strict_json,
)

LIMIT = 4 * 1024 * 1024


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def convert(value):
    if isinstance(value, tuple):
        return [convert(v) for v in value]
    if type(value) is list:
        return [convert(v) for v in value]
    if type(value) is dict:
        if any(type(k) is not str for k in value):
            fail("Non-string config key")
        return {k: convert(v) for k, v in value.items()}
    # No repr fallback; callable, Path, numpy scalar, etc. must fail explicitly.
    canonical(value)
    return value


def runtime():
    names = (
        "kohakumoss",
        "kohaku-engine",
        "kohakugbc-data",
        "numpy",
        "torch",
        "pyarrow",
        "kohakuvault",
    )
    return {
        "python": sys.version,
        "packages": {n: importlib.metadata.version(n) for n in names},
    }


class Capture(io.TextIOBase):
    def __init__(self):
        self.parts = []
        self.size = 0

    def write(self, value):
        self.size += len(value.encode("utf-8"))
        if self.size > LIMIT // 2:
            fail("Checker output limit exceeded", "LIMIT_EXCEEDED")
        self.parts.append(value)
        return len(value)

    def text(self):
        return "".join(self.parts)


def load(request):
    from kohakumoss.cli.config.validate import TRAIN, config_knobs, script_defaults

    path = Path(request["path"])
    root = Path(request["root"])
    script, values = config_knobs(path, root)
    if script != TRAIN:
        fail("Only scripts/train/moss.py configs are supported")
    return {
        "values": convert(values),
        "defaults": convert(script_defaults(root, TRAIN)),
        "runtime": runtime(),
    }


def check(request):
    from kohakumoss.cli.config.main import main

    path = Path(request["path"])
    before = path.read_bytes()
    capture = Capture()
    code = 0
    with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
        try:
            main(["check", str(path)])
        except SystemExit as exc:
            code = int(exc.code or 0)
    if path.read_bytes() != before:
        fail("Effective file changed during check", "SOURCE_CHANGED")
    return {
        "ok": code == 0,
        "exitCode": code,
        "output": capture.text(),
        "effectiveHash": digest(before),
        "runtime": runtime(),
    }


def preview(request):
    import pyarrow.parquet as pq
    from kohakugbc_data import GbcGraph
    from kohakugbc_data.dataset import GbcDataset
    from kohakumoss_data.caption.gbc import GbcTemplate, crop_box
    from kohakumoss_data.config import DataConfig
    from kohakumoss_data.dataset import MossDataset, text_chars_budget
    from kohakumoss_data.format.catalog import Catalog
    from kohakumoss_data.registry import PLANNERS
    from kohakumoss_data.resolution.geometry import Geometry
    from kohakumoss_data.seeds import rng_of
    from kohakumoss_data.sources.gbc import GbcRecord
    from kohakumoss_data.types import CatalogRow
    from kohakuvault import DataPacker, KVault

    values = request["values"]
    geometry(values)
    cfg = DataConfig.from_knobs(values)
    if values.get("PLANNER_KWARGS"):
        fail(
            "Preview does not support custom planner arguments",
            "UNSUPPORTED_CAPABILITY",
        )
    # A locator disambiguates shards. A bare key is accepted only if unique.
    sample_id = request["sampleId"]
    selected = []
    for entry in values["DATASETS"]:
        caption(entry["caption"])
        for shard in entry["shards"]:
            selected.append((entry, shard))
    found = None
    for entry, shard in selected:
        key = sample_id
        if "/" in sample_id:
            wanted, key = sample_id.split("/", 1)
            if wanted != shard:
                continue
        manifest = Path(entry["path"]) / "shards" / shard / "manifest.parquet"
        # Reader materializes one shard; gate both stored bytes and row count.
        if (
            manifest.stat().st_size > 32 * 1024 * 1024
            or pq.ParquetFile(manifest).metadata.num_rows > 100000
        ):
            fail("Preview shard exceeds reader limit", "LIMIT_EXCEEDED")
        with GbcDataset(
            entry["path"], shards=[shard], image=None, graphs=False, meta=False
        ) as dataset:
            if key not in dataset:
                continue
            if found is not None:
                fail(
                    "Sample belongs to multiple dataset entries; use a unique selection"
                )
            sample = dataset[key]
            # Never let KVault auto-pack dispatch a Pickle/custom-object codec.
            folder = manifest.parent
            with contextlib.ExitStack() as stack:
                stores = {}
                for name in ("graphs", "meta"):
                    store = KVault(
                        str(folder / (name + ".db")), table=name, read_only=True
                    )
                    stack.callback(store.close)
                    store.disable_auto_pack()
                    raw = store[key]
                    if type(raw) is not bytes or len(raw) > 2 * 1024 * 1024:
                        fail("Sample record exceeds preview limit", "LIMIT_EXCEEDED")
                    stores[name] = raw
                raw_meta = stores["meta"]
                if not raw_meta.startswith(b"\x89K\x01\x04\x00\x00\x00\x00VK"):
                    fail("Only MessagePack metadata is supported", "VALIDATION_FAILED")
                sample.meta = DataPacker("msgpack").unpack(raw_meta[10:], 0)
                canonical(sample.meta)
                if type(sample.meta) is not dict:
                    fail("Sample metadata must be a dict")
                sample.graph = GbcGraph.from_msgpack(stores["graphs"])
                if not sample.graph.typed:
                    fail("Sample graph is not typed", "VALIDATION_FAILED")
            found = (entry, sample)
    if found is None:
        fail("Sample not in selected shards", "NOT_FOUND")
    entry, sample = found
    row = CatalogRow(
        id=0, locator=f"{sample.shard}/{sample.key}", w=int(sample.w), h=int(sample.h)
    )
    catalog = Catalog([row])
    geo = Geometry(
        area=cfg.target_area,
        step=cfg.size_step,
        aspect_range=cfg.aspect_range,
        upscale=cfg.upscale,
        crop=cfg.crop,
        latent_factor=cfg.latent_factor,
    )
    planner = PLANNERS.get(cfg.resolution_mode)(
        catalog=catalog,
        geometry=geo,
        seed=cfg.seed,
        flip=False,
        caption_variants=cfg.caption_variants,
        batching=cfg.batching,
        overlong=cfg.overlong,
        max_tokens=cfg.max_tokens,
        max_samples=cfg.max_samples,
        batch_size=cfg.batch_size,
    )
    # Use the real method without constructing MossDataset (which would scan/cache).
    geo_row = MossDataset.row_geometry(
        SimpleNamespace(catalog=catalog, planner=planner, geometry=geo), 0
    )
    target = {"width": int(geo_row.target_hw[1]), "height": int(geo_row.target_hw[0])}
    if request["target"] is not None and request["target"] != target:
        fail("Requested target does not match trainer planner", "VERSION_CONFLICT")
    t = GbcTemplate(**{k: v for k, v in entry["caption"].items() if k != "template"})
    record = GbcRecord(sample.graph, sample.meta)
    source = SimpleNamespace(record=lambda locator: record)
    max_chars = text_chars_budget(cfg)
    render = t.bind(source, lambda row_id: geo_row, max_chars)
    seed, variant = request["seed"], request["variant"]
    # Stable preview row=0; not a claim to reproduce the full training catalog RNG.
    parts = t.parts(record, geo_row)
    rng = rng_of("caption", seed, 0, variant)
    fmt = t.choose(parts, rng)
    tag_source = t.tag_source(parts, rng)
    output = render(row, rng_of("caption", seed, 0, variant), 0)
    graph_data = json.loads(sample.graph.to_json())
    mapped = []
    for vertex in graph_data["vertices"]:
        b = vertex["bbox"]
        box = crop_box(tuple(b[k] for k in ("left", "top", "right", "bottom")), geo_row)
        mapped.append(
            {
                "nodeId": vertex["vertex_id"],
                "normalizedLtrb": list(box) if box else None,
            }
        )
    geo_dto = {
        "img_size": [row.w, row.h],
        "target": target,
        "crop": {"mode": "center", "ltrb": list(geo_row.crop_ltrb)},
        "bboxOutputSpace": "abs_centralized-yxyx",
        "mappedBoxes": mapped,
    }
    geo_dto["geometryHash"] = digest(canonical(geo_dto))
    budget = {
        "maxTextTokens": cfg.max_text_tokens,
        "textOverheadTokens": cfg.text_overhead_tokens,
        "textCharsPerToken": cfg.text_chars_per_token,
        "fitMargin": t.margin,
    }
    budget_hash = digest(canonical(budget))
    planner_hash = digest(
        canonical(
            {
                k: values[k]
                for k in (
                    "RESOLUTION_MODE",
                    "TARGET_AREA",
                    "SIZE_STEP",
                    "ASPECT_RANGE",
                    "UPSCALE",
                    "CROP",
                )
            }
        )
    )
    return {
        "sampleId": row.locator,
        "seed": seed,
        "variant": variant,
        "text": output,
        "chosenFormat": list(fmt.sections),
        "chosenTagSource": tag_source,
        "geometry": geo_dto,
        "budget": {
            **budget,
            "source": "trainer-knobs",
            "sourceHash": budget_hash,
            "maxChars": render.max_chars,
            "withinCharacterEstimate": len(output) <= render.max_chars,
            "hardTokenLimit": False,
        },
        "provenance": {
            "adapterVersion": "gbc-draft-v1",
            "rngMapping": "rng_of(caption,seed,row=0,variant)",
            "templateHash": digest(canonical(entry["caption"])),
            "graphHash": digest(canonical(graph_data)),
            "metaHash": digest(canonical(sample.meta)),
            "plannerHash": planner_hash,
            "budgetSourceHash": budget_hash,
            "dataPackageVersion": importlib.metadata.version("kohakugbc-data"),
        },
        "warnings": [
            "Character estimate, not a hard token limit; preview row=0 is not the training catalog row."
        ],
    }


def main():
    raw = sys.stdin.buffer.read(LIMIT + 1)
    if len(raw) > LIMIT:
        fail("Input size limit", "LIMIT_EXCEEDED")
    request = strict_json(raw)
    operations = {"load": load, "check": check, "preview": preview}
    if request.get("op") not in operations:
        fail("Unknown worker operation")
    # Protect stdout even against native writes from imported libraries.
    protocol = os.dup(1)
    os.dup2(2, 1)
    try:
        try:
            with contextlib.redirect_stdout(sys.stderr):
                data = operations[request["op"]](request)
            reply = {"ok": True, "data": data, "error": None}
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - protocol boundary reports failures
            reply = {
                "ok": False,
                "data": None,
                "error": {
                    "code": getattr(exc, "code", "VALIDATION_FAILED")
                    if isinstance(exc, DraftError)
                    else "VALIDATION_FAILED",
                    "message": str(exc),
                },
            }
        encoded = canonical(reply)
        with os.fdopen(protocol, "wb") as out:
            out.write(encoded)
    finally:
        sys.stdout.flush()


if __name__ == "__main__":
    main()

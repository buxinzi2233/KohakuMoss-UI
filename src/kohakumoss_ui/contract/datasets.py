"""Inspect datasets declared by user configs, plus their cache directories."""

import os
import threading
import time
from pathlib import Path

from . import configs
from .root import TrainerRoot


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
_CACHE: dict[str, object] = {"at": 0.0, "payload": None}
_LOCK = threading.Lock()


def _count(root: Path, max_files: int = 50_000) -> dict:
    images = 0
    captions = 0
    files = 0
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__"}]
        for name in names:
            files += 1
            suffix = Path(name).suffix.lower()
            if suffix in IMAGE_SUFFIXES:
                images += 1
            elif suffix == ".txt":
                captions += 1
            if files >= max_files:
                return {"images": images, "captions": captions, "files": files, "truncated": True}
    return {"images": images, "captions": captions, "files": files, "truncated": False}


def _cache_info(dataset: Path) -> dict:
    folder = dataset / "cache"
    if not folder.is_dir():
        return {"present": False, "files": 0, "bytes": 0}
    files = 0
    size = 0
    try:
        for base, _dirs, names in os.walk(folder):
            for name in names:
                files += 1
                try:
                    size += (Path(base) / name).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return {"present": True, "files": files, "bytes": size}


def scan(root: TrainerRoot, refresh: bool = False) -> dict:
    """Unique DATASETS entries from user configs and the data templates."""
    with _LOCK:
        fresh = _CACHE["payload"] is not None and time.time() - float(_CACHE["at"]) < 30
        if fresh and not refresh:
            return _CACHE["payload"]  # type: ignore[return-value]

    entries: dict[tuple[str, str], dict] = {}
    scanned: list[str] = []
    failures: list[dict] = []
    for item in configs.list_configs(root):
        folder = item["dir"]
        if not (folder.startswith("user") or folder in {"moss/data", "moss/recipes", "moss/_base"}):
            continue
        try:
            values = configs.load_config(root, item["path"])
        except Exception as exc:
            failures.append({"config": item["path"], "error": str(exc)[-500:]})
            continue
        scanned.append(item["path"])
        for dataset in values.get("DATASETS") or []:
            if not isinstance(dataset, dict):
                continue
            path = str(dataset.get("path") or "")
            key = (str(dataset.get("source") or ""), path)
            if key not in entries:
                entries[key] = {
                    "source": key[0],
                    "path": path,
                    "configs": [],
                    "options": {k: v for k, v in dataset.items() if k not in {"source", "path"}},
                }
            entries[key]["configs"].append(item["path"])

    result: list[dict] = []
    for item in entries.values():
        raw = item["path"]
        dataset = Path(raw).expanduser()
        if not dataset.is_absolute():
            dataset = root.root / dataset
        exists = dataset.is_dir()
        counts = _count(dataset) if exists else {"images": 0, "captions": 0, "files": 0, "truncated": False}
        result.append(
            {
                **item,
                "template": not any(config.startswith("configs/user/") for config in item["configs"]),
                "absolute": str(dataset),
                "exists": exists,
                "images": counts["images"],
                "captions": counts["captions"],
                "files": counts["files"],
                "truncated": counts["truncated"],
                "cache": _cache_info(dataset) if exists else {"present": False, "files": 0, "bytes": 0},
            }
        )

    payload = {
        "datasets": sorted(result, key=lambda item: (item["template"], item["path"])),
        "scanned_configs": scanned,
        "failures": failures,
        "scanned_at": time.time(),
        "root": str(root.root),
    }
    with _LOCK:
        _CACHE["payload"] = payload  # type: ignore[assignment]
        _CACHE["at"] = time.time()
    return payload

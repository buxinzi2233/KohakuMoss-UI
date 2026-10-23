"""Scan run directories: status, metrics, checkpoints, samples, exports."""

import json
import os
import time
from pathlib import Path

from . import logs, metrics, state
from .root import TrainerRoot


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
STEP_PREFIX = "step_"


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text("utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _checkpoint_dirs(run_path: Path) -> list[Path]:
    folder = run_path / "checkpoints"
    if not folder.is_dir():
        return []
    try:
        return sorted(
            (item for item in folder.iterdir() if item.is_dir() and item.name.startswith(STEP_PREFIX)),
            key=lambda item: item.name,
        )
    except OSError:
        return []


def _sample_dirs(run_path: Path) -> list[Path]:
    folder = run_path / "samples"
    if not folder.is_dir():
        return []
    try:
        return sorted(
            (item for item in folder.iterdir() if item.is_dir() and item.name.startswith(STEP_PREFIX)),
            key=lambda item: item.name,
        )
    except OSError:
        return []


def _status(run_path: Path, config: dict, last: dict | None, managed: dict | None) -> str:
    if managed is not None:
        return "running" if managed.get("alive") else "stopped"
    steps = _as_int(config.get("STEPS"))
    step = _as_int(last.get("step")) if last else 0
    if steps and step >= steps:
        return "done"
    if last is not None:
        return "stopped"
    if (run_path / "log.txt").exists() or (run_path / "config.json").exists():
        return "unknown"
    return "empty"


def summarize(root: TrainerRoot, run_path: Path, managed: dict | None = None) -> dict:
    config = _read_json(run_path / "config.json")
    metrics_path = run_path / "metrics.jsonl"
    last = metrics.last_metric(metrics_path) if metrics_path.is_file() else None
    status = _status(run_path, config, last, managed)
    steps = _as_int(config.get("STEPS"))
    step = _as_int(last.get("step")) if last else 0
    checkpoints = _checkpoint_dirs(run_path)
    samples = _sample_dirs(run_path)
    exports = []
    export_dir = run_path / "export"
    if export_dir.is_dir():
        try:
            exports = sorted(item.name for item in export_dir.iterdir() if item.is_file())
        except OSError:
            exports = []

    activity = max(_mtime(metrics_path), _mtime(run_path / "log.txt"), _mtime(run_path))
    started = (managed or {}).get("started_at") or _mtime(run_path)
    return {
        "name": run_path.name,
        "path": str(run_path),
        "status": status,
        "step": step,
        "steps": steps,
        "progress": (step / steps) if steps else 0.0,
        "loss": last.get("loss") if last else None,
        "lr": last.get("lr") if last else None,
        "grad_norm": last.get("grad_norm") if last else None,
        "tokens_per_s": last.get("tokens_per_s") if last else None,
        "vram": last.get("vram_peak_alloc") if last else None,
        "step_time": last.get("step_time") if last else None,
        "started_at": started,
        "last_activity": activity,
        "checkpoints": len(checkpoints),
        "samples": len(samples),
        "exports": exports,
        "config": {
            "ALGO": config.get("ALGO"),
            "DIM": config.get("DIM"),
            "LR": config.get("LR"),
            "STEPS": config.get("STEPS"),
            "PRECISION": config.get("PRECISION"),
            "MEMORY_PROFILE": config.get("MEMORY_PROFILE"),
            "OUTPUT_DIR": config.get("OUTPUT_DIR"),
            "RUN_NAME": config.get("RUN_NAME"),
        },
        "managed": managed,
    }


def list_runs(root: TrainerRoot) -> list[dict]:
    """Every run directory, newest activity first, plus managed processes."""
    runs: list[dict] = []
    seen: set[str] = set()
    base = root.runs_dir
    if base.is_dir():
        try:
            for item in base.iterdir():
                if not item.is_dir():
                    continue
                managed = state.status(item.name)
                runs.append(summarize(root, item, managed))
                seen.add(item.name)
        except OSError:
            pass

    for name, entry in state.all_entries().items():
        if name in seen:
            continue
        alive = state.pid_alive(int(entry.get("pid", 0)))
        runs.append(
            {
                "name": name,
                "path": str(base / name),
                "status": "running" if alive else "stopped",
                "step": 0,
                "steps": 0,
                "progress": 0.0,
                "loss": None,
                "lr": None,
                "grad_norm": None,
                "tokens_per_s": None,
                "vram": None,
                "step_time": None,
                "started_at": entry.get("started_at", 0),
                "last_activity": entry.get("started_at", 0),
                "checkpoints": 0,
                "samples": 0,
                "exports": [],
                "config": {},
                "managed": dict(entry, alive=alive),
            }
        )
    runs.sort(key=lambda item: item.get("last_activity") or 0, reverse=True)
    return runs


def run_path(root: TrainerRoot, name: str) -> Path:
    """Resolve one run directory, refusing anything outside the runs root."""
    base = root.runs_dir.resolve()
    path = (base / name).resolve()
    if path.parent != base:
        raise ValueError("run name escapes the runs root")
    if not path.is_dir():
        raise FileNotFoundError(name)
    return path


def checkpoints(run: Path) -> list[dict]:
    items = []
    for folder in _checkpoint_dirs(run):
        export_dir = folder / "export"
        exports = []
        if export_dir.is_dir():
            try:
                exports = sorted(item.name for item in export_dir.iterdir() if item.is_file())
            except OSError:
                pass
        items.append(
            {
                "name": folder.name,
                "step": _as_int(folder.name[len(STEP_PREFIX):]),
                "size": _dir_size(folder),
                "mtime": _mtime(folder),
                "exports": exports,
            }
        )
    return items


def samples(run: Path) -> list[dict]:
    items = []
    for folder in _sample_dirs(run):
        files = []
        try:
            for item in sorted(folder.iterdir()):
                if item.is_file():
                    files.append({"name": item.name, "size": item.stat().st_size})
        except OSError:
            pass
        items.append(
            {
                "name": folder.name,
                "step": _as_int(folder.name[len(STEP_PREFIX):]),
                "files": files,
            }
        )
    return items


def detail(root: TrainerRoot, name: str, limit: int = 800) -> dict:
    path = run_path(root, name)
    summary = summarize(root, path, state.status(name))
    metrics_points = []
    metrics_path = path / "metrics.jsonl"
    if metrics_path.is_file():
        metrics_points = metrics.tail_metrics(metrics_path, limit)

    config = _read_json(path / "config.json")
    return {
        "summary": summary,
        "metrics": metrics_points,
        "config": config,
        "logs": logs.tail_lines(path / "log.txt", 400),
        "checkpoints": checkpoints(path),
        "samples": samples(path),
        "exports": summary.get("exports", []),
        "grid": (path / "samples" / "grid.png").is_file(),
        "server_time": time.time(),
    }


def artifact_file(root: TrainerRoot, name: str, relpath: str) -> Path:
    """Resolve a file inside a run directory, refusing traversal."""
    base = run_path(root, name)
    target = (base / relpath).resolve()
    if target != base and base not in target.parents:
        raise ValueError("path escapes the run directory")
    if not target.is_file():
        raise FileNotFoundError(relpath)
    return target

"""Run the trainer's doctor and reduce it to a preflight verdict."""

import json
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import configs
from .root import TrainerRoot


_LOCK = threading.Lock()
_CACHE: dict[str, object] = {"at": 0.0, "payload": None}


def _doctor_payload(root: TrainerRoot) -> dict:
    tool = root.doctor_bin
    if tool is None:
        return {"error": "kohakumoss-doctor not found"}
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        target = Path(handle.name)
    try:
        result = subprocess.run(
            [str(tool), "--json", str(target)],
            cwd=root.root,
            capture_output=True,
            text=True,
            timeout=240,
        )
        if target.is_file() and target.stat().st_size > 0:
            payload = json.loads(target.read_text("utf-8"))
            if isinstance(payload, dict):
                return payload
        return {"error": (result.stderr or result.stdout).strip()[-2000:] or "doctor wrote no report"}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            target.unlink()
        except OSError:
            pass


def _exists(root: TrainerRoot, value) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root.root / path
    return path.exists()


def _user_configs(root: TrainerRoot) -> list[dict]:
    return [item for item in configs.list_configs(root) if item["dir"].startswith("user")]


def _preflight(root: TrainerRoot) -> dict:
    blocking: list[dict] = []
    warnings: list[dict] = []
    configs_found = _user_configs(root)
    if not configs_found:
        warnings.append({"what": "no user config", "fix": "kohakumoss-config new train"})
    for item in configs_found[:8]:
        try:
            values = configs.load_config(root, item["path"])
        except Exception as exc:
            warnings.append({"what": f"{item['path']}: {exc}", "fix": "check the config"})
            continue
        weights = values.get("WEIGHTS")
        if not _exists(root, weights):
            blocking.append(
                {
                    "what": f"weights: {weights or '(unset)'}",
                    "fix": f"set WEIGHTS in {item['path']}",
                }
            )
        for dataset in values.get("DATASETS") or []:
            if isinstance(dataset, dict) and not _exists(root, dataset.get("path")):
                blocking.append(
                    {
                        "what": f"dataset: {dataset.get('path') or '(unset)'}",
                        "fix": f"set DATASETS in {item['path']}",
                    }
                )
    return {"blocking": blocking, "warnings": warnings, "configs": len(configs_found)}


def report(root: TrainerRoot, refresh: bool = False) -> dict:
    """Cached doctor report plus a preflight derived from user configs."""
    with _LOCK:
        fresh = _CACHE["payload"] is not None and time.time() - float(_CACHE["at"]) < 60
        if fresh and not refresh:
            return _CACHE["payload"]  # type: ignore[return-value]
        payload = _doctor_payload(root)
    payload["preflight"] = _preflight(root)
    payload["checked_at"] = time.time()
    with _LOCK:
        _CACHE["payload"] = payload
        _CACHE["at"] = time.time()
    return payload


def clear() -> None:
    with _LOCK:
        _CACHE["payload"] = None
        _CACHE["at"] = 0.0

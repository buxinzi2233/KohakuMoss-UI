"""Persistent registry of trainer processes started by this UI."""

import json
import os
import threading
import time
from pathlib import Path

from .. import paths


_LOCK = threading.Lock()


def _read() -> dict:
    try:
        data = json.loads(paths.state_file().read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"runs": {}}
    if not isinstance(data, dict) or not isinstance(data.get("runs"), dict):
        return {"runs": {}}
    return data


def _write(data: dict) -> None:
    target = paths.state_file()
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), "utf-8")
    os.replace(tmp, target)


def pid_alive(pid: int) -> bool:
    """True when the pid exists and is not a zombie."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    except OSError:
        return False
    proc = Path("/proc") / str(pid)
    if proc.is_dir():
        try:
            stat = (proc / "stat").read_text("utf-8")
            if stat.rsplit(")", 1)[-1].strip().startswith("Z"):
                return False
        except OSError:
            pass
    return True


def register(name: str, pid: int, config: str, command: list[str], log_path: str) -> None:
    """Record a child process so its state survives a UI restart."""
    with _LOCK:
        data = _read()
        data["runs"][name] = {
            "pid": pid,
            "config": config,
            "command": command,
            "log_path": log_path,
            "started_at": time.time(),
        }
        _write(data)


def remove(name: str) -> None:
    with _LOCK:
        data = _read()
        if data["runs"].pop(name, None) is not None:
            _write(data)


def entry(name: str) -> dict | None:
    with _LOCK:
        return _read()["runs"].get(name)


def all_entries() -> dict:
    with _LOCK:
        return dict(_read()["runs"])


def status(name: str) -> dict | None:
    """Return a managed entry plus a live flag, or None when unmanaged."""
    item = entry(name)
    if item is None:
        return None
    result = dict(item)
    result["alive"] = pid_alive(int(item.get("pid", 0)))
    return result

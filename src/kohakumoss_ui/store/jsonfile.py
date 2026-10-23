"""Atomic, private JSON files under the UI state directory.

Every UI-owned JSON record goes through these two helpers so that a crash
mid-write never leaves a half-written file (tmp + os.replace, one fsync) and so
that credential-bearing files land with mode 0600.

A file that exists but does not parse is copied once to `<name>.bak` so the
broken payload stays inspectable while the UI keeps working from defaults.
"""

import copy
import json
import os
import tempfile
import threading
from pathlib import Path


MODE = 0o600
BACKUP_SUFFIX = ".bak"

_LOCK = threading.Lock()


def read_json(path, default):
    """Parsed JSON, or a deep copy of `default` when missing or unreadable."""
    target = Path(path)
    try:
        text = target.read_text("utf-8")
    except OSError:
        return copy.deepcopy(default)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        _backup(target)
        return copy.deepcopy(default)
    if isinstance(default, dict) and not isinstance(data, dict):
        _backup(target)
        return copy.deepcopy(default)
    return data


def write_json(path, data) -> Path:
    """Replace `path` with `data` atomically and with mode 0600."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with _LOCK:
        handle, tmp = tempfile.mkstemp(
            dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as sink:
                sink.write(text)
                sink.flush()
                os.fsync(sink.fileno())
            os.chmod(tmp, MODE)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return target


def _backup(path: Path) -> None:
    """Keep the last unparsable payload next to the file it came from."""
    try:
        payload = path.read_bytes()
    except OSError:
        return
    target = path.with_name(path.name + BACKUP_SUFFIX)
    try:
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, target)
    except OSError:
        pass

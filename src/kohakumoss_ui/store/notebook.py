"""Notebook entries: ideas and recipes kept beside the run queue.

An entry is either a note (status idea / todo) or something launchable: it
carries a config path plus optional knob overrides, which is exactly what the
queue needs. Records live in <state>/notebook.json and every write is atomic.
"""

import time
import uuid

from .. import paths
from . import jsonfile


FILE = "notebook.json"
LIMIT = 500
STATUSES = ("idea", "todo", "queued", "trained", "failed", "dropped")
DEFAULT_STATUS = "idea"
MAX_TITLE = 200
MAX_TEXT = 4000
MAX_NAME = 120
MAX_TAGS = 20
MAX_TAG = 40
MAX_OVERRIDES = 200
MAX_OVERRIDE_KEY = 80
MAX_OVERRIDE_VALUE = 200


def _file():
    return paths.state_dir() / FILE


def _default() -> dict:
    return {"items": []}


def _text(value, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default


def _stamp(value, default: float) -> float:
    number = _float(value, default)
    return number if number > 0 else default


def _tags(value) -> list:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        return []
    found: list = []
    for item in value:
        tag = _text(item, MAX_TAG)
        if tag and tag not in found:
            found.append(tag)
        if len(found) >= MAX_TAGS:
            break
    return found


def _overrides(value) -> dict:
    if not isinstance(value, dict):
        return {}
    found: dict = {}
    for key, item in list(value.items())[:MAX_OVERRIDES]:
        name = _text(key, MAX_OVERRIDE_KEY)
        if not name:
            continue
        found[name] = _text(item, MAX_OVERRIDE_VALUE)
    return found


def _status(value, strict: bool) -> str:
    text = _text(value, 40).lower()
    if not text:
        return DEFAULT_STATUS
    if text in STATUSES:
        return text
    if strict:
        raise ValueError("unknown notebook status: " + text)
    return DEFAULT_STATUS


def _blank(now: float) -> dict:
    return {
        "id": "",
        "title": "",
        "base": "",
        "dim": 0,
        "steps": 0,
        "lr": 0.0,
        "dataset": "",
        "note": "",
        "tags": [],
        "priority": 0,
        "status": DEFAULT_STATUS,
        "config": "",
        "overrides": {},
        "created_at": now,
        "updated_at": now,
        "run_name": "",
    }


def _apply(item: dict, raw: dict, now: float, strict: bool) -> dict:
    """Overlay the fields a caller supplied onto an existing entry."""
    result = dict(item)
    if "title" in raw:
        result["title"] = _text(raw.get("title"), MAX_TITLE)
    if "base" in raw:
        result["base"] = _text(raw.get("base"), MAX_TEXT)
    if "dataset" in raw:
        result["dataset"] = _text(raw.get("dataset"), MAX_TEXT)
    if "note" in raw:
        result["note"] = _text(raw.get("note"), MAX_TEXT)
    if "config" in raw:
        result["config"] = _text(raw.get("config"), MAX_TEXT)
    if "run_name" in raw:
        result["run_name"] = _text(raw.get("run_name"), MAX_NAME)
    if "dim" in raw:
        result["dim"] = max(0, _int(raw.get("dim")))
    if "steps" in raw:
        result["steps"] = max(0, _int(raw.get("steps")))
    if "lr" in raw:
        result["lr"] = max(0.0, _float(raw.get("lr")))
    if "priority" in raw:
        result["priority"] = _int(raw.get("priority"))
    if "tags" in raw:
        result["tags"] = _tags(raw.get("tags"))
    if "overrides" in raw:
        result["overrides"] = _overrides(raw.get("overrides"))
    if "status" in raw:
        result["status"] = _status(raw.get("status"), strict)
    if "created_at" in raw:
        result["created_at"] = _stamp(raw.get("created_at"), result["created_at"])
    result["updated_at"] = now
    return result


def _item(raw, now: float) -> dict | None:
    """One stored entry, normalized; None when it has no usable identity."""
    base = _blank(now)
    if not isinstance(raw, dict):
        return None
    item = _apply(base, raw, now, strict=False)
    item["id"] = _text(raw.get("id"), 64) or uuid.uuid4().hex[:12]
    item["created_at"] = _stamp(raw.get("created_at"), now)
    item["updated_at"] = _stamp(raw.get("updated_at"), item["created_at"])
    return item


def _load() -> list:
    raw = jsonfile.read_json(_file(), _default())
    rows = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return []
    now = time.time()
    found: list = []
    for row in rows[:LIMIT]:
        item = _item(row, now)
        if item is not None:
            found.append(item)
    return found


def _save(items: list) -> dict:
    jsonfile.write_json(_file(), {"items": items})
    return {"items": items}


def read() -> dict:
    return {"items": _load()}


def find(item_id: str) -> dict | None:
    for item in _load():
        if item["id"] == item_id:
            return item
    return None


def add(draft: dict) -> dict:
    if not isinstance(draft, dict):
        raise ValueError("notebook draft must be an object")
    items = _load()
    if len(items) >= LIMIT:
        raise ValueError("notebook is full (" + str(LIMIT) + " items)")
    now = time.time()
    item = _apply(_blank(now), draft, now, strict=True)
    title = _text(draft.get("title"), MAX_TITLE)
    if not title:
        raise ValueError("title is required")
    item["title"] = title
    item["id"] = _text(draft.get("id"), 64) or uuid.uuid4().hex[:12]
    items.insert(0, item)
    return _save(items)


def update(item_id: str, patch) -> dict:
    if not isinstance(patch, dict):
        raise ValueError("notebook patch must be an object")
    items = _load()
    now = time.time()
    for index, item in enumerate(items):
        if item["id"] != item_id:
            continue
        merged = _apply(item, patch, now, strict=True)
        if not merged["title"]:
            raise ValueError("title is required")
        items[index] = merged
        return _save(items)
    raise ValueError("notebook item not found: " + str(item_id))


def remove(item_id: str) -> dict:
    items = _load()
    kept = [item for item in items if item["id"] != item_id]
    if len(kept) == len(items):
        raise ValueError("notebook item not found: " + str(item_id))
    return _save(kept)


def set_status(item_id: str, status: str, run_name: str = "") -> dict | None:
    """Update one entry from the queue; a missing entry is not an error."""
    items = _load()
    now = time.time()
    for index, item in enumerate(items):
        if item["id"] != item_id:
            continue
        item = dict(item)
        item["status"] = _status(status, strict=True)
        item["updated_at"] = now
        if run_name:
            item["run_name"] = _text(run_name, MAX_NAME)
        items[index] = item
        return _save(items)
    return None

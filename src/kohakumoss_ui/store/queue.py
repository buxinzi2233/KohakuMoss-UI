"""Serial run queue: one trainer process at a time, settled by a 5 s tick.

Every transition is decided from the managed pid registry (contract.state) plus
the run summary (contract.runs); the queue never guesses. All process-touching
functions accept injected launcher / status / alive / stopper callables so the
state machine can be exercised without spawning a trainer.

States: waiting -> starting -> running -> done | failed, plus canceled and the
retry edge back to waiting. Guards before a launch: paused, an active item, any
alive managed run, and an existing run directory.
"""

import threading
import time
import uuid

from .. import paths
from ..contract import launch, runs, state
from . import jsonfile, notebook


FILE = "queue.json"
LIMIT = 200
STATUSES = ("waiting", "starting", "running", "done", "failed", "canceled")
ACTIVE = ("starting", "running")
START_LOST = 30.0
MAX_ERROR = 400
MAX_NAME = 120
MAX_TEXT = 500
MAX_OVERRIDES = 200
MAX_OVERRIDE_KEY = 80
MAX_OVERRIDE_VALUE = 200
PAUSED_BY = (None, "user", "manual")

_LOCK = threading.Lock()


def _file():
    return paths.state_dir() / FILE


def _text(value, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _stamp(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


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


def _slug(value) -> str:
    """Run name from a free-form title; empty when nothing usable is left."""
    text = _text(value, MAX_NAME).lower()
    folded = [char if char.isalnum() else "-" for char in text]
    result = "".join(folded)
    while "--" in result:
        result = result.replace("--", "-")
    return result.strip("-")[:60]


def _default() -> dict:
    return {"paused": False, "pausedBy": None, "items": [], "version": 0}


def _blank(now: float) -> dict:
    return {
        "id": "",
        "name": "",
        "config": "",
        "overrides": {},
        "status": "waiting",
        "notebookId": "",
        "steps": 0,
        "step": 0,
        "created_at": now,
        "updated_at": now,
        "started_at": 0.0,
        "finished_at": 0.0,
        "pid": 0,
        "error": "",
    }


def _item(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    now = time.time()
    item = _blank(now)
    item["id"] = _text(raw.get("id"), 64) or uuid.uuid4().hex[:12]
    item["name"] = _text(raw.get("name") or raw.get("runName") or raw.get("run_name"), MAX_NAME)
    item["config"] = _text(raw.get("config"))
    item["overrides"] = _overrides(raw.get("overrides"))
    status = _text(raw.get("status"), 40).lower()
    item["status"] = status if status in STATUSES else "waiting"
    item["notebookId"] = _text(raw.get("notebookId") or raw.get("notebook_id"), 64)
    item["steps"] = max(0, _int(raw.get("steps")))
    item["step"] = max(0, _int(raw.get("step")))
    item["created_at"] = _stamp(raw.get("created_at"), now)
    item["updated_at"] = _stamp(raw.get("updated_at"), item["created_at"])
    item["started_at"] = _stamp(raw.get("started_at"), 0.0)
    item["finished_at"] = _stamp(raw.get("finished_at"), 0.0)
    item["pid"] = max(0, _int(raw.get("pid")))
    item["error"] = _text(raw.get("error"), MAX_ERROR)
    return item


def _load() -> dict:
    raw = jsonfile.read_json(_file(), _default())
    payload = _default()
    payload["paused"] = bool(raw.get("paused"))
    paused_by = raw.get("pausedBy")
    payload["pausedBy"] = paused_by if paused_by in PAUSED_BY else None
    payload["version"] = max(0, _int(raw.get("version")))
    rows = raw.get("items")
    if isinstance(rows, list):
        found: list = []
        for row in rows[:LIMIT]:
            item = _item(row)
            if item is not None:
                found.append(item)
        payload["items"] = found
    return payload


def _save(payload: dict) -> None:
    jsonfile.write_json(_file(), payload)


def view(payload: dict) -> dict:
    items = payload.get("items") or []
    current = None
    for item in items:
        if item.get("status") in ACTIVE:
            current = dict(item)
            break
    return {
        "paused": bool(payload.get("paused")),
        "pausedBy": payload.get("pausedBy"),
        "current": current,
        "items": [dict(item) for item in items],
        "version": max(0, _int(payload.get("version"))),
    }


def read() -> dict:
    with _LOCK:
        return view(_load())


def _index(items: list, item_id: str) -> int:
    wanted = _text(item_id, 64)
    if not wanted:
        raise ValueError("item id is required")
    for index, item in enumerate(items):
        if item.get("id") == wanted:
            return index
    raise ValueError("queue item not found: " + wanted)


def _find(payload: dict, item_id: str) -> dict:
    return payload["items"][_index(payload["items"], item_id)]


def _bump(payload: dict) -> None:
    payload["version"] = max(0, _int(payload.get("version"))) + 1


def _notebook_status(item_id: str, status: str, run_name: str = "") -> None:
    try:
        notebook.set_status(item_id, status, run_name)
    except Exception:
        pass


def _writeback(item: dict) -> None:
    """Tell the notebook how a queued entry ended up."""
    item_id = item.get("notebookId") or ""
    if not item_id:
        return
    status = "trained" if item.get("status") == "done" else "failed"
    _notebook_status(item_id, status, str(item.get("name") or ""))


def add(draft: dict) -> dict:
    """Append one waiting item; the run name defaults from the title."""
    if not isinstance(draft, dict):
        raise ValueError("queue draft must be an object")
    with _LOCK:
        payload = _load()
        if len(payload["items"]) >= LIMIT:
            raise ValueError("queue is full (" + str(LIMIT) + " items)")
        now = time.time()
        item = _blank(now)
        item["id"] = uuid.uuid4().hex[:12]
        item["name"] = _text(
            draft.get("name") or draft.get("runName") or draft.get("run_name"), MAX_NAME
        )
        item["config"] = _text(draft.get("config"))
        item["overrides"] = _overrides(draft.get("overrides"))
        item["notebookId"] = _text(draft.get("notebookId") or draft.get("notebook_id"), 64)
        item["steps"] = max(0, _int(draft.get("steps")))
        if not item["name"]:
            item["name"] = _slug(draft.get("title") or "")
        if not item["config"]:
            raise ValueError("config is required")
        payload["items"].append(item)
        _bump(payload)
        _save(payload)
        if item["notebookId"]:
            _notebook_status(item["notebookId"], "queued", item["name"])
        return view(payload)


def move(item_id: str, direction: str) -> dict:
    """Swap one item with its neighbour; edges are a no-op."""
    step = _text(direction, 10).lower()
    if step not in ("up", "down"):
        raise ValueError("direction must be up or down")
    with _LOCK:
        payload = _load()
        items = payload["items"]
        index = _index(items, item_id)
        target = index - 1 if step == "up" else index + 1
        if 0 <= target < len(items):
            now = time.time()
            items[index], items[target] = items[target], items[index]
            items[index]["updated_at"] = now
            items[target]["updated_at"] = now
            _bump(payload)
            _save(payload)
        return view(payload)


def update(item_id: str, patch) -> dict:
    """Edit a waiting item; anything active or finished is read-only."""
    if not isinstance(patch, dict):
        raise ValueError("queue patch must be an object")
    with _LOCK:
        payload = _load()
        item = _find(payload, item_id)
        if item.get("status") != "waiting":
            raise ValueError("only waiting items can be edited")
        if "name" in patch:
            item["name"] = _text(patch.get("name"), MAX_NAME)
        if "config" in patch:
            item["config"] = _text(patch.get("config"))
        if "overrides" in patch:
            item["overrides"] = _overrides(patch.get("overrides"))
        if "steps" in patch:
            item["steps"] = max(0, _int(patch.get("steps")))
        if not item["config"]:
            raise ValueError("config is required")
        item["updated_at"] = time.time()
        _bump(payload)
        _save(payload)
        return view(payload)


def remove(item_id: str) -> dict:
    """Drop one item; active items must be canceled first."""
    with _LOCK:
        payload = _load()
        item = _find(payload, item_id)
        if item.get("status") in ACTIVE:
            raise ValueError("active item cannot be removed; cancel it first")
        payload["items"] = [row for row in payload["items"] if row.get("id") != item["id"]]
        _bump(payload)
        _save(payload)
        return view(payload)


def control(action: str, item_id: str = "", stopper=None) -> dict:
    """pause | resume | retry | cancel | clear-done."""
    stopper = launch.stop if stopper is None else stopper
    name = _text(action, 20).lower()
    with _LOCK:
        payload = _load()
        if name == "pause":
            payload["paused"] = True
            payload["pausedBy"] = "user"
        elif name == "resume":
            payload["paused"] = False
            payload["pausedBy"] = None
        elif name == "retry":
            item = _find(payload, item_id)
            if item.get("status") not in ("failed", "canceled"):
                raise ValueError("only failed or canceled items can be retried")
            item["status"] = "waiting"
            item["error"] = ""
            item["pid"] = 0
            item["step"] = 0
            item["step"] = 0
            item["started_at"] = 0.0
            item["finished_at"] = 0.0
            item["updated_at"] = time.time()
        elif name == "cancel":
            item = _find(payload, item_id)
            status = item.get("status")
            if status in ACTIVE:
                try:
                    stopper(str(item.get("name") or ""))
                except Exception:
                    pass
            elif status != "waiting":
                raise ValueError("only waiting or active items can be canceled")
            now = time.time()
            item["status"] = "canceled"
            item["finished_at"] = now
            item["updated_at"] = now
            _writeback(item)
        elif name == "clear-done":
            payload["items"] = [row for row in payload["items"] if row.get("status") != "done"]
        else:
            raise ValueError("unknown queue action: " + (name or "(empty)"))
        _bump(payload)
        _save(payload)
        return view(payload)


def pause_for_manual() -> dict:
    """A manual START RUN wins: pause the queue instead of racing it."""
    with _LOCK:
        payload = _load()
        active = any(item.get("status") in ACTIVE for item in payload["items"])
        if active and not payload.get("paused"):
            payload["paused"] = True
            payload["pausedBy"] = "manual"
            _bump(payload)
            _save(payload)
        return view(payload)


def _summary(root, name: str):
    if not name:
        return None
    try:
        path = root.runs_dir / name
        if not path.is_dir():
            return None
        return runs.summarize(root, path, None)
    except Exception:
        return None


def _assign(item: dict, key: str, value) -> bool:
    if item.get(key) == value:
        return False
    item[key] = value
    return True


def _settle(root, payload: dict, status_fn, alive_fn, moment: float) -> bool:
    """Reconcile starting/running items with the process registry."""
    changed = False
    for item in payload["items"]:
        if item.get("status") not in ACTIVE:
            continue
        name = str(item.get("name") or "")
        managed = None
        try:
            managed = status_fn(name)
        except Exception:
            managed = None
        pid = _int((managed or {}).get("pid") or item.get("pid"))
        if managed is not None:
            alive = bool(managed.get("alive"))
        else:
            alive = bool(pid) and bool(alive_fn(pid))
        summary = _summary(root, name)
        if summary is not None:
            changed |= _assign(item, "step", max(0, _int(summary.get("step"))))
            changed |= _assign(
                item, "steps", max(0, _int(item.get("steps")) or _int(summary.get("steps")))
            )
        if alive:
            changed |= _assign(item, "status", "running")
            changed |= _assign(item, "error", "")
            continue
        lost = (
            item.get("status") == "starting"
            and not pid
            and moment - _stamp(item.get("started_at"), moment) < START_LOST
        )
        if lost:
            continue
        steps = max(0, _int(item.get("steps")))
        step = max(0, _int(item.get("step")))
        finished = steps > 0 and step >= steps
        changed |= _assign(item, "status", "done" if finished else "failed")
        if not finished:
            changed |= _assign(item, "error", "process exited before the last step")
        changed |= _assign(item, "finished_at", moment)
        changed |= _assign(item, "updated_at", moment)
        _writeback(item)
    return changed


def _any_live_managed(alive_fn) -> bool:
    try:
        entries = state.all_entries()
    except Exception:
        return False
    for entry in entries.values():
        try:
            if alive_fn(_int(entry.get("pid"))):
                return True
        except Exception:
            continue
    return False


def _maybe_start(root, payload: dict, launcher, alive_fn, moment: float) -> bool:
    """Launch the first waiting item when every guard allows it."""
    if payload.get("paused"):
        return False
    if any(item.get("status") in ACTIVE for item in payload["items"]):
        return False
    if _any_live_managed(alive_fn):
        return False
    target = None
    for item in payload["items"]:
        if item.get("status") == "waiting":
            target = item
            break
    if target is None:
        return False
    if not target.get("config"):
        _assign(target, "status", "failed")
        _assign(target, "error", "config is required")
        _assign(target, "finished_at", moment)
        _writeback(target)
        return True
    name = str(target.get("name") or "")
    if name:
        try:
            exists = (root.runs_dir / name).exists()
        except OSError:
            exists = False
        if exists:
            _assign(target, "status", "failed")
            _assign(target, "error", "run name already exists: " + name)
            _assign(target, "finished_at", moment)
            _writeback(target)
            return True
    target["status"] = "starting"
    target["started_at"] = moment
    target["updated_at"] = moment
    target["error"] = ""
    target["pid"] = 0
    try:
        result = launcher(root, target["config"], dict(target.get("overrides") or {}), name or None)
    except Exception as exc:
        target["status"] = "failed"
        target["error"] = _text(type(exc).__name__ + ": " + str(exc), MAX_ERROR)
        target["finished_at"] = moment
        _writeback(target)
        return True
    if isinstance(result, dict):
        target["name"] = _text(result.get("name") or name, MAX_NAME)
        target["pid"] = max(0, _int(result.get("pid")))
        if result.get("alive") is False:
            target["status"] = "failed"
            target["error"] = "trainer process did not stay alive"
            target["finished_at"] = moment
            _writeback(target)
            return True
    target["status"] = "running"
    return True


def tick(root, launcher=None, status_fn=None, alive_fn=None, now=None) -> dict:
    """One scheduler pass: settle active items, then maybe start the next."""
    launcher = launch.start if launcher is None else launcher
    status_fn = state.status if status_fn is None else status_fn
    alive_fn = state.pid_alive if alive_fn is None else alive_fn
    moment = time.time() if now is None else float(now)
    with _LOCK:
        payload = _load()
        changed = _settle(root, payload, status_fn, alive_fn, moment)
        if _maybe_start(root, payload, launcher, alive_fn, moment):
            changed = True
        if changed:
            _bump(payload)
            _save(payload)
        return view(payload)

"""Aggregate metrics.jsonl samples into calendar activity and run history.

The only module that knows which metrics fields carry wall-clock time and the
step counter: ``time`` (epoch seconds) and ``step`` (cumulative). Everything
else consumes the aggregates produced here.

Two read paths:

* :func:`daily_activity` - per-day steps, active seconds and run counts for a
  calendar heatmap. Per-run work is cached by (path, size, mtime) for
  CACHE_TTL seconds so a 5 s UI refresh does not re-parse whole metric tails.
* :func:`run_history` - one row per run, reusing :mod:`.runs` status so the
  ledger and the run list never disagree.

Day attribution: a step/time delta belongs to the local calendar date of the
*later* sample of the pair. Gaps are clamped to GAP_CAP seconds per pair and
DAY_CAP seconds per day so a paused run cannot invent a 3-day session.
"""

from datetime import date, datetime, timedelta
import threading
import time
from pathlib import Path

from . import metrics, runs
from .root import TrainerRoot


MAX_RUNS = 200
MAX_TAIL = 2_000_000
MAX_DAYS = 3660
DEFAULT_DAYS = 365
GAP_CAP = 3600
DAY_CAP = 86_400
CACHE_TTL = 30.0
CACHE_LIMIT = 512

_LOCK = threading.Lock()
_CACHE: dict = {}


def cache_clear() -> None:
    """Drop every cached per-run aggregate."""
    with _LOCK:
        _CACHE.clear()


def _local_date(stamp: float) -> str:
    return datetime.fromtimestamp(stamp).date().isoformat()


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _fingerprint(path: Path):
    try:
        info = path.stat()
    except OSError:
        return None
    return (str(path), int(info.st_size), round(info.st_mtime, 6))


def _samples(path: Path) -> list:
    """(time, step) pairs from the tail of one metrics file, oldest first."""
    points: list = []
    for row in metrics.parse_lines(metrics.read_tail_text(path, MAX_TAIL)):
        try:
            stamp = float(row.get("time"))
            step = int(row.get("step"))
        except (TypeError, ValueError):
            continue
        if stamp <= 0 or step < 0:
            continue
        points.append((stamp, step))
    points.sort(key=lambda pair: pair[0])
    return points


def _empty_scan() -> dict:
    return {
        "days": {},
        "last_step": 0,
        "delta_steps": 0,
        "seconds": 0,
        "first": None,
        "last": None,
    }


def _scan(path: Path) -> dict:
    """Per-run day buckets plus the step/time span, cached for CACHE_TTL."""
    fingerprint = _fingerprint(path)
    if fingerprint is None:
        return _empty_scan()
    moment = time.time()
    with _LOCK:
        cached = _CACHE.get(fingerprint)
        if cached is not None and moment - cached[0] < CACHE_TTL:
            return cached[1]

    points = _samples(path)
    buckets: dict = {}
    delta_steps = 0
    seconds = 0
    for (start, low), (end, high) in zip(points, points[1:]):
        gained = max(0, high - low)
        gap = min(max(end - start, 0.0), GAP_CAP)
        day = _local_date(end)
        bucket = buckets.setdefault(day, {"steps": 0, "seconds": 0})
        bucket["steps"] += gained
        bucket["seconds"] = min(DAY_CAP, bucket["seconds"] + gap)
        delta_steps += gained
        seconds += gap

    record = {
        "days": buckets,
        "last_step": points[-1][1] if points else 0,
        "delta_steps": delta_steps,
        "seconds": seconds,
        "first": points[0][0] if points else None,
        "last": points[-1][0] if points else None,
    }
    with _LOCK:
        _CACHE[fingerprint] = (moment, record)
        if len(_CACHE) > CACHE_LIMIT:
            overflow = len(_CACHE) - CACHE_LIMIT
            for key in sorted(_CACHE, key=lambda item: _CACHE[item][0])[:overflow]:
                _CACHE.pop(key, None)
    return record


def _metrics_paths(root: TrainerRoot) -> list:
    """Metrics files of the newest MAX_RUNS run directories, newest first."""
    base = root.runs_dir
    if not base.is_dir():
        return []
    found: list = []
    try:
        for item in base.iterdir():
            if not item.is_dir():
                continue
            path = item / "metrics.jsonl"
            if not path.is_file():
                continue
            found.append((max(_mtime(path), _mtime(item)), path))
    except OSError:
        return []
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [path for _stamp, path in found[:MAX_RUNS]]


def _clamp_days(days) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return max(1, min(MAX_DAYS, value))


def _window(days: int, end: date) -> list:
    start = end - timedelta(days=days - 1)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(days)]


def _streaks(flags: list) -> tuple:
    """(current streak, longest streak) over a list of day-activity flags."""
    longest = 0
    current = 0
    for active in flags:
        current = current + 1 if active else 0
        longest = max(longest, current)
    index = len(flags) - 1
    if index >= 0 and not flags[index]:
        index -= 1
    streak = 0
    while index >= 0 and flags[index]:
        streak += 1
        index -= 1
    return streak, longest


def daily_activity(root: TrainerRoot, days: int = DEFAULT_DAYS, year=None) -> dict:
    """Recent activity or a complete calendar year, including zero/future days."""
    span = _clamp_days(days)
    today = date.today()
    if year is not None:
        year = int(year)
        if not 1970 <= year <= today.year:
            raise ValueError("year outside supported activity range")
        start, end = date(year, 1, 1), date(year, 12, 31)
        window = _window((end - start).days + 1, end)
    else:
        window = _window(span, today)
    buckets: dict = {}
    names: dict = {}
    for path in _metrics_paths(root):
        record = _scan(path)
        if not record["days"]:
            continue
        run_name = path.parent.name
        for day, value in record["days"].items():
            if day > today.isoformat():
                continue
            bucket = buckets.setdefault(day, {"steps": 0, "seconds": 0})
            bucket["steps"] += value["steps"]
            bucket["seconds"] = min(DAY_CAP, bucket["seconds"] + value["seconds"])
            names.setdefault(day, set()).add(run_name)

    listed: list = []
    active_names: set = set()
    for day in window:
        bucket = buckets.get(day)
        who = names.get(day) or set()
        active_names.update(who)
        listed.append(
            {
                "date": day,
                "steps": bucket["steps"] if bucket else 0,
                "seconds": int(round(bucket["seconds"])) if bucket else 0,
                "runs": len(who),
            }
        )

    flags = [bool(entry["steps"] or entry["seconds"]) for entry in listed]
    streak, longest = _streaks(flags)
    years = sorted({int(day[:4]) for day, value in buckets.items()
                    if (value["steps"] or value["seconds"]) and day <= today.isoformat()})
    if today.year not in years:
        years = sorted(years + [today.year])
    years = list(range(min(years), today.year + 1))
    return {
        "days": listed,
        "totals": {
            "days": sum(1 for active in flags if active),
            "steps": sum(entry["steps"] for entry in listed),
            "seconds": sum(entry["seconds"] for entry in listed),
            "runs": len(active_names),
        },
        "streak": streak,
        "longest": longest,
        "years": years,
        "start": listed[0]["date"] if listed else "",
        "end": listed[-1]["date"] if listed else "",
        "today": today.isoformat(),
        "generated_at": time.time(),
    }


def run_history(root: TrainerRoot) -> dict:
    """One row per run: name, start, recorded steps, active seconds, exports."""
    listed: list = []
    for summary in runs.list_runs(root):
        name = str(summary.get("name") or "")
        record = _empty_scan()
        if name:
            record = _scan(root.runs_dir / name / "metrics.jsonl")
        # Only this contract layer reads the trainer's saved DATASETS schema.
        config = runs._read_json(root.runs_dir / name / "config.json")
        datasets = []
        seen = set()
        raw_datasets = config.get("DATASETS")
        for item in raw_datasets if isinstance(raw_datasets, list) else []:
            if isinstance(item, str):
                item = {"path": item}
            if not isinstance(item, dict) or not item.get("path"):
                continue
            path = Path(str(item["path"])).expanduser()
            if not path.is_absolute():
                path = root.root / path
            path = str(path.resolve())
            source = str(item.get("source") or "")
            if (source, path) in seen:
                continue
            seen.add((source, path))
            datasets.append({"path": path, "source": source,
                             "name": str(item.get("name") or Path(path).name)})
        listed.append(
            {
                "name": name,
                "started_at": float(record["first"] or summary.get("started_at") or 0.0),
                "steps": int(record["last_step"]),
                "seconds": int(round(record["seconds"])),
                "status": str(summary.get("status") or "unknown"),
                "exports": [str(item) for item in (summary.get("exports") or [])],
                "datasets": datasets,
                "algo": config.get("ALGO"),
                "dim": config.get("DIM"),
                "lr": config.get("LR"),
            }
        )
    return {"runs": listed, "server_time": time.time()}

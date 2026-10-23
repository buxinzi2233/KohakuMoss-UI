"""Read metrics.jsonl incrementally and downsample it for charts."""

import json
from pathlib import Path


def read_tail_text(path: Path, max_bytes: int = 2_000_000) -> str:
    """Last chunk of a text file, starting on a line boundary."""
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
            handle.readline()
        return handle.read().decode("utf-8", "replace")


def parse_lines(text: str) -> list[dict]:
    points: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            points.append(item)
    return points


def last_metric(path: Path) -> dict | None:
    """The newest valid metrics row, reading only the file's tail."""
    points = parse_lines(read_tail_text(path, 262_144))
    return points[-1] if points else None


def tail_metrics(path: Path, limit: int = 800) -> list[dict]:
    """Newest rows, downsampled to at most `limit` points, first and last kept."""
    points = parse_lines(read_tail_text(path))
    if len(points) <= limit:
        return points
    stride = len(points) / float(limit)
    sampled = [points[int(index * stride)] for index in range(limit - 1)]
    sampled.append(points[-1])
    return sampled

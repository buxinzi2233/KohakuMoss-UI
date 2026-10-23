"""Tail log.txt, and read the bytes appended since an offset for SSE."""

from pathlib import Path


def tail_lines(path: Path, count: int = 400, max_bytes: int = 1_000_000) -> list[str]:
    try:
        size = path.stat().st_size
    except OSError:
        return []
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
            handle.readline()
        data = handle.read()
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[-count:] if count > 0 else lines


def read_from(path: Path, offset: int, max_bytes: int = 512_000) -> tuple[int, list[str]]:
    """Return (new offset, complete lines appended since offset)."""
    try:
        size = path.stat().st_size
    except OSError:
        return 0, []
    if offset < 0 or offset > size:
        offset = max(0, size - max_bytes)
    with path.open("rb") as handle:
        if size - offset > max_bytes:
            offset = size - max_bytes
            handle.seek(offset)
            handle.readline()
            offset = handle.tell()
        else:
            handle.seek(offset)
        data = handle.read(max_bytes)
    cut = data.rfind(b"\n")
    if cut < 0:
        return offset, []
    complete = data[: cut + 1]
    lines = complete.decode("utf-8", "replace").splitlines()
    return offset + len(complete), lines

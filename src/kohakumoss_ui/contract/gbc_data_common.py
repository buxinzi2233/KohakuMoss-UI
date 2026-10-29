"""Conservative stdlib path/JSON checks, also usable for declared scan counts."""

import json
import os
import re
import stat
from pathlib import Path

MAX_JSON = 16 << 20
MAX_LINE = 2 << 20
MAX_IMAGE = 32 << 20
SHARD_FILES = ("images.zip", "graphs.db", "meta.db", "manifest.parquet")


class Fault(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def require(ok, code, message):
    if not ok:
        raise Fault(code, message)


def integer(value, minimum=0, maximum=2**63 - 1):
    return type(value) is int and minimum <= value <= maximum


def safe_path(value, kind="file"):
    require(isinstance(value, str) and bool(value) and "\x00" not in value,
            "unsafe_path", "Path must be a nonempty string")
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts, "unsafe_path", "Absolute non-traversing path required")
    for part in reversed((path, *path.parents)):
        st = part.lstat()
        require(not stat.S_ISLNK(st.st_mode), "unsafe_path", "Symlinks are forbidden")
        if part != path or kind == "dir":
            require(stat.S_ISDIR(st.st_mode), "unsafe_path", "Expected a directory")
        else:
            require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1,
                    "unsafe_path", "Only single-link regular files are allowed")
    return path


def read_bytes(path, maximum):
    path = safe_path(str(path))
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1, "unsafe_path", "Unsafe file")
        require(st.st_size <= maximum, "limit_exceeded", "File exceeds read limit")
        data = f.read(maximum + 1)
        require(len(data) <= maximum, "limit_exceeded", "File exceeds read limit")
        return data


def json_value(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "invalid_request", "Duplicate JSON object key")
            result[key] = value
        return result

    def constant(_value):
        raise Fault("invalid_request", "Non-finite JSON number")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def flat_name(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", value) is not None


def dataset_info(path, selected=None):
    root = safe_path(str(path), "dir")
    info = json_value(read_bytes(root / "dataset.json", MAX_JSON))
    require(isinstance(info, dict) and info.get("format") == "kohakugbc-dataset"
            and type(info.get("version")) is int and info["version"] == 1,
            "invalid_dataset", "Expected kohakugbc-dataset version 1")
    require(integer(info.get("samples")) and isinstance(info.get("shards"), list),
            "invalid_dataset", "Invalid dataset counts")
    names = set()
    for shard in info["shards"]:
        require(isinstance(shard, dict) and flat_name(shard.get("name"))
                and integer(shard.get("samples")) and integer(shard.get("bytes")),
                "invalid_dataset", "Invalid shard declaration")
        require(shard["name"] not in names, "invalid_dataset", "Duplicate shard name")
        names.add(shard["name"])
    require(sum(s["samples"] for s in info["shards"]) == info["samples"],
            "invalid_dataset", "Declared total differs from shard sum")
    if selected is not None:
        require(isinstance(selected, list) and len(selected) > 0
                and all(isinstance(s, str) for s in selected),
                "invalid_request", "shards must be a nonempty list")
        require(len(set(selected)) == len(selected) and set(selected) <= names,
                "invalid_request", "Unknown or duplicate selected shard")
    shards = [s for s in info["shards"] if selected is None or s["name"] in selected]
    return root, info, shards


def declared_counts(path, shards=None):
    """No heavy imports or content-validity claim on the UI scan path."""
    result = {"images": None, "captions": None, "files": None, "truncated": False,
              "declaredSamples": None, "manifestRows": None, "validatedSamples": None,
              "actualBytes": None, "countStatus": "unverified"}
    try:
        _, _, selected = dataset_info(path, shards)
        result["declaredSamples"] = sum(s["samples"] for s in selected)
        result["countStatus"] = "declared"
    except (Fault, OSError, ValueError, TypeError):
        result["countStatus"] = "unavailable"
    return result

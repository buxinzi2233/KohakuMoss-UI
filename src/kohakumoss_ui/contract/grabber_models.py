"""Bounded local ONNX discovery; never opens models or starts inference."""

import os
import stat
import time
from pathlib import Path

MAX_DEPTH = 2
MAX_ENTRIES = 4096
MAX_MODELS = 256
MAX_SECONDS = 2.0


def _is_junction(entry):
    """Windows junctions are reparse points rather than symlinks (3.12+)."""
    probe = getattr(entry, "is_junction", None)
    return bool(probe and probe())


def scan_models(directory):
    """Scan a real directory, with no-follow traversal and explicit truncation."""
    root = Path(directory).expanduser()
    if root.is_symlink():
        raise ValueError("Model scan directory must not be a symlink")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    result = {"directory": str(root), "models": [], "truncated": False}
    deadline = time.monotonic() + MAX_SECONDS
    visited = 0

    def budget():
        nonlocal visited
        if (visited >= MAX_ENTRIES or len(result["models"]) >= MAX_MODELS
                or time.monotonic() >= deadline):
            result["truncated"] = True
            return False
        visited += 1
        return True

    def tags_beside(path, directory_fd):
        for name in ("selected_tags.csv", path.stem + ".csv"):
            try:
                if directory_fd is None:
                    info = os.stat(path.parent / name, follow_symlinks=False)
                else:
                    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode):
                return str(path.parent / name)
        return None

    def record(entry, path, tags):
        result["models"].append({"name": entry.name, "model_path": str(path), "tags_path": tags})

    def walk(fd, relative, depth):
        with os.scandir(fd) as entries:
            for entry in entries:
                if not budget():
                    return
                if entry.name.startswith(".") or entry.is_symlink():
                    continue
                path = root / relative / entry.name
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if depth >= MAX_DEPTH:
                            result["truncated"] = True
                            continue
                        child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                        try:
                            walk(child, relative / entry.name, depth + 1)
                        finally:
                            os.close(child)
                    elif entry.is_file(follow_symlinks=False) and path.suffix.lower() == ".onnx":
                        record(entry, path, tags_beside(path, fd))
                except OSError:
                    # A disappearing/unreadable child makes the listing incomplete.
                    result["truncated"] = True

    def walk_path(parent, relative, depth):
        # Windows has no dir_fd support, so every child is addressed by path.
        with os.scandir(parent) as entries:
            for entry in entries:
                if not budget():
                    return
                if entry.name.startswith(".") or entry.is_symlink() or _is_junction(entry):
                    continue
                path = root / relative / entry.name
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if depth >= MAX_DEPTH:
                            result["truncated"] = True
                            continue
                        walk_path(path, relative / entry.name, depth + 1)
                    elif entry.is_file(follow_symlinks=False) and path.suffix.lower() == ".onnx":
                        record(entry, path, tags_beside(path, None))
                except OSError:
                    result["truncated"] = True

    if os.name == "nt":
        walk_path(root, Path(), 0)
    else:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            walk(fd, Path(), 0)
        finally:
            os.close(fd)
    result["models"].sort(key=lambda model: model["model_path"].casefold())
    return result

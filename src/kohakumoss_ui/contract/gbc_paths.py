"""Authorized-root traversal with no-follow descriptors and bounded snapshots."""

import hashlib
import os
import stat
from pathlib import Path

from .gbc_schema import fail, text

LIMIT = 256 * 1024 * 1024


def safe_path(value, directory=True):
    path = Path(text(str(value)))
    if not path.is_absolute() or ".." in path.parts:
        fail("UNSAFE_PATH", "An absolute normalized path is required")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            last = index == len(path.parts) - 2
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if not last or directory:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode) and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                fail("UNSAFE_PATH", "Only unlinked regular files and directories are supported")
        info = os.fstat(fd)
        return path, [info.st_dev, info.st_ino, info.st_size if not directory else 0,
                      info.st_mtime_ns if not directory else 0]
    except OSError:
        fail("UNSAFE_PATH", "Path is missing, linked, or inaccessible")
    finally:
        os.close(fd)


def runtime_path(value):
    """Pin an explicitly trusted venv launcher, including its final symlink.

    Data roots never permit symlinks. A venv Python launcher is different: its
    exact link and resolved binary are both registered and checked every use.
    """
    path = Path(text(value, "python"))
    if not path.is_absolute() or ".." in path.parts:
        fail("UNSAFE_PATH", "Runtime path must be absolute")
    safe_path(path.parent)
    info = path.lstat()
    link = os.readlink(path) if stat.S_ISLNK(info.st_mode) else None
    resolved = path.resolve(strict=True) if link is not None else path
    _, target = safe_path(resolved, directory=False)
    cfg = path.parent.parent / "pyvenv.cfg"
    cfg_hash = hashlib.sha256(read_file(cfg, 65536)).hexdigest() if cfg.exists() else None
    return path, {"launcher": [info.st_dev, info.st_ino, info.st_mtime_ns],
                  "link": link, "resolved": str(resolved), "binary": target, "venvConfig": cfg_hash}


def within(root, relative):
    text(relative, "relativePath")
    parts = Path(relative).parts
    if Path(relative).is_absolute() or any(p == ".." for p in parts) or "\\" in relative:
        fail("UNSAFE_PATH", "Relative path must stay in its registered root")
    return safe_path(Path(root) / relative)[0]


def read_file(path, limit=LIMIT):
    safe_path(path, directory=False)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            fail("LIMIT_EXCEEDED", "Unsafe or oversized input", 413)
        with os.fdopen(os.dup(fd), "rb") as source:
            data = source.read(limit + 1)
        after = os.fstat(fd)
        if len(data) > limit or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            fail("SOURCE_CHANGED", "Input changed during snapshot", 409)
        return data
    finally:
        os.close(fd)


def write_new(path, data):
    safe_path(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(os.dup(fd), "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(fd)


def copy_tree(source, destination):
    """Reject links/special files; never follow archive contents or execute them."""
    source = safe_path(source)[0]
    destination.mkdir(mode=0o700)
    total = 0
    manifest = []
    for base, dirs, files in os.walk(source, followlinks=False):
        rel = Path(base).relative_to(source)
        if len(rel.parts) > 16 or len(manifest) + len(dirs) > 10_000:
            fail("LIMIT_EXCEEDED", "Snapshot file/depth limit exceeded", 413)
        for name in dirs:
            safe_path(Path(base) / name)
            (destination / rel / name).mkdir(mode=0o700)
        for name in files:
            data = read_file(Path(base) / name)
            total += len(data)
            if total > LIMIT or len(manifest) >= 10_000:
                fail("LIMIT_EXCEEDED", "Snapshot exceeds 256 MiB or 10000 files", 413)
            write_new(destination / rel / name, data)
            manifest.append({"path": str(rel / name), "hash": "sha256:" + hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    return manifest

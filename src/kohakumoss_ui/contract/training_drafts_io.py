"""Bounded subprocess transport and private atomic draft storage (POSIX)."""

import contextlib
import hashlib
import os
import selectors
import signal
import stat
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .training_drafts_schema import DraftError, canonical, fail, strict_json

LIMIT = 4 * 1024 * 1024


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def open_directory(path, create=False):
    path = Path(path).absolute()
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if part in (".", ".."):
                fail("Unsafe directory", "UNSAFE_PATH")
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def file_hash(path, limit=20 * 1024**3, *, return_bytes=False):
    """No symlink components, no devices/hardlinks; detects mutation while hashing."""
    path = Path(path)
    parent = open_directory(path.parent)
    try:
        fd = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
    finally:
        os.close(parent)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > limit
        ):
            fail("Unsafe or oversized source file", "UNSAFE_PATH")
        h = hashlib.sha256()
        chunks = []
        size = 0
        while chunk := os.read(fd, 1024 * 1024):
            size += len(chunk)
            if size > limit:
                fail("Source size limit", "LIMIT_EXCEEDED")
            h.update(chunk)
            if return_bytes:
                chunks.append(chunk)
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            fail("Source changed while reading", "SOURCE_CHANGED")
        return b"".join(chunks) if return_bytes else "sha256:" + h.hexdigest()
    finally:
        os.close(fd)


class DraftStore:
    def __init__(self, path):
        self.path = Path(path).absolute()
        self.fd = open_directory(self.path, create=True)
        self.lock = threading.RLock()
        self._verify_dir()

    def _verify_dir(self):
        st = os.fstat(self.fd)
        current = open_directory(self.path)
        try:
            now = os.fstat(current)
        finally:
            os.close(current)
        if (
            (st.st_dev, st.st_ino) != (now.st_dev, now.st_ino)
            or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o700
        ):
            fail("Draft directory must be owned/private/unchanged", "UNSAFE_STORAGE")

    @contextlib.contextmanager
    def transaction(self):
        import fcntl

        with self.lock:
            self._verify_dir()
            fd = os.open(
                "owner.lock",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=self.fd,
            )
            try:
                self._verify_file(os.fstat(fd))
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)

    @staticmethod
    def _verify_file(st):
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o600
            or st.st_nlink != 1
        ):
            fail(
                "Draft file must be a private regular single-link file",
                "UNSAFE_STORAGE",
            )

    def read(self, name):
        self._verify_dir()
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            self._verify_file(os.fstat(fd))
            chunks, size = [], 0
            while chunk := os.read(fd, 65536):
                size += len(chunk)
                if size > LIMIT:
                    fail("State file too large", "LIMIT_EXCEEDED")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(fd)

    def write(self, name, data):
        self._verify_dir()
        if len(data) > LIMIT:
            fail("State size limit", "LIMIT_EXCEEDED")
        # Never silently replace a symlink or foreign/hardlinked state file.
        try:
            self._verify_file(os.stat(name, dir_fd=self.fd, follow_symlinks=False))
        except FileNotFoundError:
            pass
        tmp = ".tmp-" + uuid.uuid4().hex
        fd = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=self.fd,
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            try:
                os.unlink(tmp, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


def worker(root, python, payload, timeout=60):
    request = memoryview(canonical(payload))
    script = Path(__file__).with_name("training_drafts_worker.py")
    argv = [str(python), "-I", "-B", str(script)]
    env = {
        "PATH": os.defpath,
        "HOME": str(root),
        "PYTHONIOENCODING": "utf-8",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "CUDA_VISIBLE_DEVICES": "",
    }
    try:
        proc = subprocess.Popen(
            argv,
            cwd=root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
    except OSError as exc:
        raise DraftError(
            "DEPENDENCY_MISSING", "Trainer interpreter unavailable"
        ) from exc
    buffers = {"out": bytearray(), "err": bytearray()}
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as sel:
            for stream, key in (
                (proc.stdout, "out"),
                (proc.stderr, "err"),
                (proc.stdin, "in"),
            ):
                os.set_blocking(stream.fileno(), False)
                sel.register(
                    stream,
                    selectors.EVENT_WRITE if key == "in" else selectors.EVENT_READ,
                    key,
                )
            while sel.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    fail("Trainer worker timeout", "WORKER_TIMEOUT")
                for key, _ in sel.select(min(remaining, 0.2)):
                    if key.data == "in":
                        count = os.write(key.fd, request[:65536])
                        request = request[count:]
                        if not request:
                            sel.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            sel.unregister(key.fileobj)
                        else:
                            buffers[key.data].extend(chunk)
                            if sum(map(len, buffers.values())) > LIMIT:
                                fail("Trainer worker output limit", "LIMIT_EXCEEDED")
            proc.wait(timeout=max(0.01, deadline - time.monotonic()))
        if proc.returncode:
            fail(
                "Trainer worker exited unexpectedly: "
                + buffers["err"].decode("utf-8", "replace")[-2000:],
                "WORKER_PROTOCOL",
            )
        reply = strict_json(bytes(buffers["out"]))
        if (
            type(reply) is not dict
            or set(reply) != {"ok", "data", "error"}
            or type(reply["ok"]) is not bool
        ):
            fail("Invalid trainer worker envelope", "WORKER_PROTOCOL")
        if not reply["ok"]:
            error = reply["error"]
            if type(error) is not dict or set(error) != {"code", "message"}:
                fail("Invalid worker error", "WORKER_PROTOCOL")
            fail(str(error["message"]), str(error["code"]))
        if type(reply["data"]) is not dict or reply["error"] is not None:
            fail("Invalid worker data", "WORKER_PROTOCOL")
        return reply["data"]
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if not stream.closed:
                stream.close()

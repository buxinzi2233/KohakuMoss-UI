"""One atomic lane shared by GBC and every training launch.

An unreleased persisted intent blocks all launches after a crash, even when a
PID is dead or reused. Recovery is deliberately manual, never guess-and-kill.
"""

import fcntl
import json
import os
import threading

from ..store.gbc import GbcStore
from .gbc_schema import GbcError

_guard = threading.RLock()


class Lease:
    def __init__(self, owner, state=None):
        self.store = GbcStore(state)
        self.fd = None
        self.owner = owner
        with _guard, self.store._directory(create=True) as directory:
            fd = os.open("resource.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            from ..store.annotations import _regular
            try:
                _regular(fd, 16384)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                raw = os.read(fd, 16384)
                if raw.strip():
                    raise GbcError(409, "RECOVERY_REQUIRED", "An unconfirmed resource lease blocks execution")
                self.fd = fd
                self._write({"owner": owner, "ownerPid": os.getpid(), "state": "reserved"})
            except BlockingIOError:
                os.close(fd)
                raise GbcError(409, "BUSY", "Generation or training already owns the resource") from None
            except BaseException:
                os.close(fd)
                raise

    def _write(self, data):
        raw = json.dumps(data).encode()
        # Never create an empty crash window while replacing a live intent.
        os.pwrite(self.fd, raw, 0)
        os.fsync(self.fd)
        os.ftruncate(self.fd, len(raw))
        os.fsync(self.fd)

    def bind(self, pid, identity):
        self._write({"owner": self.owner, "ownerPid": os.getpid(), "pid": pid,
                     "identity": identity, "state": "running"})

    def release(self):
        with _guard:
            if self.fd is not None:
                os.ftruncate(self.fd, 0)
                os.fsync(self.fd)
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)
                self.fd = None

    def abandon(self):
        """Keep the durable fence when group disappearance is unconfirmed."""
        with _guard:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None

"""Bounded process-group lifecycle. The UI imports no model packages."""

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from .gbc_schema import GbcError

LOG_LIMIT = 1024 * 1024


def identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return {"start": fields[19], "group": int(fields[2]), "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}
    except (OSError, ValueError, IndexError):
        return None


def group_alive(group):
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == group and fields[0] != "Z":
                return True
        except (OSError, ValueError, IndexError):
            pass
    return False


class ManagedProcess:
    def __init__(self, python, config, action, cwd, timeout, log_path, lease, finished, output=None):
        self.lock = threading.RLock()
        self.cancel_requested = False
        self.force = False
        self.dropped = 0
        self.size = 0
        self.exit_code = None
        self.lease = lease
        self.finished = finished
        self.timeout = timeout
        self.started = time.monotonic()
        self.log_path = Path(log_path)
        self.log_fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG", "LC_ALL", "VIRTUAL_ENV"}}
            env["PYTHONUNBUFFERED"] = "1"
            self.process = subprocess.Popen(
                [str(python), "-I", str(Path(__file__).with_name("gbc_runtime_worker.py"))],
                cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                shell=False, start_new_session=True, env=env)
            self.identity = identity(self.process.pid)
            if self.identity is None:
                raise GbcError(503, "PROCESS_START_FAILED", "Cannot identify supervisor")
            lease.bind(self.process.pid, self.identity)
            payload = {"action": action, "config": str(config) if config else None,
                       "timeout": timeout, "parent": os.getpid(), "output": str(output or cwd)}
            self.process.stdin.write(json.dumps(payload).encode())
            self.process.stdin.close()
        except BaseException:
            os.close(self.log_fd)
            if hasattr(self, "process"):
                self.process.kill()
                self.process.wait()
                self.process.stdout.close()
            lease.release()
            raise
        self.thread = threading.Thread(target=self._read, name="gbc-process", daemon=True)

    def start_monitor(self):
        self.thread.start()

    def _read(self):
        try:
            while True:
                block = self.process.stdout.read1(8192)
                if not block:
                    break
                with self.lock:
                    keep = block[:max(0, LOG_LIMIT - self.size)]
                    if keep:
                        os.write(self.log_fd, keep)
                        self.size += len(keep)
                    self.dropped += len(block) - len(keep)
            self.exit_code = self.process.wait()
        except OSError:
            # Disk/log pipe failure must not detach a running generator.
            if self.process.poll() is None:
                self.stop(force=True)
            self.exit_code = self.process.wait()
        finally:
            self.process.stdout.close()
            os.close(self.log_fd)
            # A crashed supervisor may leave live children. Keep the fence then.
            clean = not group_alive(self.process.pid)
            if clean:
                self.lease.release()
            else:
                self.lease.abandon()
            self.finished(self, clean)

    def stop(self, force=False):
        with self.lock:
            if self.process.poll() is not None:
                return False
            if identity(self.process.pid) != self.identity:
                raise GbcError(409, "RECOVERY_REQUIRED", "Supervisor identity no longer matches")
            self.cancel_requested = True
            self.force = force
            os.killpg(self.process.pid, signal.SIGKILL if force else signal.SIGTERM)
            return True

    def close(self):
        if self.process.poll() is None:
            self.stop()
            self.thread.join(2)
            if self.process.poll() is None:
                # Service shutdown is a documented bounded teardown, not user STOP.
                self.stop(force=True)
        self.thread.join(3)

    def logs(self, cursor=0, maximum=65536):
        with self.lock:
            fd = os.open(self.log_path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.lseek(fd, min(cursor, self.size), 0)
                raw = os.read(fd, maximum)
            finally:
                os.close(fd)
            return {"text": raw.decode("utf-8", "replace"), "nextCursor": min(cursor, self.size) + len(raw),
                    "dropped": self.dropped, "terminal": self.exit_code is not None}

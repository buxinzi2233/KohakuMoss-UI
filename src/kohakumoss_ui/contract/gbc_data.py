"""Stdlib-only client for the fixed, model-free GBC data worker."""

import json
import os
import queue
import signal
import subprocess
import threading
import time
from pathlib import Path

MAX_PROTOCOL = 16 << 20
MAX_STDERR = 1 << 20
READ_CHUNK = 64 << 10
ACTIONS = frozenset({"inspect", "validate", "page", "pack", "graph_page", "dataset_media", "frozen_media"})


class DataError(Exception):
    """Stable error boundary for services; never treat an error as an empty result."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _terminate(proc):
    """Only the group created by this Popen belongs to this call."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        pass
    proc.wait()


def _exchange(proc, request, timeout):
    """Drain both pipes continuously with bounded queue/memory and a deadline."""
    messages = queue.Queue(maxsize=16)
    stop = threading.Event()
    deadline = time.monotonic() + timeout

    def post(name, data):
        while not stop.is_set():
            try:
                messages.put((name, data), timeout=0.05)
                return
            except queue.Full:
                continue

    def drain(name, pipe):
        try:
            while not stop.is_set():
                data = os.read(pipe.fileno(), READ_CHUNK)
                if not data:
                    break
                post(name, data)
        except OSError:
            pass  # Termination closes pipes; the owner checks status/deadline.
        finally:
            post(name, None)

    def feed():
        try:
            proc.stdin.write(request)
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass  # A failed child is handled through its result/exit status.
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass  # Buffered close may repeat the already-handled broken pipe.

    threads = [threading.Thread(target=drain, args=(name, pipe), daemon=True)
               for name, pipe in (("stdout", proc.stdout), ("stderr", proc.stderr))]
    threads.append(threading.Thread(target=feed, daemon=True))
    for thread in threads:
        thread.start()
    output, counts, finished = bytearray(), {"stdout": 0, "stderr": 0}, set()
    try:
        while len(finished) < 2:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DataError("timeout", "Data worker timed out")
            try:
                name, data = messages.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue
            if data is None:
                finished.add(name)
                continue
            counts[name] += len(data)
            maximum = MAX_PROTOCOL if name == "stdout" else MAX_STDERR
            if counts[name] > maximum:
                raise DataError("limit_exceeded", f"Worker {name} exceeded {maximum} bytes while running")
            if name == "stdout":
                output.extend(data)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DataError("timeout", "Data worker timed out")
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise DataError("timeout", "Data worker timed out") from exc
        return output
    except BaseException:
        _terminate(proc)
        raise
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=1)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if not pipe.closed:
                pipe.close()


def call(python: Path, payload: dict, timeout: float = 120) -> dict:
    """Run only our worker, with JSON on stdin. The interpreter is trusted config."""
    if not isinstance(payload, dict) or not isinstance(payload.get("action"), str) or payload["action"] not in ACTIONS:
        raise DataError("invalid_request", "Unknown data action")
    python = Path(python)
    if not python.is_absolute() or not python.is_file():
        raise DataError("missing_dependency", "Configured Python is not an absolute executable")
    try:
        request = json.dumps(payload, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise DataError("invalid_request", "Payload must be finite JSON") from exc
    if len(request) > MAX_PROTOCOL:
        raise DataError("limit_exceeded", "Request exceeds 16 MiB")
    worker = Path(__file__).with_name("gbc_data_worker.py")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["KOHAKUMOSS_GBC_PARENT_PID"] = str(os.getpid())
    try:
        proc = subprocess.Popen(
            [str(python), "-I", "-B", str(worker)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        raise DataError("missing_dependency", "Cannot start configured Python") from exc
    output = _exchange(proc, request, timeout)
    try:
        reply = json.loads(output)
    except (ValueError, UnicodeError) as exc:
        raise DataError("worker_failed", "Worker did not return JSON") from exc
    if not isinstance(reply, dict) or type(reply.get("ok")) is not bool:
        raise DataError("protocol_error", "Invalid worker response")
    if not reply["ok"]:
        error = reply.get("error", {})
        if not isinstance(error, dict):
            raise DataError("protocol_error", "Invalid worker error")
        raise DataError(str(error.get("code", "worker_failed")), str(error.get("message", "Worker failed")))
    if proc.returncode != 0 or not isinstance(reply.get("result"), dict):
        raise DataError("protocol_error", "Inconsistent worker result")
    return reply["result"]

"""Real subprocess ownership and bounded-pipe regressions; no model mocks."""

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from test_gbc_data import PYTHON, SRC, WORKER, graph, png

from kohakumoss_ui.contract.gbc_data import DataError, call


def alive(pid):
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0]
        return state not in {"Z", "X"}
    except (FileNotFoundError, ProcessLookupError):
        return False


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Process synchronization timed out")


@unittest.skipUnless(sys.platform == "linux", "Linux process ownership regression")
class GbcDataRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="runtime-data-", dir=os.environ.get("GBC_DATA_EVIDENCE")))

    def wrapper(self, source):
        path = self.root / "test-python"
        path.write_text(f"#!{PYTHON}\n" + source, encoding="ascii")
        path.chmod(0o700)
        return path

    def test_stdout_flood_is_stopped_while_running(self):
        self.flood(1)

    def test_stderr_flood_is_stopped_while_running(self):
        self.flood(2)

    def flood(self, fd):
        # A trusted interpreter fault fixture, not a user-visible execution action.
        child_file = self.root / "pids.json"
        partial = self.root / "output.partial"
        script = f"""
import json, os, subprocess, sys
from pathlib import Path
child = subprocess.Popen([sys.executable, '-I', '-B', '-c', 'import time; time.sleep(60)'])
Path({str(child_file)!r}).write_text(json.dumps([os.getpid(), child.pid]))
Path({str(partial)!r}).write_bytes(b'preserved failed output')
while True:
    os.write({fd}, b'x' * 65536)
"""
        python = self.wrapper(script)
        sentinel = subprocess.Popen([str(PYTHON), "-I", "-B", "-c", "import time; time.sleep(60)"])
        start = time.monotonic()
        try:
            with self.assertRaises(DataError) as caught:
                call(python, {"action": "inspect", "path": str(self.root)}, timeout=20)
            elapsed = time.monotonic() - start
            self.assertEqual(caught.exception.code, "limit_exceeded")
            self.assertIn("stdout" if fd == 1 else "stderr", caught.exception.message)
            self.assertLess(elapsed, 10)
            pids = json.loads(child_file.read_text())
            wait_for(lambda: all(not alive(pid) for pid in pids))
            self.assertIsNone(sentinel.poll(), "Unrelated process was signaled")
            self.assertEqual(partial.read_bytes(), b"preserved failed output")
            (self.root / "result.json").write_text(json.dumps({"stream": fd, "code": caught.exception.code,
                "elapsed": elapsed, "workerAndChildStopped": True, "unrelatedAlive": True}), encoding="ascii")
        finally:
            sentinel.terminate()
            sentinel.wait(timeout=10)
            if child_file.exists():
                worker = json.loads(child_file.read_text())[0]
                if alive(worker):
                    os.killpg(worker, signal.SIGKILL)

    def test_parent_loss_stops_all_actions_waiting_for_input(self):
        for action in ("inspect", "validate", "page", "graph_page", "pack"):
            with self.subTest(action=action):
                pid_file = self.root / f"{action}.pid"
                code = """
import os, subprocess, sys, time
from pathlib import Path
env = dict(os.environ, KOHAKUMOSS_GBC_PARENT_PID=str(os.getpid()))
p = subprocess.Popen([sys.argv[1], '-I', '-B', sys.argv[2]], stdin=subprocess.PIPE,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     env=env, start_new_session=True)
p.stdin.write(('{"action": "' + sys.argv[4] + '"').encode())
p.stdin.flush()
Path(sys.argv[3]).write_text(str(p.pid))
time.sleep(60)
"""
                parent = subprocess.Popen([str(PYTHON), "-I", "-B", "-c", code,
                                           str(PYTHON), str(WORKER), str(pid_file), action])
                worker = None
                try:
                    wait_for(pid_file.exists)
                    worker = int(pid_file.read_text())
                    # The second thread is the armed guard; stdin is still open.
                    wait_for(lambda pid=worker: len(list(Path(f"/proc/{pid}/task").iterdir())) >= 2)
                    if action == "validate":
                        # Kernel parent-death signaling must work even if no Python thread runs.
                        os.kill(worker, signal.SIGSTOP)
                    parent.kill()
                    parent.wait(timeout=5)
                    wait_for(lambda pid=worker: not alive(pid), timeout=3)
                finally:
                    if parent.poll() is None:
                        parent.kill()
                        parent.wait(timeout=5)
                    if worker is not None and alive(worker):
                        os.killpg(worker, signal.SIGKILL)
        (self.root / "all-actions.json").write_text(json.dumps({"actions": 5, "orphanWorkersRunning": 0}), encoding="ascii")

    def test_parent_loss_during_real_pack_preserves_partial_not_ready(self):
        images = self.root / "images"
        images.mkdir()
        records, graphs = [], []
        for index in range(700):
            image = images / f"{index}.png"
            image.write_bytes(png())
            records.append({"key": str(index), "image": str(image), "meta": {}})
            graphs.append(graph(image))
        jsonl = self.root / "graphs.jsonl"
        jsonl.write_text("".join(json.dumps(g) + "\n" for g in graphs), encoding="ascii")
        original = hashlib.sha256(jsonl.read_bytes()).hexdigest()
        output = self.root / "packed"
        payload = {"action": "pack", "graphs": str(jsonl), "imageRoot": str(images),
                   "output": str(output), "records": records, "perShard": 128}
        request = self.root / "request.json"
        request.write_text(json.dumps(payload), encoding="ascii")
        pid_file, ready = self.root / "worker.pid", self.root / "ready.json"
        code = """
import json, subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from kohakumoss_ui.contract.gbc_data import call
original = subprocess.Popen
def tracked(*args, **kwargs):
    p = original(*args, **kwargs)
    Path(sys.argv[4]).write_text(str(p.pid))
    return p
subprocess.Popen = tracked
result = call(Path(sys.argv[2]), json.loads(Path(sys.argv[3]).read_text()))
Path(sys.argv[5]).write_text(json.dumps(result))
"""
        parent = subprocess.Popen([str(PYTHON), "-I", "-B", "-c", code, str(SRC), str(PYTHON),
                                   str(request), str(pid_file), str(ready)])
        worker = None
        try:
            wait_for(pid_file.exists)
            worker = int(pid_file.read_text())
            first = output / ".partial/dataset/shards/shard_00000/images.zip"
            wait_for(lambda: first.exists() and first.stat().st_size > 0)
            parent.kill()
            parent.wait(timeout=5)
            wait_for(lambda: not alive(worker), timeout=3)
            self.assertTrue(first.exists())
            self.assertFalse(ready.exists())
            self.assertFalse((output / "dataset.json").exists())
            self.assertEqual(hashlib.sha256(jsonl.read_bytes()).hexdigest(), original)
            self.assertTrue(all(Path(r["image"]).read_bytes() == png() for r in records))
            (self.root / "parent-loss.json").write_text(json.dumps({"workerStopped": True,
                "partialPreserved": True, "readyPublished": False, "sourceUnchanged": True}), encoding="ascii")
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=5)
            if worker is not None and alive(worker):
                os.killpg(worker, signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()

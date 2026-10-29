"""Real subprocess supervision using an explicitly fake GBC CLI, never inference."""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from kohakumoss_ui.contract.gbc_lease import Lease
from kohakumoss_ui.contract.gbc_process import LOG_LIMIT, ManagedProcess, group_alive
from kohakumoss_ui.contract.gbc_schema import GbcError


def fake_cli(directory, code):
    package = directory / "kohakugbc" / "cli"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "main.py").write_text(code)


@unittest.skipUnless(sys.platform == "linux", "Linux process identity/group supervision")
class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.done = []
        self.proc = None

    def tearDown(self):
        if self.proc:
            self.proc.close()
        self.tmp.cleanup()

    def start(self, code, timeout=10):
        fake_cli(self.base, code)
        lease = Lease("fake-test", self.base / "state")
        self.proc = ManagedProcess(Path(sys.executable).resolve(), None, "doctor", self.base,
                                   timeout, self.base / "log", lease,
                                   lambda proc, clean: self.done.append((proc.exit_code, clean)))
        self.proc.start_monitor()
        return self.proc

    def wait(self):
        self.proc.thread.join(10)
        self.assertFalse(self.proc.thread.is_alive())
        self.assertTrue(self.done)

    def test_exit_and_log_flood(self):
        self.start("import sys\nsys.stdout.write('x' * 2000000)\n")
        self.wait()
        self.assertEqual(self.done, [(0, True)])
        self.assertEqual((self.base / "log").stat().st_size, LOG_LIMIT)
        self.assertEqual(self.proc.dropped, 2000000 - LOG_LIMIT)
        page = self.proc.logs(8, 42)
        self.assertEqual(page["nextCursor"], 50)
        self.assertEqual(len(page["text"]), 42)
        lease = Lease("next", self.base / "state")
        lease.release()

    def test_cancel_owns_child_group(self):
        self.start("import subprocess,time\nsubprocess.Popen(['sleep','60'])\nprint('ready',flush=True)\ntime.sleep(60)\n")
        deadline = time.monotonic() + 5
        while self.proc.size == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(self.proc.stop())
        self.wait()
        self.assertTrue(self.proc.cancel_requested)
        self.assertFalse(group_alive(self.proc.process.pid))

    def test_force_required_for_ignored_term(self):
        self.start("import signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nprint('ready',flush=True)\ntime.sleep(60)\n")
        deadline = time.monotonic() + 5
        while self.proc.size == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.proc.stop()
        time.sleep(0.2)
        self.assertIsNone(self.proc.process.poll())
        self.proc.stop(force=True)
        self.wait()
        self.assertFalse(group_alive(self.proc.process.pid))

    def test_timeout(self):
        self.start("import time\ntime.sleep(60)\n", timeout=1)
        self.wait()
        self.assertNotEqual(self.done[0][0], 0)
        self.assertFalse(group_alive(self.proc.process.pid))

    def test_pid_mismatch_never_signals(self):
        self.start("import time\ntime.sleep(60)\n")
        with patch("kohakumoss_ui.contract.gbc_process.identity", return_value={"start": "wrong"}), patch("os.killpg") as kill:
            with self.assertRaises(GbcError):
                self.proc.stop()
            kill.assert_not_called()

    def test_parent_loss_reaps_own_group(self):
        fake_cli(self.base, "import subprocess,time\nsubprocess.Popen(['sleep','60'])\ntime.sleep(60)\n")
        from kohakumoss_ui.contract import gbc_process
        worker = Path(gbc_process.__file__).with_name("gbc_runtime_worker.py")
        code = (
            "import subprocess,sys,json,os,time\n"
            "p=subprocess.Popen([sys.executable,'-I',sys.argv[1]],stdin=subprocess.PIPE,"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)\n"
            "p.stdin.write(json.dumps({'action':'doctor','config':None,'timeout':30,"
            "'parent':os.getpid(),'output':sys.argv[2]}).encode());p.stdin.close()\n"
            "print(json.dumps({'pid':p.pid}),flush=True)\n"
            "time.sleep(0.3)\n"
            "os._exit(0)\n"
        )
        parent = subprocess.run([sys.executable, "-c", code, str(worker), str(self.base)],
                                cwd=self.base, capture_output=True, text=True, timeout=5, check=True)
        pid = json.loads(parent.stdout)["pid"]
        deadline = time.monotonic() + 5
        while group_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(group_alive(pid))

    def test_restart_fence_and_competing_leases(self):
        first = Lease("generation", self.base / "state")
        with self.assertRaises(GbcError) as busy:
            Lease("training", self.base / "state")
        self.assertEqual(busy.exception.code, "BUSY")
        first.abandon()
        with self.assertRaises(GbcError) as recovery:
            Lease("training", self.base / "state")
        self.assertEqual(recovery.exception.code, "RECOVERY_REQUIRED")

    def test_missing_dependency_is_real_nonzero(self):
        lease = Lease("missing", self.base / "state")
        self.proc = ManagedProcess(Path(sys.executable).resolve(), None, "doctor", self.base,
                                   5, self.base / "log", lease,
                                   lambda proc, clean: self.done.append((proc.exit_code, clean)))
        self.proc.start_monitor()
        self.wait()
        self.assertNotEqual(self.done[0][0], 0)
        self.assertIn("kohakugbc", self.proc.logs()["text"])


if __name__ == "__main__":
    unittest.main()

"""Windows liveness, stop and RUN_NAME guards, exercised from a POSIX host."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kohakumoss_ui import cli
from kohakumoss_ui.contract import launch, state
from kohakumoss_ui.contract.root import TrainerRoot


def make_trainer_root(base: Path) -> TrainerRoot:
    (base / "scripts" / "train").mkdir(parents=True)
    (base / "scripts" / "train" / "moss.py").write_text("# trainer\n", encoding="utf-8")
    (base / "configs").mkdir()
    (base / "configs" / "x.py").write_text("", encoding="utf-8")
    return TrainerRoot(base)


class FakeKernel32:
    """Minimal kernel32 double covering only the calls _alive_windows makes."""

    def __init__(self, handle=4321, wait_code=state._WAIT_TIMEOUT):
        self.handle = handle
        self.wait_code = wait_code
        self.closed = []

    def OpenProcess(self, access, inherit, pid):
        return self.handle

    def WaitForSingleObject(self, handle, milliseconds):
        return self.wait_code

    def CloseHandle(self, handle):
        self.closed.append(handle)


class PidAliveTests(unittest.TestCase):
    def test_windows_probe_never_calls_os_kill(self):
        with mock.patch("os.name", "nt"), mock.patch.object(
            state, "_alive_windows", return_value=True
        ) as probe, mock.patch("os.kill", side_effect=AssertionError("os.kill on Windows")):
            self.assertTrue(state.pid_alive(4321))
        probe.assert_called_once_with(4321)

    def test_posix_probe_keeps_signal_zero(self):
        mine = os.getpid()
        with mock.patch("os.name", "posix"), mock.patch("os.kill") as kill:
            self.assertTrue(state.pid_alive(mine))
        kill.assert_called_once_with(mine, 0)

    def test_windows_running_process_is_alive_and_handle_closed(self):
        fake = FakeKernel32(wait_code=state._WAIT_TIMEOUT)
        with mock.patch.object(state, "_kernel32", return_value=fake):
            self.assertTrue(state._alive_windows(4321))
        self.assertEqual(fake.closed, [fake.handle])

    def test_windows_exited_process_is_dead_and_handle_closed(self):
        fake = FakeKernel32(wait_code=state._WAIT_OBJECT_0)
        with mock.patch.object(state, "_kernel32", return_value=fake):
            self.assertFalse(state._alive_windows(4321))
        self.assertEqual(fake.closed, [fake.handle])

    def test_windows_missing_process_is_dead(self):
        fake = FakeKernel32(handle=None)
        with mock.patch.object(state, "_kernel32", return_value=fake), mock.patch.object(
            state, "_last_error", return_value=87
        ):
            self.assertFalse(state._alive_windows(4321))

    def test_windows_access_denied_counts_as_alive(self):
        fake = FakeKernel32(handle=None)
        with mock.patch.object(state, "_kernel32", return_value=fake), mock.patch.object(
            state, "_last_error", return_value=5
        ):
            self.assertTrue(state._alive_windows(4321))

    def test_windows_handle_is_closed_when_wait_fails(self):
        fake = FakeKernel32()
        fake.WaitForSingleObject = mock.Mock(side_effect=OSError("boom"))
        with mock.patch.object(state, "_kernel32", return_value=fake):
            with self.assertRaises(OSError):
                state._alive_windows(4321)
        self.assertEqual(fake.closed, [fake.handle])


class StopWindowsTests(unittest.TestCase):
    def _status(self):
        return {"pid": 4242, "alive": True}

    def test_graceful_stop_uses_taskkill_without_force(self):
        with mock.patch("os.name", "nt"), mock.patch.object(
            launch.state, "status", return_value=self._status()
        ), mock.patch.object(launch.state, "pid_alive", return_value=False), mock.patch.object(
            launch.subprocess, "run", return_value=mock.Mock(returncode=0)
        ) as run:
            result = launch.stop("run")
        self.assertTrue(result["stopped"])
        self.assertEqual(result["signal"], "taskkill")
        self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "4242", "/T"])
        self.assertIn("creationflags", run.call_args.kwargs)

    def test_force_stop_goes_straight_to_taskkill_f(self):
        with mock.patch("os.name", "nt"), mock.patch.object(
            launch.state, "status", return_value=self._status()
        ), mock.patch.object(launch.state, "pid_alive", return_value=False), mock.patch.object(
            launch.subprocess, "run", return_value=mock.Mock(returncode=0)
        ) as run:
            result = launch.stop("run", force=True)
        self.assertTrue(result["stopped"])
        self.assertEqual(result["signal"], "taskkill /F")
        self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "4242", "/T", "/F"])

    def test_refused_polite_request_escalates_immediately(self):
        replies = [mock.Mock(returncode=1), mock.Mock(returncode=0)]
        with mock.patch("os.name", "nt"), mock.patch.object(
            launch.state, "status", return_value=self._status()
        ), mock.patch.object(launch.state, "pid_alive", return_value=False), mock.patch.object(
            launch.subprocess, "run", side_effect=replies
        ) as run:
            result = launch.stop("run")
        self.assertTrue(result["stopped"])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "4242", "/T", "/F"])

    def test_survivor_of_the_grace_period_is_forced(self):
        waiting = {"stopped": False, "pid": 4242, "signal": "taskkill", "reason": "still running"}
        forced = {"stopped": True, "pid": 4242, "signal": "taskkill /F"}
        with mock.patch("os.name", "nt"), mock.patch.object(
            launch.state, "status", return_value=self._status()
        ), mock.patch.object(
            launch, "_await_exit", side_effect=[waiting, forced]
        ) as wait, mock.patch.object(
            launch.subprocess, "run", return_value=mock.Mock(returncode=0)
        ) as run:
            result = launch.stop("run")
        self.assertTrue(result["stopped"])
        self.assertEqual(result["signal"], "taskkill /F")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(wait.call_count, 2)


class StopPosixTests(unittest.TestCase):
    def test_posix_stop_signals_the_process_group(self):
        identity = {"boot": "fixture-boot", "start": "123", "group": 4242}
        with mock.patch("os.name", "posix"), mock.patch.object(
            launch.state, "status", return_value={"pid": 4242, "alive": True, "identity": identity}
        ), mock.patch.object(launch.state, "pid_alive", return_value=False), mock.patch.object(
            launch.state, "process_identity", return_value=identity
        ), mock.patch.object(launch.os, "killpg") as group_kill:
            result = launch.stop("run")
        self.assertTrue(result["stopped"])
        self.assertEqual(result["signal"], "SIGTERM")
        group_kill.assert_called_once_with(4242, launch.signal.SIGTERM)


class RunNameTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_trainer_root(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _start(self, name):
        launch.start(self.root, "configs/x.py", {"RUN_NAME": name})

    def test_windows_rejects_reserved_device_names(self):
        with mock.patch("os.name", "nt"):
            for name in ("con", "CON", "con.txt", "nul.log", "Com1", "lpt9"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self._start(name)

    def test_windows_rejects_invalid_characters(self):
        with mock.patch("os.name", "nt"):
            for name in ("a:b", "a*b", "a?b", "a<b", "a>b", 'a"b', "a|b"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self._start(name)

    def test_windows_rejects_trailing_dot_or_space(self):
        with mock.patch("os.name", "nt"):
            for name in ("run.", "run "):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self._start(name)

    def test_invalid_characters_are_rejected_on_posix_too(self):
        with mock.patch("os.name", "posix"), self.assertRaises(ValueError):
            self._start("a:b")

    def test_posix_keeps_device_like_names_and_trailing_dot(self):
        with mock.patch("os.name", "posix"):
            launch._validate_run_name("con")
            launch._validate_run_name("run.")


class CleanupTests(unittest.TestCase):
    def test_live_entries_get_a_forced_fallback(self):
        entries = {"a": {"pid": 11}, "b": {"pid": 22}, "c": {"pid": 33}}
        statuses = {
            "a": {"pid": 11, "alive": True},
            "b": {"pid": 22, "alive": False},
            "c": {"pid": 33, "alive": True},
        }
        replies = [
            {"stopped": False, "pid": 11},
            {"stopped": True, "pid": 11},
            {"stopped": True, "pid": 33},
        ]
        with mock.patch.object(cli.state, "all_entries", return_value=entries), mock.patch.object(
            cli.state, "status", side_effect=lambda name: statuses[name]
        ), mock.patch.object(cli.launch, "stop", side_effect=replies) as stop:
            cli.stop_training_processes(timeout=2.0)
        self.assertEqual(
            stop.call_args_list,
            [mock.call("a"), mock.call("a", force=True), mock.call("c")],
        )

    def test_no_entries_starts_no_worker(self):
        with mock.patch.object(cli.state, "all_entries", return_value={}), mock.patch.object(
            cli.threading, "Thread"
        ) as thread:
            cli.stop_training_processes()
        thread.assert_not_called()

    def test_registry_errors_do_not_escape(self):
        with mock.patch.object(cli.state, "all_entries", side_effect=OSError("boom")):
            cli.stop_training_processes(timeout=1.0)

    def test_stop_failures_do_not_escape(self):
        with mock.patch.object(
            cli.state, "all_entries", return_value={"a": {"pid": 11}}
        ), mock.patch.object(
            cli.state, "status", return_value={"pid": 11, "alive": True}
        ), mock.patch.object(cli.launch, "stop", side_effect=RuntimeError("boom")):
            cli.stop_training_processes(timeout=2.0)


if __name__ == "__main__":
    unittest.main()

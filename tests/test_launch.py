"""Process lifecycle test: a fake kogine keeps the POSIX start/stop path honest."""

import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from kohakumoss_ui.contract import launch, state
from kohakumoss_ui.contract.root import TrainerRoot


@unittest.skipIf(os.name == "nt", "POSIX process-group test")
class LaunchTests(unittest.TestCase):
    def test_start_and_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "scripts" / "train").mkdir(parents=True)
            (base / "scripts" / "train" / "moss.py").write_text("")
            (base / "configs").mkdir()
            (base / "configs" / "x.py").write_text("")
            binaries = base / ".venv" / "bin"
            binaries.mkdir(parents=True)
            kogine = binaries / "kogine"
            kogine.write_text("#!/bin/sh\nexec sleep 30\n")
            kogine.chmod(kogine.stat().st_mode | stat.S_IEXEC)

            root = TrainerRoot(base)
            with mock.patch.dict(os.environ, {"KOHAKUMOSS_UI_STATE": str(base / "state")}):
                result = launch.start(root, "configs/x.py", {"RUN_NAME": "launch-test"})
                self.assertTrue(result["alive"])
                time.sleep(0.2)
                stopped = launch.stop("launch-test")
                self.assertTrue(stopped["stopped"])
                self.assertFalse(state.status("launch-test")["alive"])


if __name__ == "__main__":
    unittest.main()

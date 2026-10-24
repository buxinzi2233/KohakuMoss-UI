"""Platform-dependent tool lookup and static MIME selection."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kohakumoss_ui.contract.root import TrainerRoot, _venv_bin
from kohakumoss_ui.server.http import STATIC_MIME, _mime_for


def make_trainer_root(base: Path) -> TrainerRoot:
    (base / "scripts" / "train").mkdir(parents=True)
    (base / "scripts" / "train" / "moss.py").write_text("# trainer\n", encoding="utf-8")
    (base / "configs").mkdir()
    return TrainerRoot(base)


class VenvLookupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_windows_scripts_exe_is_found(self):
        root = make_trainer_root(self.base)
        tool = self.base / ".venv" / "Scripts" / "kohakumoss-doctor.exe"
        tool.parent.mkdir(parents=True)
        tool.write_bytes(b"")
        with patch("os.name", "nt"):
            self.assertEqual(root.doctor_bin, tool)

    def test_windows_layout_covers_every_tool(self):
        root = make_trainer_root(self.base)
        scripts = self.base / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        for name in ("python", "kogine", "kohakumoss-config", "kohakumoss-doctor"):
            (scripts / f"{name}.exe").write_bytes(b"")
        with patch("os.name", "nt"), patch.dict("os.environ", {"KOHAKUMOSS_PYTHON": ""}):
            self.assertEqual(root.python, scripts / "python.exe")
            self.assertEqual(root.kogine, scripts / "kogine.exe")
            self.assertEqual(root.config_bin, scripts / "kohakumoss-config.exe")
            self.assertEqual(root.doctor_bin, scripts / "kohakumoss-doctor.exe")

    def test_posix_bin_layout_is_found(self):
        root = make_trainer_root(self.base)
        bin_dir = self.base / ".venv" / "bin"
        bin_dir.mkdir(parents=True)
        tool = bin_dir / "kohakumoss-doctor"
        tool.write_bytes(b"")
        with patch("os.name", "posix"):
            self.assertEqual(root.doctor_bin, tool)
            self.assertEqual(_venv_bin(self.base, "kohakumoss-doctor"), tool)

    def test_windows_layout_falls_back_to_posix_bin(self):
        make_trainer_root(self.base)
        tool = self.base / ".venv" / "bin" / "kohakumoss-config"
        tool.parent.mkdir(parents=True)
        tool.write_bytes(b"")
        with patch("os.name", "nt"):
            self.assertEqual(_venv_bin(self.base, "kohakumoss-config"), tool)


class StaticMimeTests(unittest.TestCase):
    def test_table_holds_critical_types(self):
        self.assertEqual(STATIC_MIME[".js"], "text/javascript")
        self.assertEqual(STATIC_MIME[".mjs"], "text/javascript")
        self.assertEqual(STATIC_MIME[".css"], "text/css")
        self.assertEqual(STATIC_MIME[".json"], "application/json")
        self.assertEqual(STATIC_MIME[".svg"], "image/svg+xml")

    def test_module_script_is_not_shadowed_by_mimetypes(self):
        # Windows returns text/plain for .js from the registry; the table must win.
        with patch(
            "kohakumoss_ui.server.http.mimetypes.guess_type",
            return_value=("application/octet-stream", None),
        ) as guess:
            self.assertEqual(_mime_for("app.js"), "text/javascript; charset=utf-8")
            self.assertEqual(_mime_for("worker.mjs"), "text/javascript; charset=utf-8")
            guess.assert_not_called()

    def test_text_and_binary_types(self):
        self.assertEqual(_mime_for("index.html"), "text/html; charset=utf-8")
        self.assertEqual(_mime_for("theme.css"), "text/css; charset=utf-8")
        self.assertEqual(_mime_for("logo.svg"), "image/svg+xml")
        self.assertEqual(_mime_for("font.woff2"), "font/woff2")
        self.assertEqual(_mime_for("blob.unknown"), "application/octet-stream")


if __name__ == "__main__":
    unittest.main()

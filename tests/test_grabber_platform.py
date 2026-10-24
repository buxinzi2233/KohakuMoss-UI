"""Windows branches: child log polling, media open, model scan and JSON backup."""

import os
import pathlib
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kohakumoss_ui.contract import grabber
from kohakumoss_ui.contract import grabber_models as models
from kohakumoss_ui.store import jsonfile
from test_grabber import GrabberCase


class FakeProcess:
    """Stand-in Popen: writes the ANSI-page banner into the log it is handed."""

    def __init__(self, args, cwd=None, env=None, stdin=None, stdout=None,
                 stderr=None, start_new_session=False):
        self.pid = 4242
        self.env = dict(env or {})
        self._alive = True
        # A cp936 console cannot carry the Chinese prefix, only the bare URL.
        stdout.write("  \u754c\u9762\u5730\u5740\uff1a".encode("gbk", errors="replace")
                     + b"http://127.0.0.1:8717\r\n")
        stdout.flush()

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self._alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._alive = False


class WindowsLogTests(GrabberCase):
    def test_start_uses_named_log_and_bare_url_banner(self):
        popen = mock.Mock(side_effect=FakeProcess)
        with mock.patch("os.name", "nt"), \
                mock.patch.object(grabber.subprocess, "Popen", popen), \
                mock.patch.object(grabber.os, "pread",
                                  side_effect=AssertionError("os.pread is Unix-only")), \
                mock.patch.object(grabber.Grabber, "status",
                                  return_value={"running": True, "managed": True}):
            state = self.client.start_service()
        self.assertTrue(state["running"])
        self.assertEqual(self.client._port, 8717)
        log_path = self.client._stdout_path
        self.assertIsNotNone(log_path)
        self.assertTrue(os.path.exists(log_path))
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env["PYTHONUTF8"], "1")
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")
        self.client.close()
        self.assertFalse(os.path.exists(log_path))

    def test_new_bytes_are_read_without_moving_the_child_offset(self):
        log_path = Path(self.tmp.name) / "grabber.log"
        log_path.write_bytes(b"first\r\n")
        self.client._stdout_path = str(log_path)
        self.client._stdout = log_path.open("r+b")
        self.addCleanup(self.client._stdout.close)
        with mock.patch.object(grabber.os, "pread",
                               side_effect=AssertionError("os.pread is Unix-only")):
            first = self.client._read_new_output(0)
            self.client._stdout.seek(0, os.SEEK_END)
            self.client._stdout.write(b"second\r\n")
            self.client._stdout.flush()
            position = self.client._stdout.tell()
            second = self.client._read_new_output(len(first))
            self.assertEqual(self.client._stdout.tell(), position)
        self.assertEqual(first, b"first\r\n")
        self.assertEqual(second, b"second\r\n")
        self.assertEqual(self.client._read_new_output(len(first) + len(second)), b"")


class MediaOpenTests(GrabberCase):
    def setUp(self):
        super().setUp()
        self.downloads = self.asg / "downloads"
        (self.downloads / "nested").mkdir()
        self.target = self.downloads / "nested" / "pic.png"
        self.target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"payload")

    def test_windows_opener_reads_regular_file(self):
        with grabber._open_media_windows(self.downloads, "nested/pic.png") as (stream, info):
            self.assertEqual(stream.read(), self.target.read_bytes())
            self.assertTrue(stat.S_ISREG(info.st_mode))

    def test_windows_opener_rejects_links_directories_and_missing_files(self):
        link = self.downloads / "nested" / "link.png"
        os.symlink(self.target, link)
        with self.assertRaises(ValueError):
            with grabber._open_media_windows(self.downloads, "nested/link.png"):
                pass
        with self.assertRaises(ValueError):
            with grabber._open_media_windows(self.downloads, "nested"):
                pass
        with self.assertRaises(FileNotFoundError):
            with grabber._open_media_windows(self.downloads, "missing.png"):
                pass

    def test_dispatch_on_windows_maps_missing_media_to_404(self):
        with mock.patch("os.name", "nt"):
            with self.client._open_media("/files/nested/pic.png") as (stream, mime, info):
                self.assertEqual(stream.read(), self.target.read_bytes())
                self.assertEqual(mime, "image/png")
            with self.assertRaises(grabber.GrabberError) as caught:
                with self.client._open_media("/files/missing.png"):
                    pass
        self.assertEqual(caught.exception.status, 404)
        os.symlink(self.target, self.downloads / "nested" / "link.png")
        with mock.patch("os.name", "nt"), self.assertRaises(ValueError):
            with self.client._open_media("/files/nested/link.png"):
                pass


class ModelScanTests(unittest.TestCase):
    def test_windows_scan_uses_paths_and_skips_junctions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "models"
            (root / "sub").mkdir(parents=True)
            (root / "sub" / "a.onnx").write_bytes(b"onnx")
            (root / "sub" / "selected_tags.csv").write_text("tag", encoding="utf-8")
            loop = root / "loop"
            loop.mkdir()
            (loop / "b.onnx").write_bytes(b"onnx")
            with mock.patch("os.name", "nt"), \
                    mock.patch.object(models, "Path", pathlib.PosixPath), \
                    mock.patch.object(models, "_is_junction",
                                      lambda entry: entry.name == "loop"):
                scanned = models.scan_models(root)
            baseline = models.scan_models(root)
        self.assertEqual([model["name"] for model in scanned["models"]], ["a.onnx"])
        self.assertEqual(scanned["models"][0]["tags_path"],
                         str(root / "sub" / "selected_tags.csv"))
        self.assertFalse(scanned["truncated"])
        self.assertEqual(sorted(model["name"] for model in baseline["models"]),
                         ["a.onnx", "b.onnx"])

    def test_entry_without_is_junction_is_not_a_junction(self):
        entry = mock.Mock(spec=["name"])
        self.assertFalse(models._is_junction(entry))
        entry = mock.Mock()
        entry.is_junction.return_value = True
        self.assertTrue(models._is_junction(entry))


class JsonBackupTests(unittest.TestCase):
    def test_backup_without_fchmod_writes_bak_and_never_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "one.json"
            target.write_text("{broken", encoding="utf-8")
            # Windows has no os.fchmod, so the fallback must use os.chmod by path.
            with mock.patch.object(jsonfile.os, "fchmod", None):
                self.assertEqual(jsonfile.read_json(target, {"ok": True}), {"ok": True})
            backup = target.with_name("one.json.bak")
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_bytes(), b"{broken")
            self.assertEqual(sorted(item.name for item in target.parent.iterdir()),
                             ["one.json", "one.json.bak"])


if __name__ == "__main__":
    unittest.main()

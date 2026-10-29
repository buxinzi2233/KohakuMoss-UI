"""Dependency-free ESM and structural DOM regressions for GBC views."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node is required for ESM behavior tests")
class GbcUiTests(unittest.TestCase):
    def test_gbc_transport_and_view_lifecycle(self):
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("gbc_ui.mjs"))],
            text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("GBC UI tests passed", result.stdout)

    def test_gbc_authenticated_media_and_cleanup(self):
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("gbc_media.mjs"))],
            text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("GBC media UI tests passed", result.stdout)


if __name__ == "__main__":
    unittest.main()

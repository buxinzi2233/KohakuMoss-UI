"""Exercise real shell navigation with the real annotation dirty gate offline."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node is required for ESM behavior tests")
class AppNavigationTests(unittest.TestCase):
    def test_route_and_language_dirty_gates(self):
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("app_navigation.mjs"))],
            text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("86 behavioral cases passed", result.stdout)


if __name__ == "__main__":
    unittest.main()

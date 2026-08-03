import subprocess
import sys
import unittest
from pathlib import Path

from core import __version__


class ReleaseGateTests(unittest.TestCase):
    def test_release_script_accepts_current_version(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "scripts/verify_release.py",
                "--version",
                __version__,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"Release checks passed: {__version__}", result.stdout)

    def test_release_script_rejects_a_different_version(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(Path("scripts/verify_release.py")),
                "--version",
                "0.0.1",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(1, result.returncode)
        self.assertIn("does not match", result.stderr)


if __name__ == "__main__":
    unittest.main()

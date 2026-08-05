import subprocess
import sys
import unittest
from pathlib import Path

from core import __version__
from scripts.verify_release import build_full_gate_commands


class ReleaseGateTests(unittest.TestCase):
    def test_full_gate_uses_fixed_argv_and_explicit_web_checks(self) -> None:
        root = Path.cwd()
        commands = build_full_gate_commands(root, root / "report", True)

        self.assertEqual(
            [
                "Python tests",
                "Python compile",
                "diff check",
                "offline benchmark",
                "Web typecheck",
                "Web lint",
                "Web build",
            ],
            [name for name, _command in commands],
        )
        self.assertTrue(all(isinstance(command, tuple) for _name, command in commands))
        self.assertFalse(any(command[0] in {"sh", "bash", "zsh"} for _name, command in commands))

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

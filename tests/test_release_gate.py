import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import __version__
from scripts.verify_release import (
    _verify_agent_actions,
    _verify_owned_agent_calls,
    _verify_removed_code_names,
    build_full_gate_commands,
)


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

    def test_release_gate_rejects_private_agent_calls_outside_owners(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "adapter" / "external.py"
            path.parent.mkdir()
            path.write_text(
                "def run(agent):\n    return agent._create_skills('alice')\n",
                encoding="utf-8",
            )

            errors = _verify_owned_agent_calls(root, [path])

        self.assertEqual(1, len(errors))
        self.assertIn("_create_skills", errors[0])

    def test_release_gate_rejects_removed_public_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.py"
            path.write_text(
                "def prune_expired_audit_events():\n    return None\n",
                encoding="utf-8",
            )

            errors = _verify_removed_code_names([path])

        self.assertEqual(1, len(errors))
        self.assertIn("prune_expired_audit_events", errors[0])

    def test_release_gate_rejects_removed_private_agent_methods(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.py"
            path.write_text(
                "class Agent:\n    def _add_skill_handler(self):\n        return None\n",
                encoding="utf-8",
            )

            errors = _verify_removed_code_names([path])

        self.assertEqual(1, len(errors))
        self.assertIn("_add_skill_handler", errors[0])

    def test_release_gate_rejects_agent_action_growth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.py"
            path.write_text(
                """class AgentSkills:
    def enable(self): pass
    def add_handler(self): pass
class AgentEvents:
    def add_subscriber(self): pass
class Agent:
    def add_model(self): pass
    def add_skill_path(self): pass
    def add_subagent(self): pass
    def add_tool(self): pass
    def for_user(self): pass
    def run(self): pass
    def hidden_fallback(self): pass
""",
                encoding="utf-8",
            )

            errors = _verify_agent_actions(path)

        self.assertEqual(1, len(errors))
        self.assertIn("hidden_fallback", errors[0])


if __name__ == "__main__":
    unittest.main()

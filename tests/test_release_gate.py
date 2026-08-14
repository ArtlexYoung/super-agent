import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import __version__
from scripts.verify_release import (
    _verify_agent_actions,
    _verify_offline_benchmark,
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
                "Feature contract",
                "Python tests",
                "Python compile",
                "diff check",
                "Python package build",
                "offline benchmark",
                "Web typecheck",
                "Web lint",
                "Web build",
            ],
            [name for name, _command in commands],
        )
        self.assertTrue(all(isinstance(command, tuple) for _name, command in commands))
        self.assertFalse(any(command[0] in {"sh", "bash", "zsh"} for _name, command in commands))
        package_command = dict(commands)["Python package build"]
        self.assertEqual(("uv", "build"), package_command[:2])
        feature_command = dict(commands)["Feature contract"]
        self.assertEqual((sys.executable, "-m", "unittest"), feature_command[:3])

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

    def test_release_gate_rejects_removed_runtime_layers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tools.py"
            path.write_text("class RunToolsContext:\n    pass\n", encoding="utf-8")

            errors = _verify_removed_code_names([path])

        self.assertEqual(1, len(errors))
        self.assertIn("RunToolsContext", errors[0])

    def test_runtime_ownership_is_direct_and_memory_stays_in_skills(self) -> None:
        source = Path("src")
        runtime_tree = ast.parse(
            source.joinpath("core/runtime.py").read_text(encoding="utf-8")
        )
        runtime_classes = {
            node.name: node
            for node in runtime_tree.body
            if isinstance(node, ast.ClassDef)
        }
        run_fields = {
            node.target.id
            for node in runtime_classes["Run"].body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
        }
        self.assertTrue(
            {"task", "skills", "identity", "event_log", "store", "task_runner"}
            <= run_fields
        )
        self.assertTrue(
            {"model_profile", "model_profiles", "provider"}.isdisjoint(run_fields)
        )
        runtime_methods = {
            node.name
            for node in runtime_classes["Runtime"].body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
        }
        self.assertEqual({"run_task"}, runtime_methods)

        tools_tree = ast.parse(
            source.joinpath("core/tools.py").read_text(encoding="utf-8")
        )
        run_tools = next(
            node
            for node in tools_tree.body
            if isinstance(node, ast.ClassDef) and node.name == "RunTools"
        )
        initializer = next(
            node
            for node in run_tools.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        positional = [*initializer.args.posonlyargs, *initializer.args.args]
        self.assertEqual(["self", "run"], [argument.arg for argument in positional[:2]])

        memory_imports: list[str] = []
        for path in source.joinpath("core").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "skill.handlers.memory":
                    memory_imports.append(str(path))
                if isinstance(node, ast.Import) and any(
                    alias.name == "skill.handlers.memory" for alias in node.names
                ):
                    memory_imports.append(str(path))
        self.assertEqual([], memory_imports)

    def test_release_gate_rejects_a_fixture_only_offline_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            value = json.loads(
                Path("examples/offline-gate-benchmark.json").read_text(encoding="utf-8")
            )
            value["agents"][0]["command"] = ["{python}", "-c", "print('ready')"]
            path = Path(tmp) / "benchmark.json"
            path.write_text(json.dumps(value), encoding="utf-8")

            errors = _verify_offline_benchmark(path)

        self.assertTrue(any("real Super Agent CLI" in error for error in errors))

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

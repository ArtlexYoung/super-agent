from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from core import __version__


MAX_FUNCTION_LINES = 100
MAX_CONTROL_FLOW_COMPLEXITY = 10
MAX_SOURCE_LINES = 600
MAX_DIRECTORY_CHILDREN = 10
CONTROL_FLOW_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.Match,
    ast.IfExp,
)


class ReleaseShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = tomllib.loads(
            Path("pyproject.toml").read_text(encoding="utf-8")
        )

    def test_release_versions_have_one_value(self) -> None:
        web_package = json.loads(
            Path("web/package.json").read_text(encoding="utf-8")
        )

        self.assertEqual(__version__, self.project["project"]["version"])
        self.assertEqual(__version__, web_package["version"])

    def test_default_python_install_has_no_dependencies(self) -> None:
        self.assertEqual([], self.project["project"]["dependencies"])

    def test_wheel_contains_only_the_public_source_layout(self) -> None:
        wheel = self.project["tool"]["hatch"]["build"]["targets"]["wheel"]

        self.assertEqual(
            [
                "src/adapter",
                "src/core",
                "src/skill",
                "src/cli.py",
                "src/super_agent.py",
            ],
            wheel["only-include"],
        )
        self.assertEqual(["src"], wheel["sources"])

    def test_builtin_skill_resources_are_complete_and_packaged(self) -> None:
        root = Path("src/skill/builtin")
        expected = {
            "freshness/default/skill.toml",
            "feedback/conversation/SKILL.md",
            "feedback/conversation/skill.toml",
            "memory/default/SKILL.md",
            "memory/default/skill.toml",
            "task/code/SKILL.md",
            "task/code/skill.toml",
            "task/common/SKILL.md",
            "task/common/skill.toml",
        }
        packaged = {
            str(path.relative_to(root))
            for path in root.rglob("*")
            if path.is_file()
        }

        self.assertEqual(expected, packaged)
        wheel = self.project["tool"]["hatch"]["build"]["targets"]["wheel"]
        self.assertIn("src/skill", wheel["only-include"])

    def test_automatic_evolution_state_machine_is_removed(self) -> None:
        self.assertFalse(Path("src/core/evolution").exists())
        self.assertTrue(Path("src/core/evaluation/learning.py").is_file())
        self.assertTrue(Path("src/core/skill_use/update.py").is_file())
        source = Path("src/core/skill_use/update.py").read_text(encoding="utf-8")
        cli_source = Path("src/adapter/cli_adapter/skills.py").read_text(
            encoding="utf-8"
        )
        for operation in ("propose_skill_change", "test_skill_change", "apply_skill_change", "undo_skill_change"):
            self.assertIn(f"def {operation}", source)
        self.assertNotIn('add_parser("promote"', cli_source)

    def test_skill_change_has_one_import_path(self) -> None:
        from core.skill_use.update import SkillUpdater

        self.assertEqual("SkillUpdater", SkillUpdater.__name__)

    def test_removed_source_layouts_do_not_return(self) -> None:
        removed_paths = [
            "src/builtin_skills",
            "src/capabilities",
            "src/commands",
            "src/frontend",
            "src/mcp",
            "src/memory",
            "src/provider_adapter",
            "src/runtime",
            "src/super_agent",
            "src/workflow",
            "skill_scenes",
            "src/core/actions.py",
            "src/core/identity.py",
            "src/core/secrets.py",
            "src/core/session.py",
        ]

        self.assertEqual([], [path for path in removed_paths if Path(path).exists()])

    def test_public_modules_import_in_a_fresh_process(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path("src").resolve())
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from super_agent import Agent; "
                    "from adapter.ag_ui_adapter import AGUIEventMapper; "
                    "from core.runtime.run import Run; "
                    "from skill.manifest import SkillManifest",
                ],
                cwd=tmp,
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_removed_core_modules_cannot_be_imported(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path("src").resolve())
        with tempfile.TemporaryDirectory() as tmp:
            for module_name in (
                "core.actions",
                "core.identity",
                "core.secrets",
                "core.session",
            ):
                with self.subTest(module_name=module_name):
                    completed = subprocess.run(
                        [sys.executable, "-c", f"import {module_name}"],
                        cwd=tmp,
                        check=False,
                        capture_output=True,
                        text=True,
                        env=environment,
                    )

                    self.assertNotEqual(0, completed.returncode)

    def test_core_owns_runtime_and_skill_does_not(self) -> None:
        self.assertTrue(Path("src/core/runtime/runtime.py").is_file())
        self.assertTrue(Path("src/core/runtime/loop.py").is_file())
        self.assertFalse(Path("src/skill/task").exists())

    def test_removed_coupled_core_domains_do_not_return(self) -> None:
        removed = [
            "src/core/agent.py",
            "src/core/engine.py",
            "src/core/run.py",
            "src/core/state/store.py",
            "src/core/task",
            "src/core/user.py",
        ]

        self.assertEqual([], [path for path in removed if Path(path).exists()])

    def test_removed_task_controllers_do_not_return(self) -> None:
        removed = [
            "src/core/runtime/plan.py",
            "src/core/runtime/planning.py",
            "src/core/runtime/preflight.py",
            "src/core/runtime/preparation.py",
            "src/core/runtime/scheduler.py",
        ]

        self.assertEqual([], [path for path in removed if Path(path).exists()])

    def test_removed_runtime_contracts_do_not_return(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in Path("src").rglob("*.py")
        )

        self.assertNotIn("runtime_lock", source)
        self.assertNotIn("task.step.scheduled", source)
        self.assertNotIn("task.step.completed", source)
        self.assertNotIn("SkillSceneManager", source)
        self.assertNotIn("route_response", source)
        self.assertNotIn("ModelRouting", source)
        self.assertNotIn("routing_evidence", source)

    def test_temporary_memory_and_secondary_organizer_do_not_return(self) -> None:
        removed = [
            "src/skill/state/memory_models.py",
            "src/skill/state/memory_organization.py",
            "src/skill/state/memory_service.py",
            "src/skill/state/memory_support.py",
            "src/skill/builtin/memory/code",
        ]

        self.assertEqual([], [path for path in removed if Path(path).exists()])

    def test_python_source_files_stay_within_the_size_limit(self) -> None:
        oversized = {}
        for path in Path("src").rglob("*.py"):
            line_count = _count_non_import_lines(path)
            if line_count > MAX_SOURCE_LINES:
                oversized[str(path)] = line_count

        self.assertEqual({}, oversized)

    def test_python_functions_stay_within_maintenance_limits(self) -> None:
        oversized = {}
        complex_functions = {}
        for path in Path("src").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for function in (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            ):
                key = f"{path}:{function.lineno}:{function.name}"
                line_count = function.end_lineno - function.lineno + 1
                complexity = _count_control_flow_complexity(function)
                if line_count > MAX_FUNCTION_LINES:
                    oversized[key] = line_count
                if complexity > MAX_CONTROL_FLOW_COMPLEXITY:
                    complex_functions[key] = complexity

        self.assertEqual({}, oversized)
        self.assertEqual({}, complex_functions)

    def test_source_directories_stay_within_the_child_limit(self) -> None:
        crowded = {}
        directories = [Path("src"), *Path("src").rglob("*")]
        for directory in directories:
            if not directory.is_dir() or directory.name == "__pycache__":
                continue
            children = [
                child
                for child in directory.iterdir()
                if child.name not in {"__init__.py", "__pycache__"}
                and not child.name.startswith(".")
            ]
            if len(children) > MAX_DIRECTORY_CHILDREN:
                crowded[str(directory)] = len(children)

        self.assertEqual({}, crowded)


def _count_non_import_lines(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    import_lines: set[int] = set()
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if isinstance(node, ast.Import | ast.ImportFrom):
            import_lines.update(range(node.lineno, node.end_lineno + 1))
    return len(source.splitlines()) - len(import_lines)


def _count_control_flow_complexity(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> int:
    counter = _ControlFlowCounter()
    for statement in function.body:
        counter.visit(statement)
    return 1 + counter.branches


class _ControlFlowCounter(ast.NodeVisitor):
    def __init__(self) -> None:
        self.branches = 0

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, CONTROL_FLOW_NODES):
            self.branches += 1
        super().generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return None


if __name__ == "__main__":
    unittest.main()

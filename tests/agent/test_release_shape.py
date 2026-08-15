from __future__ import annotations

import ast
import json
import os
import runpy
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from core import __version__
from scripts.verify_release import (
    EXPECTED_DOMAIN_CHILDREN,
    EXPECTED_EVALUATION_PACKAGE_FILES,
    EXPECTED_SDIST_FORCE_INCLUDE,
    EXPECTED_SDIST_ROOTS,
    FEATURE_CONTRACT_MODULES,
    FINAL_SOURCE_LINE_TARGET,
    MAX_TOTAL_SOURCE_FILES,
    MAX_TOTAL_SOURCE_LINES,
    PRESERVED_OPTIONAL_DEPENDENCIES,
    PRESERVED_SOURCE_SYMBOLS,
    SOURCE_LINE_BUDGETS,
    _verify_preserved_capabilities,
    _verify_owned_agent_calls,
)


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

    def test_source_reduction_plan_cannot_be_silently_relaxed(self) -> None:
        budgets = list(SOURCE_LINE_BUDGETS.values())

        self.assertEqual(9_738, FINAL_SOURCE_LINE_TARGET)
        self.assertEqual(FINAL_SOURCE_LINE_TARGET, budgets[-1])
        self.assertEqual(MAX_TOTAL_SOURCE_LINES, SOURCE_LINE_BUDGETS[__version__])
        self.assertTrue(all(current > following for current, following in zip(budgets, budgets[1:])))

    def test_high_complexity_capabilities_cannot_be_removed_for_size(self) -> None:
        self.assertEqual(
            {"mysql", "postgresql"},
            PRESERVED_OPTIONAL_DEPENDENCIES,
        )
        self.assertIn("skill/tasks/task_queue.py", PRESERVED_SOURCE_SYMBOLS)
        self.assertIn("skill/tasks/task_groups.py", PRESERVED_SOURCE_SYMBOLS)
        self.assertIn("adapter/cli_support/cli_data.py", PRESERVED_SOURCE_SYMBOLS)
        self.assertEqual(
            [],
            _verify_preserved_capabilities(Path("src"), self.project["project"]),
        )
        self.assertIn("tests.runtime.test_composability", FEATURE_CONTRACT_MODULES)

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

    def test_sdist_contains_all_language_guides(self) -> None:
        sdist = self.project["tool"]["hatch"]["build"]["targets"]["sdist"]

        self.assertEqual(EXPECTED_SDIST_ROOTS, sdist["only-include"])
        self.assertEqual(EXPECTED_SDIST_FORCE_INCLUDE, sdist["force-include"])
        self.assertEqual(15, len(EXPECTED_EVALUATION_PACKAGE_FILES))
        self.assertTrue(
            {"README.md", "README_cn.md", "README_en.md"}.issubset(
                sdist["only-include"]
            )
        )

    def test_bilingual_readme_links_guides_and_acknowledgements(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("README_cn.md", readme)
        self.assertIn("README_en.md", readme)
        self.assertIn("## 致谢与借鉴", readme)

    def test_evaluation_assets_live_under_tests(self) -> None:
        project_root = Path.cwd().resolve()
        runner = runpy.run_path("tests/eval/runner/common.py")

        self.assertFalse(Path("eval").exists())
        self.assertTrue(Path("tests/eval/README.md").is_file())
        self.assertTrue(Path("tests/eval/reports").is_dir())
        self.assertEqual(project_root, runner["PROJECT_ROOT"])
        self.assertEqual(project_root / "tests" / "eval", runner["EVAL_ROOT"])

    def test_builtin_skill_resources_are_complete_and_packaged(self) -> None:
        root = Path("src/skill/builtin")
        expected = {
            "freshness/default/skill.toml",
            "feedback/conversation/SKILL.md",
            "feedback/conversation/skill.toml",
            "memory/default/SKILL.md",
            "memory/default/skill.toml",
            "mcp/general/SKILL.md",
            "mcp/general/skill.toml",
            "task/code/SKILL.md",
            "task/code/skill.toml",
            "task/common/SKILL.md",
            "task/common/skill.toml",
            "task/code-multi-deep-optimization/SKILL.md",
            "task/code-multi-deep-optimization/skill.toml",
            "task/common-multi-producer-consumer/SKILL.md",
            "task/common-multi-producer-consumer/skill.toml",
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
        self.assertTrue(Path("src/skill/learning/run_learning.py").is_file())
        self.assertTrue(Path("src/skill/learning/update.py").is_file())
        source = Path("src/skill/learning/update.py").read_text(encoding="utf-8")
        cli_source = Path("src/adapter/cli_support/cli_skills.py").read_text(
            encoding="utf-8"
        )
        operations = (
            "propose_skill_change",
            "test_skill_change",
            "apply_skill_change",
            "undo_skill_change",
        )
        for operation in operations:
            self.assertIn(f"def {operation}", source)
        self.assertNotIn('add_parser("promote"', cli_source)

    def test_skill_change_has_one_import_path(self) -> None:
        from skill.learning.update import SkillUpdater

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
            "src/core/evaluation",
            "src/core/secrets.py",
            "src/core/files.py",
            "src/core/provider",
            "src/core/skill_use",
            "src/core/runtime/resources.py",
            "src/core/runtime/runtime.py",
            "src/core/runtime/tasks",
            "src/core/session.py",
            "src/core/state/events.py",
            "src/core/state/event_log.py",
            "src/core/state/access.py",
            "src/core/state/disclosure.py",
            "src/skill/runtime/loaded.py",
            "src/skill/runtime/registry.py",
            "src/skill/runtime/skills.py",
            "src/skill/runtime/workflow.py",
            "src/skill/runtime/update.py",
            "src/skill/runtime/files/models.py",
            "src/skill/runtime/files",
            "src/skill/learning/learning.py",
            "src/adapter/conversations.py",
            "src/adapter/cli_adapter/__init__.py",
            "src/adapter/cli_adapter/check.py",
            "src/adapter/cli_adapter/serve.py",
            "src/adapter/cli_adapter/models.py",
            "src/adapter/cli_adapter/conversations.py",
            "src/adapter/cli_adapter/memory.py",
            "src/adapter/cli_adapter/runs.py",
            "src/adapter/cli_adapter/storage.py",
            "src/adapter/cli_adapter/run/check.py",
            "src/adapter/cli_adapter/run/serve.py",
            "src/adapter/cli_adapter/manage",
            "src/adapter/storage/copy.py",
            "src/adapter/storage/values.py",
            "src/adapter/ag_ui_adapter/configuration.py",
            "src/adapter/ag_ui_adapter/__init__.py",
            "src/adapter/ag_ui_adapter/protocol.py",
            "src/adapter/storage/sql/__init__.py",
            "src/adapter/storage/sql/base.py",
            "src/adapter/storage/sql/mysql.py",
            "src/adapter/storage/sql/postgresql.py",
            "src/adapter/worktree.py",
            "src/adapter/ag_ui_adapter",
            "src/adapter/cli_adapter",
            "src/adapter/storage",
            "src/core/runtime",
            "src/core/state",
            "src/skill/runtime",
            "src/skill/disclosure.py",
            "src/skill/index.py",
            "src/skill/manifest.py",
        ]

        self.assertEqual([], [path for path in removed_paths if Path(path).exists()])

    def test_cli_has_explicit_function_groups(self) -> None:
        root = Path("src/adapter")
        support = root / "cli_support"
        self.assertTrue((root / "cli.py").is_file())
        self.assertTrue((root / "code.py").is_file())
        self.assertTrue((support / "cli_config.py").is_file())
        self.assertFalse((support / "cli_loaders.py").exists())
        self.assertTrue((support / "cli_skills.py").is_file())
        self.assertTrue((support / "cli_data.py").is_file())
        self.assertFalse((support / "manage").exists())
        self.assertFalse((support / "run").exists())
        for old_path in (
            "check.py",
            "serve.py",
            "models.py",
            "conversations.py",
            "memory.py",
            "runs.py",
            "storage.py",
        ):
            self.assertFalse((support / old_path).exists())

    def test_external_adapters_use_the_owned_agent_boundaries(self) -> None:
        agent_path = Path("src/adapter/agent.py")
        user_path = Path("src/adapter/user.py")
        self.assertTrue(agent_path.is_file())
        self.assertTrue(user_path.is_file())
        for path in Path("src/adapter").rglob("*.py"):
            if path == agent_path:
                continue
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("._setup", source, str(path))
            if path != user_path:
                self.assertNotIn("._run_for_user", source, str(path))

    def test_external_io_uses_user_domain_services(self) -> None:
        source_root = Path("src")
        source_files = list(source_root.rglob("*.py"))

        self.assertEqual(
            [],
            _verify_owned_agent_calls(source_root, source_files),
        )

    def test_core_and_skill_do_not_import_external_adapters(self) -> None:
        for root_name in ("core", "skill"):
            for path in Path("src", root_name).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        imported = [] if node.module is None else [node.module]
                    else:
                        continue
                    self.assertTrue(
                        all(not name == "adapter" and not name.startswith("adapter.")
                            for name in imported),
                        f"{path} imports external adapter code",
                    )

    def test_public_modules_import_in_a_fresh_process(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path("src").resolve())
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from super_agent import Agent; "
                    "from adapter.http.agui import AGUIEventMapper; "
                    "from core.loop import TaskRunner; "
                    "from core.model_calls import ModelCaller; "
                    "from core.runtime import Run; "
                    "from core.tools import RunTools; "
                    "from skill.handlers.runtime import Skills, SkillUse; "
                    "from skill.discovery.manifest import SkillManifest",
                ],
                cwd=tmp,
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_documented_examples_run_offline(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path("src").resolve())
        for example in ("minimal.py", "custom_skill.py", "team.py"):
            with self.subTest(example=example):
                completed = subprocess.run(
                    [sys.executable, str(Path("examples") / example)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)

    def test_removed_module_paths_cannot_be_imported(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path("src").resolve())
        with tempfile.TemporaryDirectory() as tmp:
            for module_name in (
                "adapter.ag_ui_adapter",
                "adapter.cli_adapter",
                "adapter.storage",
                "core.actions",
                "core.evaluation",
                "core.identity",
                "core.provider.chat",
                "core.provider.pool",
                "core.secrets",
                "core.state.backend",
                "core.state.views",
                "core.session",
                "core.state.events",
                "core.state.event_log",
                "core.state.models",
                "core.state.subscribers",
                "core.runtime.setup",
                "core.runtime.agent",
                "core.runtimetime",
                "core.runtime.tasks.queue",
                "core.runtime.tasks.agents",
                "core.runtime.tasks.groups",
                "core.runtime.tasks.group_data",
                "core.runtime.loop",
                "core.runtime.model_calls",
                "core.runtime.run",
                "core.runtime.team",
                "core.runtime.tools",
                "core.state",
                "core.state.disclosure",
                "core.skill_use",
                "skill.runtime.loaded",
                "skill.runtime.registry",
                "skill.runtime.skills",
                "skill.runtime.update",
                "skill.runtime.workflow",
                "skill.catalog",
                "skill.disclosure",
                "skill.index",
                "skill.manifest",
                "skill.task_groups",
                "skill.task_queue",
                "adapter.cli_adapter.check",
                "adapter.cli_adapter.conversations",
                "adapter.cli_adapter.memory",
                "adapter.cli_adapter.models",
                "adapter.cli_adapter.runs",
                "adapter.cli_adapter.serve",
                "adapter.cli_adapter.storage",
                "adapter.cli_adapter.loaders",
                "adapter.cli_data.conversations",
                "adapter.cli_data.memory",
                "adapter.cli_data.runs",
                "adapter.cli_data.storage",
                "adapter.cli_adapter.manage.models",
                "adapter.cli_adapter.manage.skills",
                "adapter.cli_adapter.run.check",
                "adapter.cli_adapter.run.serve",
                "adapter.storage.copy",
                "adapter.storage.values",
                "adapter.storage.sql.base",
                "adapter.storage.sql.mysql",
                "adapter.storage.sql.postgresql",
                "adapter.storage.jsonl",
                "adapter.storage.sqlite",
                "adapter.storage.disclosure",
                "adapter.worktree",
                "adapter.general",
                "adapter.ag_ui_adapter.configuration",
                "adapter.ag_ui_adapter.protocol",
                "skill.runtime.files.models",
                "skill.runtime.files.package",
                "skill.runtime.files.operations",
                "skill.runtime.tasks.group_data",
                "skill.runtime.tasks.agents",
                "skill.runtime.files.lock",
                "skill.learning.models",
                "skill.learning.review",
                "skill.learning.insight",
                "skill.learning.rules",
                "skill.runtime.defaults",
                "skill.source",
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

    def test_removed_runtime_names_cannot_be_imported(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path("src").resolve())
        statements = (
            "from core.loop import ModelLoop",
            "from core.model_calls import ModelCalls",
            "from core.tools import RuntimeTools",
            "from core.tools import RuntimeToolsContext",
            "from core.tools import create_runtime_tools",
            "from skill.handlers.runtime import SkillCollection",
            "from skill.handlers.runtime import SkillResult",
        )
        with tempfile.TemporaryDirectory() as tmp:
            for statement in statements:
                with self.subTest(statement=statement):
                    completed = subprocess.run(
                        [sys.executable, "-c", statement],
                        cwd=tmp,
                        check=False,
                        capture_output=True,
                        text=True,
                        env=environment,
                    )
                    self.assertNotEqual(0, completed.returncode)

    def test_core_owns_runtime_and_skill_does_not(self) -> None:
        self.assertTrue(Path("src/core/runtime.py").is_file())
        self.assertTrue(Path("src/core/loop.py").is_file())
        self.assertFalse(Path("src/core/setup.py").exists())
        self.assertFalse(Path("src/core/resources.py").exists())
        self.assertFalse(Path("src/core/agent.py").exists())
        self.assertTrue(Path("src/core/team.py").is_file())
        self.assertFalse(Path("src/skill/task").exists())

    def test_model_skill_management_has_an_explicit_path(self) -> None:
        self.assertTrue(Path("src/skill/handlers/model_management.py").is_file())

    def test_removed_coupled_core_domains_do_not_return(self) -> None:
        removed = [
            "src/core/agent.py",
            "src/core/engine.py",
            "src/core/run.py",
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

    def test_task_selection_has_one_task_fact(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in Path("src/skill/tasks").glob("task_*.py")
        )
        self.assertNotIn("class AgentTaskEstimate", source)
        self.assertNotIn("_task_estimate", source)
        self.assertIn("def token_counts", source)

    def test_release_stays_within_the_strict_python_source_gate(self) -> None:
        sources = list(Path("src").rglob("*.py"))
        line_count = sum(
            sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
            for path in sources
        )

        self.assertLess(len(sources), MAX_TOTAL_SOURCE_FILES)
        self.assertLessEqual(line_count, MAX_TOTAL_SOURCE_LINES)

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

    def test_domain_roots_keep_the_readable_release_layout(self) -> None:
        for name, expected in EXPECTED_DOMAIN_CHILDREN.items():
            actual = {
                path.name
                for path in Path("src", name).iterdir()
                if path.name not in {"__init__.py", "__pycache__"}
            }
            self.assertEqual(expected, actual, name)


def _count_non_import_lines(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    import_lines: set[int] = set()
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if isinstance(node, ast.Import | ast.ImportFrom):
            import_lines.update(range(node.lineno, node.end_lineno + 1))
    return sum(
        1
        for line_number, line in enumerate(source.splitlines(), 1)
        if line.strip() and line_number not in import_lines
    )


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

import importlib
import os
import subprocess
import sys
import unittest
from pathlib import Path

from skill.disclosure import ProgressiveDisclosureCore
from skill.runtime.mcp import McpSkillSettings
from skill.runtime.mcp import McpServer
from core.state.memory import Memory
from core.models import SubAgentResult, RunResult
from skill.runtime.handlers import create_workflow_policy_from_skill
from skill.manifest import SkillManifest


class SkillArchitectureTests(unittest.TestCase):
    def test_source_root_has_one_declared_layout(self) -> None:
        entries = {
            path.name
            for path in Path("src").iterdir()
            if path.name != "__pycache__"
        }

        self.assertEqual(
            {
                "adapter",
                "cli.py",
                "core",
                "skill",
                "super_agent.py",
            },
            entries,
        )
        builtin_root = Path("src/skill/builtin")
        self.assertTrue((builtin_root / "task/common").is_dir())
        self.assertTrue((builtin_root / "task/code").is_dir())
        self.assertFalse(Path("skill_scenes").exists())

    def test_skill_mechanisms_have_clear_owners(self) -> None:
        self.assertEqual("McpServer", McpServer.__name__)
        self.assertEqual("McpSkillSettings", McpSkillSettings.__name__)
        self.assertEqual("Memory", Memory.__name__)
        self.assertEqual("RunResult", RunResult.__name__)
        self.assertEqual("SubAgentResult", SubAgentResult.__name__)
        self.assertEqual(
            "create_workflow_policy_from_skill",
            create_workflow_policy_from_skill.__name__,
        )
        self.assertEqual("ProgressiveDisclosureCore", ProgressiveDisclosureCore.__name__)
        self.assertEqual("SkillManifest", SkillManifest.__name__)
        self.assertFalse(Path("src/skill/loader.py").exists())

    def test_old_top_level_kind_modules_are_removed(self) -> None:
        self.assertEqual("super_agent", importlib.import_module("super_agent").__name__)
        for module_name in ["super_agent.mcp", "super_agent.memory", "super_agent.workflow"]:
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(module_name)

    def test_core_contains_runtime_and_provider_code(self) -> None:
        for module_name in ["super_agent", "core.config", "core.provider"]:
            self.assertEqual(module_name, importlib.import_module(module_name).__name__)

    def test_runtime_learning_is_an_explicit_post_run_operation(self) -> None:
        engine_source = Path("src/core/runtime/run.py").read_text(encoding="utf-8")
        learning_source = Path(
            "src/skill/learning/runs.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("def _record_task_evaluation", engine_source)
        self.assertIn("def learn_from_run", learning_source)
        self.assertNotIn("EventSubscriber", learning_source)
        self.assertNotIn("learning.requested", engine_source)
        self.assertTrue(
            Path("src/skill/learning/records.py").is_file()
        )
        self.assertFalse(Path("src/core/evolution").exists())
        self.assertTrue(Path("src/core/models.py").is_file())
        self.assertTrue(Path("src/core/runtime/run.py").is_file())
        self.assertFalse(Path("src/core/session.py").exists())
        self.assertTrue(Path("src/core/state/store.py").is_file())
        self.assertFalse(Path("src/core/state/backend.py").exists())
        self.assertFalse(Path("src/core/state/views.py").exists())
        self.assertTrue(Path("src/adapter/storage/local.py").is_file())
        self.assertFalse(Path("src/core/storage").exists())

    def test_skill_package_is_passive_and_small(self) -> None:
        sources = list(Path("src/skill").glob("*.py"))
        self.assertLessEqual(len(sources), 5)
        line_count = sum(
            len(path.read_text(encoding="utf-8").splitlines()) for path in sources
        )
        self.assertLessEqual(line_count, 1500)
        for path in sources:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("from core", source)
            self.assertNotIn("from adapter", source)

    def test_only_center_source_parser_reads_skill_toml(self) -> None:
        python_sources = list(Path("src").rglob("*.py"))
        direct_parsers = [
            path
            for path in python_sources
            if "tomllib.loads" in path.read_text(encoding="utf-8")
            and '"skill.toml"' in path.read_text(encoding="utf-8")
        ]

        self.assertEqual([Path("src/skill/index.py")], direct_parsers)

    def test_only_super_agent_aggregates_public_api(self) -> None:
        for module_name, attribute_name in [
            ("core", "Agent"),
            ("skill", "SkillManifest"),
            ("skill.learning", "SkillUpdater"),
        ]:
            module = importlib.import_module(module_name)
            self.assertFalse(hasattr(module, attribute_name))
        for module_name in ("skill.kinds", "skill.runtime.files"):
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(module_name)

    def test_skill_file_lifecycle_has_one_operations_owner(self) -> None:
        operations = importlib.import_module("skill.runtime.package")
        self.assertEqual("validate_skill_directory", operations.validate_skill_directory.__name__)
        self.assertEqual(
            "apply_skill_directory_updates",
            operations.apply_skill_directory_updates.__name__,
        )
        for module_name in (
            "skill.runtime.files.operations",
            "skill.runtime.files.directory",
            "skill.runtime.files.validation",
        ):
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(module_name)

    def test_cli_configuration_owns_cli_loading(self) -> None:
        package = importlib.import_module("adapter.cli_adapter")
        configuration = importlib.import_module("adapter.cli_adapter.configuration")
        self.assertFalse(Path("src/adapter/cli_adapter/__init__.py").exists())
        self.assertFalse(hasattr(package, "CliConfig"))
        self.assertEqual("CliConfig", configuration.CliConfig.__name__)
        self.assertEqual("load_agent", configuration.load_agent.__name__)
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("adapter.cli_adapter.loaders")

    def test_real_modules_and_public_api_import_in_fresh_process(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = "src"
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "from skill.manifest import SkillManifest; "
                "from skill.learning.update import SkillUpdater; "
                "from super_agent import Agent",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)

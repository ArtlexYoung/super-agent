import importlib
import os
import subprocess
import sys
import unittest
from pathlib import Path

from skill.disclosure import ProgressiveDisclosureCore
from skill.kinds.mcp import McpServer
from skill.kinds.memory import MiniMemory
from core.task.models import SubAgentResult, TaskResult
from skill.kinds.workflow import create_workflow_policy
from skill.manifest import SkillManifest


class SkillKindArchitectureTests(unittest.TestCase):
    def test_source_root_has_one_declared_layout(self) -> None:
        entries = {
            path.name
            for path in Path("src").iterdir()
            if path.name != "__pycache__"
        }

        self.assertEqual(
            {
                "adapter",
                "builtin_skills",
                "cli.py",
                "core",
                "skill",
                "super_agent.py",
            },
            entries,
        )

    def test_skill_kinds_are_loaded_from_unified_skill_package(self) -> None:
        self.assertEqual("McpServer", McpServer.__name__)
        self.assertEqual("MiniMemory", MiniMemory.__name__)
        self.assertEqual("TaskResult", TaskResult.__name__)
        self.assertEqual("SubAgentResult", SubAgentResult.__name__)
        self.assertEqual("direct", create_workflow_policy("direct").name)
        self.assertEqual("ProgressiveDisclosureCore", ProgressiveDisclosureCore.__name__)
        self.assertEqual("SkillManifest", SkillManifest.__name__)
        self.assertFalse(Path("src/skill/loader.py").exists())

    def test_old_top_level_kind_modules_are_removed(self) -> None:
        self.assertEqual("super_agent", importlib.import_module("super_agent").__name__)
        for module_name in ["super_agent.mcp", "super_agent.memory", "super_agent.workflow"]:
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(module_name)

    def test_core_contains_runtime_and_provider_code(self) -> None:
        for module_name in ["core.agent", "core.config", "core.provider"]:
            self.assertEqual(module_name, importlib.import_module(module_name).__name__)

    def test_runtime_engine_owns_evaluation_without_importing_skill_evolution(self) -> None:
        engine_source = Path("src/core/engine.py").read_text(encoding="utf-8")
        self.assertIn("def _record_task_evaluation", engine_source)
        self.assertTrue(Path("src/core/state/evaluation.py").is_file())
        self.assertTrue(Path("src/core/session.py").is_file())
        self.assertTrue(Path("src/core/state/store.py").is_file())
        self.assertTrue(Path("src/core/storage/contracts.py").is_file())

    def test_only_center_source_parser_reads_skill_toml(self) -> None:
        python_sources = list(Path("src").rglob("*.py"))
        direct_parsers = [
            path
            for path in python_sources
            if "tomllib.loads" in path.read_text(encoding="utf-8")
            and '"skill.toml"' in path.read_text(encoding="utf-8")
        ]

        self.assertEqual([Path("src/skill/disclosure/source.py")], direct_parsers)

    def test_only_super_agent_aggregates_public_api(self) -> None:
        for module_name, attribute_name in [
            ("core", "Agent"),
            ("skill", "SkillManifest"),
            ("skill.ecosystem", "SkillPackageManager"),
            ("skill.evolution", "SkillEvolutionManager"),
            ("skill.kinds", "MiniMemory"),
        ]:
            module = importlib.import_module(module_name)
            self.assertFalse(hasattr(module, attribute_name))

    def test_real_modules_and_public_api_import_in_fresh_process(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = "src"
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "from skill.manifest import SkillManifest; "
                "from skill.evolution.manager import SkillEvolutionManager; "
                "from super_agent import Agent",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)

import importlib
import os
import subprocess
import sys
import unittest
from pathlib import Path

from skill import SkillLoader, SkillManifest
from skill.kinds.mcp import McpServer
from skill.kinds.memory import MiniMemory
from skill.kinds.workflow import RunResult, SubAgentResult, create_workflow


class SkillKindArchitectureTests(unittest.TestCase):
    def test_skill_kinds_are_loaded_from_unified_skill_package(self) -> None:
        self.assertEqual("McpServer", McpServer.__name__)
        self.assertEqual("MiniMemory", MiniMemory.__name__)
        self.assertEqual("RunResult", RunResult.__name__)
        self.assertEqual("SubAgentResult", SubAgentResult.__name__)
        self.assertEqual("direct", create_workflow("direct").name)
        self.assertEqual("SkillLoader", SkillLoader.__name__)
        self.assertEqual("SkillManifest", SkillManifest.__name__)

    def test_old_top_level_kind_modules_are_removed(self) -> None:
        self.assertEqual("super_agent", importlib.import_module("super_agent").__name__)
        for module_name in ["super_agent.mcp", "super_agent.memory", "super_agent.workflow"]:
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(module_name)

    def test_kind_implementations_stay_inside_skill_package(self) -> None:
        for path in ["src/mcp.py", "src/memory.py", "src/workflow.py", "src/mcp", "src/memory", "src/workflow"]:
            self.assertFalse(Path(path).exists())

    def test_manifest_and_evolution_facade_import_in_fresh_process(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = "src"
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import skill.manifest; from super_agent import SkillEvolutionManager",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)

import importlib
import unittest

from super_agent.skill import SkillLoader, SkillManifest
from super_agent.skill.kinds.mcp import McpServer
from super_agent.skill.kinds.memory import MiniMemory
from super_agent.skill.kinds.workflow import RunResult, SubAgentResult, create_workflow


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
        for module_name in ["super_agent.mcp", "super_agent.memory", "super_agent.workflow"]:
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(module_name)

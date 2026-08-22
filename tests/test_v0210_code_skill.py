import tempfile
import unittest
from pathlib import Path

from adapter.tools import WorkspaceSettings
from core.provider import MockModel
from skill.library import CodeSkill
from super_agent import Agent


class CodeSkillTests(unittest.TestCase):
    def test_loads_workspace_instructions_from_outer_to_inner_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project" / "src"
            root.mkdir(parents=True)
            (Path(directory) / "AGENTS.md").write_text("outer rule", encoding="utf-8")
            (root.parent / "AGENTS.md").write_text("project rule", encoding="utf-8")
            (root / "AGENTS.md").write_text("src rule", encoding="utf-8")

            values = CodeSkill(WorkspaceSettings(root)).load_instructions()

            self.assertEqual(3, len(values))
            self.assertIn("outer rule", values[0])
            self.assertIn("project rule", values[1])
            self.assertIn("src rule", values[2])

    def test_missing_instructions_are_empty_and_do_not_create_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            skill = CodeSkill(WorkspaceSettings(root))

            self.assertEqual((), skill.load_instructions())
            self.assertEqual((), tuple(root.iterdir()))

    def test_tools_combine_workspace_and_declared_process_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            from adapter.process import ProcessSettings

            skill = CodeSkill(
                WorkspaceSettings(root, allow_write=True),
                ProcessSettings(root, (("python3.11", "-V"),)),
            )

            names = {tool.name for tool in skill.tools()}

            self.assertTrue({"list_files", "read_file", "write_file"}.issubset(names))
            self.assertTrue({"start_process", "poll_process", "run_check"}.issubset(names))

    def test_code_skill_can_be_selected_without_mutating_agent_or_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = Agent(MockModel("answer"))
            skill = CodeSkill(WorkspaceSettings(root))
            before = tuple(agent.instructions)

            agent.add_instructions(*skill.load_instructions())

            self.assertEqual(before, tuple(agent.instructions))
            self.assertFalse((root / ".super-agent").exists())


if __name__ == "__main__":
    unittest.main()

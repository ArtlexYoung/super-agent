import tempfile
import unittest
from pathlib import Path

from adapter.cli import _workspace_instructions
from adapter.process import ProcessSettings, ProcessTools
from adapter.tools import CodeWorkspace, WorkspaceSettings


class CodeWorkspaceSetupTests(unittest.TestCase):
    def test_loads_workspace_instructions_from_outer_to_inner_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project" / "src"
            root.mkdir(parents=True)
            (Path(directory) / "AGENTS.md").write_text("outer rule", encoding="utf-8")
            (root.parent / "AGENTS.md").write_text("project rule", encoding="utf-8")
            (root / "AGENTS.md").write_text("src rule", encoding="utf-8")

            values = _workspace_instructions(root)

            self.assertEqual(3, len(values))
            self.assertIn("outer rule", values[0])
            self.assertIn("project rule", values[1])
            self.assertIn("src rule", values[2])

    def test_missing_instructions_are_empty_and_do_not_create_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            self.assertEqual((), _workspace_instructions(root))
            self.assertEqual((), tuple(root.iterdir()))

    def test_tools_combine_workspace_and_declared_process_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = {
                tool.name
                for tool in (
                    *CodeWorkspace(WorkspaceSettings(root, allow_write=True)).tools(),
                    *ProcessTools(
                        ProcessSettings(root, (("python3.11", "-V"),))
                    ).tools(),
                )
            }

            self.assertTrue({"list_files", "read_file", "write_file"}.issubset(names))
            self.assertTrue({"start_process", "poll_process", "run_check"}.issubset(names))

    def test_workspace_setup_does_not_mutate_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual((), _workspace_instructions(root))
            self.assertFalse((root / ".super-agent").exists())


if __name__ == "__main__":
    unittest.main()

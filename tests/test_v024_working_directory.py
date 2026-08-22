import tempfile
import unittest
from pathlib import Path

from core.config import Config, WorkingDirectory, config_from_dict
from core.provider import MockModel
from super_agent import Agent


class WorkingDirectoryTests(unittest.TestCase):
    def test_relative_paths_require_and_use_the_explicit_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            working = WorkingDirectory.from_path(directory)
            agent = Agent(MockModel("answer"), working_directory=working)
            self.assertEqual((Path(directory) / "src").resolve(), agent.resolve_path("src"))
            self.assertEqual(Path("/tmp").resolve(), agent.resolve_path("/tmp"))

    def test_no_working_directory_does_not_fall_back_for_relative_paths(self):
        agent = Agent(MockModel("answer"))
        with self.assertRaisesRegex(ValueError, "explicit working directory"):
            agent.resolve_path("relative.txt")

    def test_working_directory_identity_is_present_in_run_records(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = Agent(MockModel("answer"), working_directory=directory)
            result = agent.run("hello")
            started = next(event for event in result.events if event.event_type == "run.started")
            self.assertEqual(agent.working_directory.identity, started.data["working_directory_id"])

    def test_config_parses_working_directory_without_creating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            config = config_from_dict(
                {"working_directory": "."}, Path(directory) / "agent.toml"
            )
            self.assertEqual(Path(directory).resolve(), config.resolve_working_directory().path)


if __name__ == "__main__":
    unittest.main()

import sys
import tempfile
import time
import unittest
from pathlib import Path

from super_agent.adapter.cli import CliConfig, _build_agent, _storage_from_config
from super_agent.adapter.database import DatabaseStorage
from super_agent.adapter.process import ProcessSettings, ProcessTools
from super_agent.adapter.storage import JsonlStorage, create_storage, verify_storage
from super_agent.core.config import Config, StorageConfig
from super_agent.core.provider import MockModel


class AdapterBoundaryTests(unittest.TestCase):
    def test_cli_storage_helper_uses_jsonl_only_when_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                storage=StorageConfig(
                    backend="none", path=str(Path(directory) / "records")
                )
            )

            backend = _storage_from_config(config)

            self.assertIsInstance(backend, JsonlStorage)
            self.assertFalse((Path(directory) / "records").exists())

    def test_cli_agent_setup_uses_one_library_boundary(self):
        config = Config()
        agent, backend = _build_agent(
            CliConfig(save=False), None, None, config=config
        )

        self.assertIsNone(backend)
        self.assertIsNotNone(agent.library)
        self.assertEqual(1, len(agent.library.paths))

    def test_process_tools_close_is_explicit_and_stops_owned_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = ProcessSettings(
                Path(directory),
                ((sys.executable, "-c", "import time; time.sleep(30)"),),
            )
            processes = ProcessTools(settings)
            start = next(tool for tool in processes.tools() if tool.name == "start_process")
            value = start.handler({"command": list(settings.allowed_commands[0])}, None)
            process = processes._require(str(value["process_id"]))

            self.assertIsNone(process.process.poll())
            processes.close()

            deadline = time.monotonic() + 2
            while process.process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNotNone(process.process.poll())

    def test_mock_model_is_still_available_without_cli_storage(self):
        agent = _build_agent(
            CliConfig(save=False), None, None, config=Config(),
        )[0]
        agent.model = MockModel("answer")

        self.assertEqual("answer", agent.run("hello").text)

    def test_remote_storage_is_created_without_loading_optional_driver(self):
        backend = create_storage(
            "postgresql",
            database_url="postgresql://user:password@localhost/database",
        )

        self.assertIsInstance(backend, DatabaseStorage)

    def test_storage_verification_uses_the_backend_contract(self):
        class VerifiableStorage:
            def verify(self):
                return {"backend": "custom", "ok": True, "records": 3}

        self.assertEqual(
            {"backend": "custom", "ok": True, "records": 3},
            verify_storage(VerifiableStorage()),
        )


if __name__ == "__main__":
    unittest.main()

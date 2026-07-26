from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from agents.agent import Agent
from cli import main
from runtime.config import AgentConfig


class CapabilityPackageTests(unittest.TestCase):
    def test_agent_installs_updates_rolls_back_and_removes_run_controller(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_source = _write_run_controller_package(root / "first", "0.1.0", "first")
            second_source = _write_run_controller_package(root / "second", "0.2.0", "second")
            agent = Agent(AgentConfig.create_default(root))

            installed = agent.install_capability(str(first_source))
            first_result = agent.run("hello")
            updated = agent.update_capability(
                "run_controller",
                "fixed",
                str(second_source),
            )
            second_result = agent.run("hello")
            restored = agent.rollback_capability("run_controller", "fixed")
            restored_result = agent.run("hello")
            agent.remove_capability("run_controller", "fixed")
            default_result = agent.run("hello")

            self.assertEqual("local", installed.descriptor.source)
            self.assertEqual("0.2.0", updated.descriptor.version)
            self.assertEqual("0.1.0", restored.descriptor.version)
            self.assertEqual("first", first_result.text)
            self.assertEqual("second", second_result.text)
            self.assertEqual("first", restored_result.text)
            self.assertEqual("Mock response", default_result.text)
            self.assertEqual("builtin", agent.capabilities.registry.require_capability(
                "run_controller"
            ).descriptor.source)

    def test_capability_cli_persists_versions_without_new_agent_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "agent.toml"
            _run_cli(["init", "--path", str(root)])
            first = _write_run_controller_package(root / "first", "0.1.0", "first")
            second = _write_run_controller_package(root / "second", "0.2.0", "second")

            install_output = _run_cli(
                [
                    "capabilities",
                    "install",
                    "--config",
                    str(config_path),
                    "--source",
                    str(first),
                ]
            )
            _run_cli(
                [
                    "capabilities",
                    "update",
                    "--config",
                    str(config_path),
                    "--slot",
                    "run_controller",
                    "--name",
                    "fixed",
                    "--source",
                    str(second),
                ]
            )
            list_output = _run_cli(
                [
                    "capabilities",
                    "list",
                    "--config",
                    str(config_path),
                    "--output",
                    "json",
                ]
            )
            rollback_output = _run_cli(
                [
                    "capabilities",
                    "rollback",
                    "--config",
                    str(config_path),
                    "--slot",
                    "run_controller",
                    "--name",
                    "fixed",
                ]
            )

            data = json.loads(list_output)
            self.assertIn("run_controller:fixed@0.1.0", install_output)
            self.assertEqual("0.2.0", data["capabilities"][0]["version"])
            self.assertIn("run_controller:fixed@0.1.0", rollback_output)

    def test_package_rejects_entry_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _write_run_controller_package(root / "bad", "0.1.0", "bad")
            manifest_path = source / "capability.toml"
            manifest_path.write_text(
                manifest_path.read_text(encoding="utf-8").replace(
                    'entry_file = "capability.py"',
                    'entry_file = "../capability.py"',
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "stay inside"):
                Agent(AgentConfig.create_default(root)).install_capability(str(source))

    def test_agent_created_package_allows_updates_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _write_run_controller_package(root / "created", "0.1.0", "created")
            manifest_path = source / "capability.toml"
            manifest_path.write_text(
                manifest_path.read_text(encoding="utf-8")
                .replace("agent_created = false", "agent_created = true")
                .replace("agent_can_update = false", ""),
                encoding="utf-8",
            )

            installed = Agent(AgentConfig.create_default(root)).install_capability(str(source))

            self.assertTrue(installed.descriptor.agent_created)
            self.assertTrue(installed.descriptor.agent_can_update)

    def test_failed_dependency_validation_does_not_leave_installed_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _write_run_controller_package(root / "dependent", "0.1.0", "unused")
            manifest_path = source / "capability.toml"
            manifest_path.write_text(
                manifest_path.read_text(encoding="utf-8").replace(
                    "dependencies = []",
                    'dependencies = ["missing_slot"]',
                ),
                encoding="utf-8",
            )
            agent = Agent(AgentConfig.create_default(root))

            with self.assertRaisesRegex(KeyError, "missing_slot"):
                agent.install_capability(str(source))

            self.assertEqual([], agent.list_installed_capabilities())


def _write_run_controller_package(
    path: Path,
    version: str,
    response: str,
) -> Path:
    path.mkdir(parents=True)
    (path / "capability.toml").write_text(
        f"""
schema_version = 1
slot = "run_controller"
name = "fixed"
description = "Fixed test controller"
version = "{version}"
entry_file = "capability.py"
entry_class = "Capability"
dependencies = []
permissions = ["execute"]
agent_created = false
agent_can_update = false
""".strip(),
        encoding="utf-8",
    )
    (path / "capability.py").write_text(
        f"""
from runtime.models import RunResult


class Capability:
    name = "fixed"
    version = "{version}"

    def run_agent(self, request, session):
        return RunResult(
            text="{response}",
            workflow="fixed",
            skills=[],
            warning_messages=request.warning_messages,
            run_id=session.run_id,
        )
""".strip(),
        encoding="utf-8",
    )
    return path


def _run_cli(arguments: list[str]) -> str:
    output = StringIO()
    with redirect_stdout(output):
        code = main(arguments)
    if code != 0:
        raise AssertionError(f"CLI returned {code}: {arguments}")
    return output.getvalue()

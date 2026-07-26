from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from agents.agent import Agent
from capability.evolution import CapabilityEvaluationCase
from capability.evolution.manager import (
    CapabilityEvolutionManager,
    CapabilityEvolutionRuntimeAccess,
)
from cli import main
from provider.chat import MockProvider
from runtime.config import AgentConfig


class CapabilityEvolutionTests(unittest.TestCase):
    def test_weak_candidate_is_rejected_without_changing_active_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _make_agent(root, [_candidate_response("0.1.1", "candidate", "return 'wrong'")])
            _install_initial_capability(agent, root)
            manager = agent.create_capability_evolution_manager()
            candidate = manager.create_capability_candidate(
                "run_controller",
                "adaptive",
                "improve behavior",
            )

            report = manager.evaluate_capability_candidate(
                candidate.candidate_id,
                [CapabilityEvaluationCase("quality", {"value": 1}, "right")],
            )

            self.assertFalse(report.passed)
            self.assertEqual(0.0, report.score)
            with self.assertRaisesRegex(ValueError, "did not pass"):
                manager.promote_capability_candidate(candidate.candidate_id)
            self.assertEqual("original", agent.run("hello").text)

    def test_strong_candidate_is_promoted_and_changes_real_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _make_agent(
                root,
                [_candidate_response("0.1.1", "improved", "return input_data['value'] * 2")],
            )
            _install_initial_capability(agent, root)
            manager = agent.create_capability_evolution_manager()
            candidate = manager.create_capability_candidate(
                "run_controller",
                "adaptive",
                "double values and improve runs",
            )
            report = manager.evaluate_capability_candidate(
                candidate.candidate_id,
                [CapabilityEvaluationCase("double", {"value": 3}, 6)],
            )

            installed = manager.promote_capability_candidate(candidate.candidate_id)

            self.assertTrue(report.passed)
            self.assertEqual("0.1.1", installed.descriptor.version)
            self.assertEqual("improved", agent.run("hello").text)
            records = manager.runtime_access.store.read_evaluation_records(
                target_type="capability",
                source_type="candidate_evaluation",
            )
            self.assertEqual(1, len(records))
            self.assertEqual("run_controller:adaptive", records[0].target.key)

    def test_promotion_rejects_changed_parent_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _make_agent(
                root,
                [_candidate_response("0.1.1", "candidate", "return 'pass'")],
            )
            _install_initial_capability(agent, root)
            manager = agent.create_capability_evolution_manager()
            candidate = manager.create_capability_candidate(
                "run_controller",
                "adaptive",
                "improve behavior",
            )
            manager.evaluate_capability_candidate(
                candidate.candidate_id,
                [CapabilityEvaluationCase("pass", {}, "pass")],
            )
            replacement = _write_capability_package(
                root / "external-update",
                "0.2.0",
                "human update",
                "return 'human'",
            )
            agent.update_capability("run_controller", "adaptive", str(replacement))

            with self.assertRaisesRegex(ValueError, "parent changed"):
                manager.promote_capability_candidate(candidate.candidate_id)

            self.assertEqual("human update", agent.run("hello").text)

    def test_candidate_file_tampering_is_rejected_before_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _make_agent(
                root,
                [_candidate_response("0.1.1", "candidate", "return 'pass'")],
            )
            _install_initial_capability(agent, root)
            manager = agent.create_capability_evolution_manager()
            candidate = manager.create_capability_candidate(
                "run_controller",
                "adaptive",
                "improve behavior",
            )
            with (candidate.package_path / "capability.py").open("a", encoding="utf-8") as file:
                file.write("\n# changed after proposal\n")

            with self.assertRaisesRegex(ValueError, "changed after proposal"):
                manager.evaluate_capability_candidate(
                    candidate.candidate_id,
                    [CapabilityEvaluationCase("pass", {}, "pass")],
                )

    def test_timeout_and_exception_become_rejection_evidence(self) -> None:
        responses = [
            _candidate_response(
                "0.1.1",
                "slow",
                "import time\ntime.sleep(1.0)\nreturn 'late'",
            ),
            _candidate_response(
                "0.1.1",
                "broken",
                "raise RuntimeError('broken candidate')",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _make_agent(root, responses)
            _install_initial_capability(agent, root)
            manager = agent.create_capability_evolution_manager(timeout_seconds=0.3)

            timeout_candidate = manager.create_capability_candidate(
                "run_controller",
                "adaptive",
                "test timeout",
            )
            timeout_report = manager.evaluate_capability_candidate(
                timeout_candidate.candidate_id,
                [CapabilityEvaluationCase("timeout", {}, "late")],
            )
            exception_manager = agent.create_capability_evolution_manager(
                timeout_seconds=2.0
            )
            exception_candidate = exception_manager.create_capability_candidate(
                "run_controller",
                "adaptive",
                "test exception",
            )
            exception_report = exception_manager.evaluate_capability_candidate(
                exception_candidate.candidate_id,
                [CapabilityEvaluationCase("exception", {}, "unused")],
            )

            self.assertEqual("TimeoutExpired", timeout_report.case_results[0].error_type)
            self.assertEqual("RuntimeError", exception_report.case_results[0].error_type)
            self.assertFalse(timeout_report.passed)
            self.assertFalse(exception_report.passed)

    def test_rollback_restores_previous_package_and_agent_registration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _make_agent(
                root,
                [_candidate_response("0.1.1", "improved", "return 'pass'")],
            )
            _install_initial_capability(agent, root)
            manager = agent.create_capability_evolution_manager()
            result = manager.evolve_capability(
                "run_controller",
                "adaptive",
                "improve behavior",
                [CapabilityEvaluationCase("pass", {}, "pass")],
            )
            self.assertEqual("promoted", result.status)
            self.assertEqual("improved", agent.run("hello").text)

            restored = manager.rollback_capability("run_controller", "adaptive")

            self.assertEqual("0.1.0", restored.descriptor.version)
            self.assertEqual("original", agent.run("hello").text)

    def test_failed_activation_restores_parent_and_allows_exact_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _make_agent(
                root,
                [_candidate_response("0.1.1", "improved", "return 'pass'")],
            )
            _install_initial_capability(agent, root)
            manager = agent.create_capability_evolution_manager()
            candidate = manager.create_capability_candidate(
                "run_controller",
                "adaptive",
                "improve behavior",
            )
            manager.evaluate_capability_candidate(
                candidate.candidate_id,
                [CapabilityEvaluationCase("pass", {}, "pass")],
            )
            replacement_calls = 0

            def fail_first_replacement(registry):
                nonlocal replacement_calls
                replacement_calls += 1
                if replacement_calls == 1:
                    raise RuntimeError("activation failed")
                agent._set_capability_registry(registry)

            retrying_manager = CapabilityEvolutionManager(
                CapabilityEvolutionRuntimeAccess(
                    config=manager.runtime_access.config,
                    package_manager=manager.runtime_access.package_manager,
                    provider=manager.runtime_access.provider,
                    store=manager.runtime_access.store,
                    read_capability_registry=lambda: agent.capabilities.registry,
                    replace_capability_registry=fail_first_replacement,
                )
            )

            with self.assertRaisesRegex(RuntimeError, "activation failed"):
                retrying_manager.promote_capability_candidate(candidate.candidate_id)

            active = manager.runtime_access.package_manager.load_capability(
                "run_controller",
                "adaptive",
            )
            self.assertEqual("0.1.0", active.descriptor.version)
            self.assertEqual("original", agent.run("hello").text)

            promoted = retrying_manager.promote_capability_candidate(candidate.candidate_id)

            self.assertEqual("0.1.1", promoted.descriptor.version)
            self.assertEqual("improved", agent.run("hello").text)

    def test_candidates_and_evolution_evidence_are_isolated_by_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _make_agent(
                root,
                [_candidate_response("0.1.1", "candidate", "return 'pass'")],
            )
            _install_initial_capability(agent, root)
            alice = agent.create_capability_evolution_manager("alice")
            bob = agent.create_capability_evolution_manager("bob")

            candidate = alice.create_capability_candidate(
                "run_controller",
                "adaptive",
                "improve behavior",
            )

            with self.assertRaisesRegex(KeyError, "candidate not found"):
                bob.evaluate_capability_candidate(
                    candidate.candidate_id,
                    [CapabilityEvaluationCase("pass", {}, "pass")],
                )
            self.assertEqual([], bob.runtime_access.store.read_evolution_events())
            self.assertEqual(1, len(alice.runtime_access.store.read_evolution_events()))

    def test_installed_capability_is_loaded_by_a_new_agent_without_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _make_agent(root, [])
            _install_initial_capability(agent, root)

            reloaded = Agent(AgentConfig.create_default(root))

            self.assertEqual("original", reloaded.run("hello").text)

    def test_cli_proposes_evaluates_and_promotes_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "agent.toml"
            _run_cli(["init", "--path", str(root)])
            initial = _write_capability_package(
                root / "initial-capability",
                "0.1.0",
                "original",
                "return 'original-evaluation'",
            )
            _run_cli(
                [
                    "capabilities",
                    "install",
                    "--config",
                    str(config_path),
                    "--source",
                    str(initial),
                ]
            )
            cases_path = root / "capability-cases.json"
            cases_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "double",
                            "input": {"value": 4},
                            "expected_output": 8,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            provider = SequenceProvider(
                [_candidate_response("0.1.1", "cli-improved", "return input_data['value'] * 2")]
            )
            with patch("agents.agent.create_chat_provider", return_value=provider):
                output = _run_cli(
                    [
                        "capabilities",
                        "propose",
                        "--config",
                        str(config_path),
                        "--slot",
                        "run_controller",
                        "--name",
                        "adaptive",
                        "--goal",
                        "double values",
                    ]
                )
                candidate_id = output.strip().split(": ", 1)[1]
                _run_cli(
                    [
                        "capabilities",
                        "evaluate",
                        "--config",
                        str(config_path),
                        "--candidate-id",
                        candidate_id,
                        "--cases",
                        str(cases_path),
                    ]
                )
                _run_cli(
                    [
                        "capabilities",
                        "promote",
                        "--config",
                        str(config_path),
                        "--candidate-id",
                        candidate_id,
                    ]
                )

            reloaded = Agent.load_from_config_file(str(config_path))
            self.assertEqual("cli-improved", reloaded.run("hi").text)


class SequenceProvider(MockProvider):
    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self.responses = list(responses)

    def send_chat_messages(self, messages: list[dict[str, object]], model: str) -> str:
        self.last_messages = messages
        if not self.responses:
            raise AssertionError("unexpected model request")
        return self.responses.pop(0)


def _make_agent(root: Path, responses: list[str]) -> Agent:
    return Agent(
        AgentConfig.create_default(root),
        provider=SequenceProvider(responses),
    )


def _install_initial_capability(agent: Agent, root: Path) -> None:
    source = _write_capability_package(
        root / "initial-capability",
        "0.1.0",
        "original",
        "return 'original-evaluation'",
    )
    agent.install_capability(str(source))


def _candidate_response(
    version: str,
    run_text: str,
    evaluation_body: str,
) -> str:
    manifest, implementation = _capability_files(version, run_text, evaluation_body)
    return json.dumps(
        {
            "write_files": {
                "capability.toml": manifest,
                "capability.py": implementation,
            },
            "delete_files": [],
        }
    )


def _write_capability_package(
    path: Path,
    version: str,
    run_text: str,
    evaluation_body: str,
) -> Path:
    path.mkdir(parents=True)
    manifest, implementation = _capability_files(version, run_text, evaluation_body)
    (path / "capability.toml").write_text(manifest, encoding="utf-8")
    (path / "capability.py").write_text(implementation, encoding="utf-8")
    return path


def _capability_files(
    version: str,
    run_text: str,
    evaluation_body: str,
) -> tuple[str, str]:
    manifest = f"""
schema_version = 1
slot = "run_controller"
name = "adaptive"
description = "Adaptive test run controller"
version = "{version}"
entry_file = "capability.py"
entry_class = "Capability"
dependencies = []
permissions = ["execute"]
agent_created = true
agent_can_update = true
""".strip()
    body = "\n".join(f"        {line}" for line in evaluation_body.splitlines())
    implementation = f"""
from runtime.models import RunResult


class Capability:
    name = "adaptive"
    version = "{version}"

    def evaluate_capability(self, input_data):
{body}

    def run_agent(self, request, session):
        return RunResult(
            text={run_text!r},
            workflow="adaptive",
            skills=[],
            warning_messages=request.warning_messages,
            run_id=session.run_id,
        )
""".strip()
    return manifest + "\n", implementation + "\n"


def _run_cli(arguments: list[str]) -> str:
    output = StringIO()
    with redirect_stdout(output):
        code = main(arguments)
    if code != 0:
        raise AssertionError(f"CLI returned {code}: {arguments}")
    return output.getvalue()

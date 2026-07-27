from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from agents.agent import Agent
from provider.chat import MockProvider
from runtime.config import AgentConfig
from runtime.evaluation import (
    EvaluationResult,
    EvaluationRecord,
    EvaluationSource,
    EvaluationTarget,
    EvaluationTokenUsage,
    create_evaluation_record,
)
from runtime.evolution.files import compare_directory_versions
from runtime.evolution.schedule_state import (
    EvolutionScheduleTarget,
    evolution_schedule_to_dict,
)
from runtime.evolution.scheduler import AutonomousEvolutionScheduler
from runtime.evolution.evidence import summarize_evaluation_evidence
from runtime.evolution.service import AutomaticEvolutionService
from runtime.store import create_local_runtime_store
from skill.evaluation import create_indexed_skill_evaluation_target
from support import write_workflow_skill


class AutonomousEvolutionSchedulerTests(unittest.TestCase):
    def test_evidence_hash_covers_record_content_not_only_record_id(self) -> None:
        original = _record(_target(), score=0.9)
        changed = replace(
            original,
            result=replace(original.result, score=0.4),
        )

        original_hash = summarize_evaluation_evidence([original])[0].evidence_sha256
        changed_hash = summarize_evaluation_evidence([changed])[0].evidence_sha256

        self.assertNotEqual(original_hash, changed_hash)

    def test_scheduler_uses_each_runtime_evidence_signal(self) -> None:
        for reason_code in (
            "failures",
            "low_score",
            "low_freshness",
            "replacement",
            "token_cost",
            "latency",
        ):
            with self.subTest(reason_code=reason_code), tempfile.TemporaryDirectory() as tmp:
                records, target, freshness = _records_for_reason(reason_code)
                store = create_local_runtime_store(Path(tmp))
                store.append_evaluation_records(records)

                schedules = AutonomousEvolutionScheduler(store).review_evolution_targets(
                    [_schedule_target(target, freshness=freshness)]
                )

                self.assertEqual(1, len(schedules))
                self.assertIn(reason_code, schedules[0].reason_codes)
                self.assertEqual(target, schedules[0].target)

    def test_locked_and_non_evolvable_targets_are_not_scheduled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = _target()
            store = create_local_runtime_store(Path(tmp))
            store.append_evaluation_records([_record(target, success=False)])
            scheduler = AutonomousEvolutionScheduler(store)

            locked = scheduler.review_evolution_targets(
                [_schedule_target(target, can_update=False)]
            )
            unsupported = scheduler.review_evolution_targets(
                [_schedule_target(target, supports_evolution=False)]
            )

            self.assertEqual([], locked)
            self.assertEqual([], unsupported)
            self.assertEqual([], scheduler.list_evolution_schedules())

    def test_same_evidence_is_scheduled_once_and_new_evidence_creates_new_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = _target()
            store = create_local_runtime_store(Path(tmp))
            store.append_evaluation_records([_record(target, success=False, sequence=1)])
            scheduler = AutonomousEvolutionScheduler(store)
            schedule_target = _schedule_target(target)

            first = scheduler.review_evolution_targets([schedule_target])
            duplicate = scheduler.review_evolution_targets([schedule_target])
            store.append_evaluation_records([_record(target, success=False, sequence=2)])
            second = scheduler.review_evolution_targets([schedule_target])

            self.assertEqual(1, len(first))
            self.assertEqual([], duplicate)
            self.assertEqual(1, len(second))
            self.assertNotEqual(first[0].schedule_id, second[0].schedule_id)
            self.assertEqual(2, len(scheduler.list_evolution_schedules()))

    def test_schedules_are_isolated_by_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = _target()
            alpha_store = create_local_runtime_store(root, user_id="alpha")
            beta_store = create_local_runtime_store(root, user_id="beta")
            alpha_store.append_evaluation_records([_record(target, success=False)])
            alpha = AutonomousEvolutionScheduler(alpha_store)
            beta = AutonomousEvolutionScheduler(beta_store)

            created = alpha.review_evolution_targets([_schedule_target(target)])[0]

            self.assertEqual("candidate_recommended", created.decision)
            self.assertEqual([], beta.list_evolution_schedules())
            payload = evolution_schedule_to_dict(created)
            self.assertEqual(created.schedule_id, payload["schedule_id"])
            self.assertEqual("candidate_recommended", payload["decision"])

    def test_directory_comparison_reports_added_modified_and_deleted_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "parent"
            candidate = root / "candidate"
            parent.mkdir()
            candidate.mkdir()
            (parent / "modified.txt").write_text("before", encoding="utf-8")
            (parent / "deleted.txt").write_text("gone", encoding="utf-8")
            (candidate / "modified.txt").write_text("after", encoding="utf-8")
            (candidate / "added.txt").write_text("new", encoding="utf-8")

            difference = compare_directory_versions(parent, candidate)

            self.assertEqual(["added.txt"], difference.added_files)
            self.assertEqual(["modified.txt"], difference.modified_files)
            self.assertEqual(["deleted.txt"], difference.deleted_files)

    def test_failed_run_automatically_promotes_then_rolls_back_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = _write_agent_project(root, agent_can_update=True)
            candidate_response = json.dumps(
                {
                    "write_files": {"SKILL.md": "Improved instructions.\n"},
                    "delete_files": [],
                }
            )
            agent = Agent(
                AgentConfig.load_from_file(config_path),
                provider=_SequenceProvider(
                    [
                        RuntimeError("task failed"),
                        candidate_response,
                        "candidate evaluation output",
                        RuntimeError("promoted regression"),
                    ]
                ),
            )

            with self.assertRaisesRegex(RuntimeError, "task failed"):
                agent.run("echo this")

            schedules = agent.list_evolution_schedules()
            self.assertEqual(["prompt:echo"], [item.target.key for item in schedules])
            self.assertIn("failures", schedules[0].reason_codes)
            self.assertEqual("promoted", schedules[0].decision)
            self.assertTrue(schedules[0].candidate_id)
            self.assertEqual(
                "Improved instructions.\n",
                (root / "skills" / "prompt" / "echo" / "SKILL.md").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertEqual(
                1,
                agent.list_model_routing_stats(purpose="skill_evolution")[0].call_count,
            )
            self.assertEqual(
                1,
                agent.list_model_routing_stats(purpose="skill_evaluation")[0].call_count,
            )

            with self.assertRaisesRegex(RuntimeError, "promoted regression"):
                agent.run("echo this again")

            monitored = agent.list_evolution_schedules()[0]
            self.assertEqual("rolled_back", monitored.decision)
            self.assertEqual(
                "Use echo instructions.\n",
                (root / "skills" / "prompt" / "echo" / "SKILL.md").read_text(
                    encoding="utf-8"
                ),
            )

    def test_scheduling_error_does_not_replace_successful_run_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = Agent(
                AgentConfig.load_from_file(_write_agent_project(root)),
                provider=MockProvider("completed"),
            )

            with patch(
                "runtime.engine.AutomaticEvolutionService.review_and_evolve",
                side_effect=RuntimeError("schedule unavailable"),
            ):
                result = agent.run("echo this")

            events = agent.runtime.create_store().read_run_events(result.run_id)
            self.assertEqual("completed", result.text)
            self.assertIn("evolution.automation_failed", [item.event_type for item in events])

    def test_automatic_service_records_candidate_difference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = _write_agent_project(root, agent_can_update=True)
            response = json.dumps(
                {
                    "write_files": {"SKILL.md": "Improved instructions.\n"},
                    "delete_files": [],
                }
            )
            agent = Agent(
                AgentConfig.load_from_file(config_path),
                provider=_SequenceProvider([response, "evaluation output"]),
            )
            manager = agent.create_skill_evolution_manager("alice")
            entry = manager.skill_disclosure.prepare_skill_index().require_skill(
                "echo",
                "prompt",
            )
            target = create_indexed_skill_evaluation_target(entry)
            store = agent.runtime.create_store("alice")
            store.append_evaluation_records([_record(target, success=False)])
            updated = AutomaticEvolutionService(store, manager).review_and_evolve(
                [
                    EvolutionScheduleTarget(
                        target=target,
                        agent_created=True,
                        agent_can_update=True,
                        supports_evolution=True,
                        freshness=70.0,
                    )
                ]
            )

            completed = next(item for item in updated if item.decision == "promoted")
            self.assertTrue(completed.candidate_id)
            self.assertIsNotNone(completed.candidate_difference)
            changed = completed.candidate_difference
            assert changed is not None
            self.assertIn("SKILL.md", changed.modified_files)
            self.assertIn("skill.toml", changed.modified_files)


def _records_for_reason(
    reason_code: str,
) -> tuple[list[EvaluationRecord], EvaluationTarget, float]:
    target = _target()
    if reason_code == "failures":
        return [_record(target, success=False)], target, 70.0
    if reason_code == "low_score":
        return [
            _record(target, score=0.5, sequence=index)
            for index in range(1, 4)
        ], target, 70.0
    if reason_code == "low_freshness":
        return [_record(target, sequence=index) for index in range(1, 3)], target, 20.0
    if reason_code == "token_cost":
        return [_record(target, input_tokens=13_000)], target, 70.0
    if reason_code == "latency":
        return [_record(target, latency_ms=10_000)], target, 70.0
    replacement = _target(key="prompt:new-search", hash_character="b")
    return [
        _record(target, sequence=1),
        _record(replacement, sequence=2),
        _record(target, sequence=3),
        _record(replacement, sequence=4),
    ], target, 70.0


def _target(
    *,
    key: str = "prompt:search",
    hash_character: str = "a",
) -> EvaluationTarget:
    return EvaluationTarget(
        target_type="skill",
        key=key,
        name=key.rsplit(":", 1)[-1],
        version="0.1.0",
        content_sha256=hash_character * 64,
        function_group="search",
    )


def _record(
    target: EvaluationTarget,
    *,
    success: bool = True,
    score: float = 1.0,
    input_tokens: int = 10,
    output_tokens: int = 5,
    latency_ms: int = 20,
    sequence: int = 1,
) -> EvaluationRecord:
    return create_evaluation_record(
        target,
        EvaluationSource(source_type="agent_run", run_id=f"run-{sequence}"),
        EvaluationResult(
            success=success,
            score=score if success else 0.0,
            token_usage=EvaluationTokenUsage(input_tokens, output_tokens),
            latency_ms=latency_ms,
            error_type="" if success else "RuntimeError",
            checks=["pass" if success else "fail"],
        ),
        created_at=datetime(2026, 7, 1, tzinfo=UTC)
        + timedelta(minutes=sequence),
    )


def _schedule_target(
    target: EvaluationTarget,
    *,
    freshness: float = 70.0,
    can_update: bool = True,
    supports_evolution: bool = True,
) -> EvolutionScheduleTarget:
    return EvolutionScheduleTarget(
        target=target,
        agent_created=True,
        agent_can_update=can_update,
        supports_evolution=supports_evolution,
        freshness=freshness,
    )


class _SequenceProvider(MockProvider):
    def __init__(self, responses: list[str | Exception]) -> None:
        super().__init__()
        self.responses = list(responses)

    def send_chat_messages(self, messages, model):
        if not self.responses:
            raise AssertionError("unexpected provider call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _write_agent_project(root: Path, *, agent_can_update: bool = False) -> Path:
    write_workflow_skill(root)
    skill = root / "skills" / "prompt" / "echo"
    skill.mkdir(parents=True)
    (skill / "skill.toml").write_text(
        f"""
schema_version = 2
name = "echo"
capability = "prompt"
description = "Echo helper"
version = "0.1.0"
triggers = ["echo"]
agent_created = {str(agent_can_update).lower()}
agent_can_update = {str(agent_can_update).lower()}
freshness = 70
function_group = "general"

[entry]
instructions = "SKILL.md"
""".strip(),
        encoding="utf-8",
    )
    (skill / "SKILL.md").write_text("Use echo instructions.\n", encoding="utf-8")
    config = root / "agent.toml"
    config.write_text(
        """
[agent]
name = "evolution-test"
system = "Test system."
workflow = "direct"
memory = "default"
skills = ["echo"]

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return config

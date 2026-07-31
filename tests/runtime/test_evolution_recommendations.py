"""Tests for Skill evolution recommendations and automatic execution."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from super_agent import Agent
from core.provider.chat import MockProvider
from core.config import AgentConfig
from core.evolution.records import (
    EvaluationResult,
    EvaluationRecord,
    EvaluationSource,
    EvaluationTokenUsage,
    append_evaluation_records,
    create_evaluation_record,
)
from core.evolution.change.files import compare_directory_versions
from core.evolution.state import (
    list_skill_evolutions,
)
from core.evolution.recommendations import recommend_skill_revisions
from core.evolution.metrics import summarize_evaluation_evidence
from core.evolution.learning import AutomaticSkillEvolution
from core.evolution.insights import explain_run_with_insight
from core.state.events import create_local_event_store
from core.evolution.models import (
    SkillRevision,
    create_indexed_skill_revision,
    skill_evolution_to_dict,
)
from support import (
    SequenceProvider,
    load_default_evolution_policy,
    write_workflow_skill,
)


class SkillRevisionEvolutionTests(unittest.TestCase):
    def test_evidence_hash_covers_record_content_not_only_record_id(self) -> None:
        original = _record(_target(), score=0.9)
        changed = replace(
            original,
            result=replace(original.result, score=0.4),
        )

        original_hash = summarize_evaluation_evidence([original])[0].evidence_sha256
        changed_hash = summarize_evaluation_evidence([changed])[0].evidence_sha256

        self.assertNotEqual(original_hash, changed_hash)

    def test_recommendations_use_each_runtime_evidence_signal(self) -> None:
        for reason_code in (
            "failures",
            "low_score",
            "low_freshness",
            "replacement",
            "token_cost",
            "latency",
        ):
            with self.subTest(reason_code=reason_code), tempfile.TemporaryDirectory() as tmp:
                records, revision, freshness = _records_for_reason(reason_code)
                store = create_local_event_store(Path(tmp))
                append_evaluation_records(store, records)

                evolutions = recommend_skill_revisions(
                    store,
                    [_evolvable_revision(revision, freshness=freshness)],
                    load_default_evolution_policy(Path(tmp)),
                )

                self.assertEqual(1, len(evolutions))
                self.assertIn(reason_code, evolutions[0].reason_codes)
                self.assertEqual(
                    _evolvable_revision(revision, freshness=freshness),
                    evolutions[0].source_revision,
                )

    def test_locked_and_non_evolvable_revisions_are_not_recommended(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            revision = _target()
            store = create_local_event_store(Path(tmp))
            append_evaluation_records(store, [_record(revision, success=False)])

            locked = recommend_skill_revisions(
                store,
                [_evolvable_revision(revision, can_update=False)],
                load_default_evolution_policy(Path(tmp)),
            )
            unsupported = recommend_skill_revisions(
                store,
                [_evolvable_revision(revision, supports_evolution=False)],
                load_default_evolution_policy(Path(tmp)),
            )

            self.assertEqual([], locked)
            self.assertEqual([], unsupported)
            self.assertEqual([], list_skill_evolutions(store))

    def test_recommendation_thresholds_come_from_evolution_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            revision = _target()
            store = create_local_event_store(root)
            append_evaluation_records(store, [_record(revision)])
            default_policy = load_default_evolution_policy(root)
            low_token_threshold = replace(default_policy, high_average_tokens=1.0)

            self.assertEqual(
                [],
                recommend_skill_revisions(
                    store,
                    [_evolvable_revision(revision)],
                    default_policy,
                ),
            )
            recommended = recommend_skill_revisions(
                store,
                [_evolvable_revision(revision)],
                low_token_threshold,
            )

            self.assertEqual(1, len(recommended))
            self.assertIn("token_cost", recommended[0].reason_codes)

    def test_same_evidence_creates_one_evolution_and_new_evidence_creates_another(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            revision = _target()
            store = create_local_event_store(Path(tmp))
            append_evaluation_records(store, [_record(revision, success=False, sequence=1)])
            evolvable = _evolvable_revision(revision)
            policy = load_default_evolution_policy(Path(tmp))

            first = recommend_skill_revisions(store, [evolvable], policy)
            duplicate = recommend_skill_revisions(store, [evolvable], policy)
            append_evaluation_records(store, [_record(revision, success=False, sequence=2)])
            second = recommend_skill_revisions(store, [evolvable], policy)

            self.assertEqual(1, len(first))
            self.assertEqual([], duplicate)
            self.assertEqual(1, len(second))
            self.assertNotEqual(first[0].evolution_id, second[0].evolution_id)
            self.assertEqual(2, len(list_skill_evolutions(store)))

    def test_evolutions_are_isolated_by_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            revision = _target()
            alpha_store = create_local_event_store(root, user_id="alpha")
            beta_store = create_local_event_store(root, user_id="beta")
            append_evaluation_records(alpha_store, [_record(revision, success=False)])

            created = recommend_skill_revisions(
                alpha_store,
                [_evolvable_revision(revision)],
                load_default_evolution_policy(root),
            )[0]

            self.assertEqual("candidate_recommended", created.status)
            self.assertEqual([], list_skill_evolutions(beta_store))
            payload = skill_evolution_to_dict(created)
            self.assertEqual(created.evolution_id, payload["evolution_id"])
            self.assertEqual("candidate_recommended", payload["status"])

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

    def test_failed_run_does_not_change_active_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = _write_agent_project(root, agent_can_update=True)
            agent = Agent(
                AgentConfig.load_from_file(config_path),
                provider=SequenceProvider([RuntimeError("task failed")]),
                use_storage=True,
            )

            with self.assertRaisesRegex(RuntimeError, "task failed"):
                agent.run("echo this")

            evolutions = agent.for_user("local").skills.list_evolutions()
            self.assertEqual([], evolutions)
            self.assertEqual(
                "Use echo instructions.\n",
                (root / "skills" / "prompt" / "echo" / "SKILL.md").read_text(
                    encoding="utf-8"
                ),
            )

    def test_scheduling_error_fails_the_requested_evolution_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = Agent(
                AgentConfig.load_from_file(_write_agent_project(root)),
                provider=MockProvider("completed"),
                use_storage=True,
            )

            result = agent.run("echo this")
            with patch(
                "core.evolution.learning."
                "AutomaticSkillEvolution.run_pending_skill_evolution_stages",
                side_effect=RuntimeError("recommendation unavailable"),
            ), self.assertRaisesRegex(RuntimeError, "recommendation unavailable"):
                agent.for_user("local").runs.learn(result.run_id)

            events = agent.runtime.create_event_store().read_run_events(result.run_id)
            self.assertEqual("completed", result.text)
            self.assertEqual("learning.failed", events[-1].event_type)
            self.assertEqual("skill_evolution", events[-1].data["stage"])

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
                provider=SequenceProvider(
                    [response, "candidate evaluation output", "baseline evaluation output"]
                ),
                use_storage=True,
            )
            manager = agent.for_user("alice").skills.create_evolution_manager()
            entry = manager.skill_disclosure.prepare_skill_index().require_skill(
                "echo",
                "prompt",
            )
            revision = create_indexed_skill_revision(
                entry,
                evolution_supported=True,
            )
            revision = replace(
                revision,
                agent_created=True,
                agent_can_update=True,
            )
            store = agent.runtime.create_event_store("alice")
            append_evaluation_records(store, [_record(revision, success=False)])
            updated = AutomaticSkillEvolution(
                store,
                manager,
                load_default_evolution_policy(root),
            ).run_pending_skill_evolution_stages([revision])

            completed = next(item for item in updated if item.status == "promoted")
            self.assertTrue(completed.candidate_id)
            self.assertIsNotNone(completed.candidate_difference)
            changed = completed.candidate_difference
            assert changed is not None
            self.assertIn("SKILL.md", changed.modified_files)
            self.assertIn("skill.toml", changed.modified_files)


def _records_for_reason(
    reason_code: str,
) -> tuple[list[EvaluationRecord], SkillRevision, float]:
    revision = _target()
    if reason_code == "failures":
        return [_record(revision, success=False)], revision, 70.0
    if reason_code == "low_score":
        return [
            _record(revision, score=0.5, sequence=index)
            for index in range(1, 4)
        ], revision, 70.0
    if reason_code == "low_freshness":
        return [_record(revision, sequence=index) for index in range(1, 3)], revision, 20.0
    if reason_code == "token_cost":
        return [_record(revision, input_tokens=13_000)], revision, 70.0
    if reason_code == "latency":
        return [_record(revision, latency_ms=10_000)], revision, 70.0
    replacement = _target(key="prompt:new-search", hash_character="b")
    return [
        _record(revision, sequence=1),
        _record(replacement, sequence=2),
        _record(revision, sequence=3),
        _record(replacement, sequence=4),
    ], revision, 70.0


def _target(
    *,
    key: str = "prompt:search",
    hash_character: str = "a",
) -> SkillRevision:
    skill_type, name = key.split(":", 1)
    return SkillRevision(
        key=key,
        skill_type=skill_type,
        name=name,
        version="0.1.0",
        content_sha256=hash_character * 64,
        function_group="search",
        agent_created=True,
        agent_can_update=True,
        evolution_supported=True,
        freshness=70.0,
    )


def _record(
    revision: SkillRevision,
    *,
    success: bool = True,
    score: float = 1.0,
    input_tokens: int = 10,
    output_tokens: int = 5,
    latency_ms: int = 20,
    sequence: int = 1,
) -> EvaluationRecord:
    return create_evaluation_record(
        revision,
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


def _evolvable_revision(
    revision: SkillRevision,
    *,
    freshness: float = 70.0,
    can_update: bool = True,
    supports_evolution: bool = True,
) -> SkillRevision:
    return replace(
        revision,
        agent_can_update=can_update,
        evolution_supported=supports_evolution,
        freshness=freshness,
    )


def _write_agent_project(root: Path, *, agent_can_update: bool = False) -> Path:
    write_workflow_skill(root)
    skill = root / "skills" / "prompt" / "echo"
    skill.mkdir(parents=True)
    (skill / "skill.toml").write_text(
        f"""
type = "prompt"
description = "Echo helper"

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
skills = ["workflow:direct", "memory:default", "echo"]

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return config

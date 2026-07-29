import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from super_agent import Agent
from core.provider.chat import MockProvider
from core.config import AgentConfig
from skill.evolution.records import (
    EvaluationResult,
    EvaluationSource,
    EvaluationTokenUsage,
    append_evaluation_records,
    create_evaluation_record,
    evaluation_record_from_dict,
    evaluation_record_to_dict,
    read_evaluation_records,
)
from skill.state.store import create_local_runtime_store
from skill.disclosure import ProgressiveDisclosureCore
from skill.evolution.freshness import calculate_skill_freshness
from skill.evolution.change.revision import SkillRevision
from support import write_memory_skill, write_workflow_skill


class SkillFreshnessTests(unittest.TestCase):
    def test_manifest_reads_freshness_and_function_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "research"
            skill_dir.mkdir(parents=True)
            (skill_dir / "skill.toml").write_text(
                """
schema_version = 3
name = "research"
type = "prompt"
description = "Research helper"
version = "0.1.0"
freshness = 83.5
function_group = "search"
freshness_updated_at = "2026-07-07T12:00:00Z"
triggers = ["research"]

[entry]
instructions = "SKILL.md"
""".strip(),
                encoding="utf-8",
            )

            disclosure = ProgressiveDisclosureCore(
                [skill_dir.parent],
            )
            disclosure.prepare_skill_index()
            manifest = disclosure.open_skill("research", "prompt").read_manifest()

            self.assertEqual(83.5, manifest.freshness)
            self.assertEqual("search", manifest.function_group)
            self.assertEqual("2026-07-07T12:00:00Z", manifest.freshness_updated_at)

    def test_freshness_store_records_success_and_updates_score_without_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            called_at = datetime(2026, 7, 7, 12, tzinfo=UTC)
            run_record = _skill_evaluation_record(
                "prompt:research",
                "search",
                called_at,
            )
            candidate_record = create_evaluation_record(
                run_record.revision,
                EvaluationSource(
                    source_type="candidate_evaluation",
                    candidate_id="candidate-1",
                    case_name="research case",
                ),
                run_record.result,
                created_at=called_at,
            )

            store = create_local_runtime_store(root)
            append_evaluation_records(store, [run_record, candidate_record])

            records = read_evaluation_records(store)
            stats = calculate_skill_freshness(
                read_evaluation_records(store, source_type="agent_run"),
                called_at,
            )["prompt:research"]
            self.assertEqual(1, stats["call_count"])
            self.assertEqual(1, stats["success_count"])
            self.assertEqual("prompt:research", records[0].revision.key)
            self.assertEqual(2, len(records))
            self.assertFalse((root / "skill_events.jsonl").exists())

    def test_same_function_successful_followup_reduces_previous_skill_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = create_local_runtime_store(root)
            first_time = datetime(2026, 7, 7, 12, tzinfo=UTC)
            second_time = first_time + timedelta(minutes=5)

            append_evaluation_records(
                store,
                [_skill_evaluation_record("prompt:old-search", "search", first_time)]
            )
            before = calculate_skill_freshness(
                read_evaluation_records(store, source_type="agent_run"),
                first_time,
            )["prompt:old-search"]["freshness"]
            append_evaluation_records(
                store,
                [_skill_evaluation_record("prompt:new-search", "search", second_time, run_id="run-2")]
            )

            old_stats = calculate_skill_freshness(
                read_evaluation_records(store, source_type="agent_run"),
                second_time,
            )["prompt:old-search"]
            self.assertEqual(1, old_stats["same_function_followups"])
            self.assertEqual(1, old_stats["same_function_successful_followups"])
            self.assertLess(old_stats["freshness"], before)

    def test_evaluation_record_schema_is_strict_and_filterable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _skill_evaluation_record(
                "prompt:research",
                "search",
                datetime(2026, 7, 7, 12, tzinfo=UTC),
            )
            store = create_local_runtime_store(root)

            append_evaluation_records(store, [record])

            loaded = read_evaluation_records(
                store,
                skill_key="prompt:research",
                source_type="agent_run",
            )
            self.assertEqual([record], loaded)
            payload = evaluation_record_to_dict(record)
            payload["unexpected"] = True
            with self.assertRaisesRegex(ValueError, "schema fields"):
                evaluation_record_from_dict(payload)
            payload = evaluation_record_to_dict(record)
            payload["schema_version"] = 1
            with self.assertRaisesRegex(ValueError, "schema_version must be 2"):
                evaluation_record_from_dict(payload)

    def test_agent_run_records_skill_freshness_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            write_memory_skill(root)
            _write_skill(root, "echo", "general")
            config_path = _write_config(root)
            agent = Agent(AgentConfig.load_from_file(config_path), provider=MockProvider("useful answer"))

            result = agent.run("echo hello")
            agent.learn_from_run(result.run_id)

            store = agent.runtime.create_store()
            records = read_evaluation_records(store)
            stats = calculate_skill_freshness(
                read_evaluation_records(store, source_type="agent_run")
            )
            self.assertEqual(1, stats["prompt:echo"]["call_count"])
            self.assertGreater(stats["prompt:echo"]["freshness"], 70)
            skill_keys = {record.revision.key for record in records}
            self.assertEqual(
                {
                    "memory:default",
                    "planner:default",
                    "prompt:common",
                    "prompt:echo",
                    "scheduler:default",
                    "scene:common",
                    "scene_manager:default",
                    "workflow:direct",
                },
                skill_keys,
            )
            self.assertTrue(all(record.source.run_id == result.run_id for record in records))
            self.assertTrue(all(len(record.revision.content_sha256) == 64 for record in records))

    def test_failed_agent_run_records_failure_for_used_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            _write_skill(root, "echo", "general")
            agent = Agent(
                AgentConfig.load_from_file(_write_config(root)),
                provider=_FailingProvider(),
            )

            with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
                agent.run("echo hello")

            store = agent.runtime.create_store()
            run_id = store.list_runs(1)[0].run_id
            agent.learn_from_run(run_id)
            records = read_evaluation_records(
                store,
                source_type="agent_run"
            )
            self.assertTrue(records)
            self.assertTrue(all(not record.result.success for record in records))
            self.assertTrue(
                all(record.result.error_type == "RuntimeError" for record in records)
            )


def _skill_evaluation_record(
    skill_key: str,
    function_group: str,
    called_at: datetime,
    *,
    run_id: str = "run-1",
):
    return create_evaluation_record(
        revision=SkillRevision(
            key=skill_key,
            skill_type=skill_key.split(":", 1)[0],
            name=skill_key.split(":", 1)[1],
            version="0.1.0",
            content_sha256="a" * 64,
            function_group=function_group,
            agent_created=True,
            agent_can_update=True,
            evolution_supported=True,
            freshness=70.0,
        ),
        source=EvaluationSource(source_type="agent_run", run_id=run_id),
        result=EvaluationResult(
            success=True,
            score=1.0,
            token_usage=EvaluationTokenUsage(input_tokens=12, output_tokens=8),
            latency_ms=25,
            error_type="",
            checks=["pass:run_completed"],
        ),
        created_at=called_at,
    )


class _FailingProvider(MockProvider):
    def send_chat_messages(self, messages, model):
        raise RuntimeError("provider unavailable")


def _write_skill(root: Path, name: str, function_group: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
schema_version = 3
name = "{name}"
type = "prompt"
description = "{name} helper"
version = "0.1.0"
freshness = 70
function_group = "{function_group}"
triggers = ["{name}"]

[entry]
instructions = "SKILL.md"
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text("Answer with the skill.", encoding="utf-8")


def _write_config(root: Path) -> Path:
    config_path = root / "agent.toml"
    config_path.write_text(
        """
[agent]
name = "demo"
system = "Base system."
skills = ["workflow:direct", "memory:default", "echo"]

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return config_path

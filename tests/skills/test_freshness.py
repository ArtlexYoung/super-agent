import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core import Agent, AgentConfig
from core.provider import MockProvider
from skill import SkillManifest
from skill.freshness import SkillFreshnessStore, SkillRunRecord
from support import write_workflow_skill


class SkillFreshnessTests(unittest.TestCase):
    def test_manifest_reads_freshness_and_function_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "research"
            skill_dir.mkdir(parents=True)
            (skill_dir / "skill.toml").write_text(
                """
name = "research"
description = "Research helper"
freshness = 83.5
function_group = "search"
freshness_updated_at = "2026-07-07T12:00:00Z"
triggers = ["research"]

[entry]
instructions = "SKILL.md"
""".strip(),
                encoding="utf-8",
            )

            manifest = SkillManifest.load_from_file(skill_dir / "skill.toml")

            self.assertEqual(83.5, manifest.freshness)
            self.assertEqual("search", manifest.function_group)
            self.assertEqual("2026-07-07T12:00:00Z", manifest.freshness_updated_at)

    def test_freshness_store_records_success_and_updates_score_without_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SkillFreshnessStore(Path(tmp))
            called_at = datetime(2026, 7, 7, 12, tzinfo=UTC)

            store.record_skill_run(
                SkillRunRecord(
                    skill_name="research",
                    function_group="search",
                    input_text="Find sources about agents.",
                    output_text="Three sources found.",
                    success=True,
                    called_at=called_at,
                )
            )

            stats = store.read_skill_stats()["research"]
            events = (Path(tmp) / "skill_events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, stats["call_count"])
            self.assertEqual(1, stats["success_count"])
            self.assertGreater(stats["freshness"], 70)
            self.assertEqual("research", json.loads(events[0])["skill"])

    def test_same_function_successful_followup_reduces_previous_skill_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SkillFreshnessStore(Path(tmp))
            first_time = datetime(2026, 7, 7, 12, tzinfo=UTC)
            second_time = first_time + timedelta(minutes=5)

            store.record_skill_run(
                SkillRunRecord(
                    skill_name="old-search",
                    function_group="search",
                    input_text="Find sources.",
                    output_text="Weak result.",
                    success=True,
                    called_at=first_time,
                )
            )
            before = store.read_skill_stats()["old-search"]["freshness"]
            store.record_skill_run(
                SkillRunRecord(
                    skill_name="new-search",
                    function_group="search",
                    input_text="Find better sources.",
                    output_text="Better result.",
                    success=True,
                    called_at=second_time,
                )
            )

            old_stats = store.read_skill_stats()["old-search"]
            self.assertEqual(1, old_stats["same_function_followups"])
            self.assertEqual(1, old_stats["same_function_successful_followups"])
            self.assertLess(old_stats["freshness"], before)

    def test_agent_run_records_skill_freshness_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            _write_skill(root, "echo", "general")
            config_path = _write_config(root)
            agent = Agent(AgentConfig.load_from_file(config_path), provider=MockProvider("useful answer"))

            agent.run("echo hello")

            stats_path = root / ".super-agent" / "memory" / "skill_stats.json"
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            self.assertEqual(1, stats["skills"]["echo"]["call_count"])
            self.assertGreater(stats["skills"]["echo"]["freshness"], 70)


def _write_skill(root: Path, name: str, function_group: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
name = "{name}"
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
workflow = "direct"
memory = "default"
skills = ["echo"]

[model]
provider = "mock"
model = "unit-test"

[paths]
skills = ["skills"]
memory = ".super-agent/memory"
""".strip(),
        encoding="utf-8",
    )
    return config_path

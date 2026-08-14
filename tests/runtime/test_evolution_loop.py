from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.config import CommonConfig
from core.provider import MockProvider, ModelResponse, ToolCall
from skill.handlers.runtime import create_progressive_skill_disclosure
from skill.learning.update import SkillChangeCase
from skill.handlers.memory import create_memory_from_skill
from skill.discovery.catalog import ProgressiveDisclosureCore
from super_agent import Agent
from support import SequenceProvider, write_memory_skill, write_workflow_skill


class EvolutionLoopTests(unittest.TestCase):
    def test_run_learning_and_explicit_skill_change_form_a_reversible_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_skill = _write_prompt_skill(root)
            config = CommonConfig.load_from_file(
                _write_config(root, ["prompt:writer"])
            )
            provider = SequenceProvider(
                [
                    "original run",
                    _proposal("Candidate instructions.\n"),
                    "required candidate",
                    "baseline",
                    "updated run",
                ]
            )
            agent = Agent(config, provider=provider, use_storage=True)
            user = agent.for_user("alice")

            first_run = user.run("write a release note")
            learning = user.runs.learn(first_run.run_id)
            updater = user.skills.create_skill_updater()
            change = updater.propose_skill_change(
                "prompt:writer",
                "make the output precise",
            )
            report = updater.test_skill_change(
                change.change_id,
                [
                    SkillChangeCase(
                        "precision",
                        "write this",
                        expected_output_contains=["required"],
                    )
                ],
            )
            applied = updater.apply_skill_change(change.change_id)
            second_run = user.run("write another release note")

            self.assertEqual("original run", first_run.text)
            self.assertEqual("updated run", second_run.text)
            self.assertTrue(report.passed)
            self.assertEqual("0.1.1", applied.version)
            self.assertTrue(
                any(
                    item["skill"] == "prompt:writer"
                    for item in learning.skill_freshness
                )
            )
            second_evidence = second_run.events[-1].data["learning_evidence"]
            writer_revision = next(
                item
                for item in second_evidence["skill_revisions"]
                if item["key"] == "prompt:writer"
            )
            self.assertEqual("0.1.1", writer_revision["version"])
            self.assertIn(
                "Candidate instructions.",
                str(provider.requests[-1][0]["content"]),
            )

            restored = updater.undo_skill_change(change.change_id)

            self.assertIsNotNone(restored)
            self.assertEqual("0.1.0", restored.version)
            self.assertEqual(
                "Original instructions.\n",
                project_skill.joinpath("SKILL.md").read_text(encoding="utf-8"),
            )

    def test_model_can_organize_recalled_memory_during_a_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_skill(root)
            write_workflow_skill(root, name="react", mode="react")
            config = CommonConfig.load_from_file(
                _write_config(root, ["workflow:react", "memory:default"])
            )
            store_agent = Agent(config, provider=MockProvider(), use_storage=True)
            store = store_agent._create_event_store("alice")
            disclosure = create_progressive_skill_disclosure(config, store=store)
            disclosure.prepare_skill_index()
            memory = create_memory_from_skill(
                disclosure.open_skill("default", "memory"),
                store,
            )
            first = memory.remember_long_term("Prefer concise answers.")
            duplicate = memory.remember_long_term("Answers should be concise.")
            provider = MockProvider(
                tool_responses=[
                    ModelResponse(
                        "",
                        [
                            ToolCall(
                                "organize-1",
                                "organize_long_term_memory",
                                {
                                    "operations": [
                                        {
                                            "operation": "merge",
                                            "item_ids": [first.item_id, duplicate.item_id],
                                            "text": "User prefers concise answers.",
                                            "reason": "duplicate stable preference",
                                        }
                                    ]
                                },
                            )
                        ],
                        "tool_calls",
                    ),
                    ModelResponse("Memory organized.", [], "model_finished"),
                ]
            )
            agent = Agent(config, provider=provider, use_storage=True)

            result = agent.for_user("alice").run(
                "Review what you remember about my answer style."
            )
            active = memory.list_long_term()

            self.assertEqual("Memory organized.", result.text)
            self.assertEqual(
                ["User prefers concise answers."],
                [item.text for item in active],
            )
            self.assertEqual(
                "memory.organized",
                store.read_events("memory")[-1].event_type,
            )
            requested_tools = {
                item["function"]["name"]
                for item in provider.tool_requests[0][1]
            }
            self.assertIn("organize_long_term_memory", requested_tools)


def _write_prompt_skill(root: Path) -> Path:
    skill = root / "skills" / "prompt" / "writer"
    skill.mkdir(parents=True)
    skill.joinpath("skill.toml").write_text(
        'type = "prompt"\ndescription = "Write concise text"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    skill.joinpath("SKILL.md").write_text(
        "Original instructions.\n",
        encoding="utf-8",
    )
    return skill


def _write_config(root: Path, skills: list[str]) -> Path:
    path = root / "common.toml"
    selected = json.dumps(skills)
    path.write_text(
        f'''schema_version = 1
kind = "common"

[agent]
name = "evolution-test"
system = "Test the evolution loop."
skills = {selected}
disabled_skills = []

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
''',
        encoding="utf-8",
    )
    return path


def _proposal(instructions: str) -> str:
    return json.dumps(
        {
            "write_files": {"SKILL.md": instructions},
            "delete_files": [],
        }
    )


if __name__ == "__main__":
    unittest.main()

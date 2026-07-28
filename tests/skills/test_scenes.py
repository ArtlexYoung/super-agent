import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from core.agent import Agent
from core.config import AgentConfig
from core.provider.chat import MockProvider, ModelResponse, ToolCall
from skill.kinds.scene import (
    SkillSceneInput,
    create_scene_policy_from_skill,
)
from skill.runners.defaults import create_progressive_skill_disclosure


class SkillSceneTests(unittest.TestCase):
    def test_builtin_scenes_select_one_complete_task_chain(self) -> None:
        cases = [
            (
                "Summarize the supplied notes",
                "scene:common",
                "direct",
                "common",
                "# General task chain",
                {
                    "scene:common",
                    "prompt:common",
                    "memory:default",
                    "planner:default",
                    "workflow:direct",
                },
            ),
            (
                "Implement a repository change",
                "scene:code",
                "code",
                "code",
                "# Repository coding chain",
                {
                    "scene:code",
                    "prompt:code",
                    "memory:code",
                    "planner:code",
                    "workflow:code",
                },
            ),
        ]
        for prompt, scene, workflow, skill, instruction, expected_keys in cases:
            with self.subTest(scene=scene), tempfile.TemporaryDirectory() as tmp:
                provider = MockProvider("finished")
                agent = Agent(AgentConfig.create_default(tmp), provider=provider)

                result = agent.run(prompt)

                store = agent.runtime.create_store()
                selected = _event_data(store, result.run_id, "scene.selected")
                evaluated = {
                    record.revision.key
                    for record in store.read_evaluation_records(source_type="agent_run")
                }
                self.assertEqual(scene, selected["scene_key"])
                self.assertEqual(workflow, result.workflow)
                self.assertEqual([skill], result.skills)
                self.assertEqual(expected_keys, evaluated)
                self.assertIn(instruction, provider.last_messages[0]["content"])

    def test_explicit_and_configured_scene_selection_precede_triggers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = MockProvider("finished")
            agent = Agent(AgentConfig.create_default(root), provider=provider)

            result = agent.run("Implement a repository change", scene="common")

            selected = _event_data(
                agent.runtime.create_store(),
                result.run_id,
                "scene.selected",
            )
            self.assertEqual("direct", result.workflow)
            self.assertEqual("scene:common", selected["scene_key"])
            self.assertEqual("selected by task request", selected["reason"])

            config = AgentConfig.create_default(root)
            configured = replace(
                config,
                agent=replace(config.agent, skills=["scene:common"]),
            )
            configured_agent = Agent(configured, provider=MockProvider("configured"))
            configured_result = configured_agent.run("Implement another change")
            configured_event = _event_data(
                configured_agent.runtime.create_store(),
                configured_result.run_id,
                "scene.selected",
            )
            self.assertEqual("direct", configured_result.workflow)
            self.assertEqual("enabled by agent config", configured_event["reason"])

    def test_scene_selection_rejects_ambiguous_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            disclosure = create_progressive_skill_disclosure(
                AgentConfig.create_default(tmp)
            )
            disclosure.prepare_skill_index()
            with self.assertRaisesRegex(ValueError, "only one configured scene"):
                disclosure.select_skill_scene_for_prompt(
                    "hello",
                    ["scene:common", "scene:code"],
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_scene_skill(root, "overlap", ["workflow:direct"], ["code"])
            disclosure = create_progressive_skill_disclosure(
                AgentConfig.create_default(root)
            )
            disclosure.prepare_skill_index()
            with self.assertRaisesRegex(ValueError, "multiple scene Skills"):
                disclosure.select_skill_scene_for_prompt("write code")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_scene_skill(
                root,
                "another-default",
                ["workflow:direct"],
                [],
                is_default=True,
            )
            disclosure = create_progressive_skill_disclosure(
                AgentConfig.create_default(root)
            )
            disclosure.prepare_skill_index()
            with self.assertRaisesRegex(ValueError, "exactly one default scene"):
                disclosure.select_skill_scene_for_prompt("hello")

    def test_scene_policy_rejects_incomplete_or_nested_chains(self) -> None:
        cases = {
            "missing-workflow": (["prompt:common"], "missing required Skill types"),
            "nested-scene": (
                ["scene:common", "workflow:direct"],
                "cannot include another scene",
            ),
            "duplicate-workflow": (
                ["workflow:direct", "workflow:code"],
                "only one workflow",
            ),
            "duplicate-planner": (
                ["planner:default", "planner:code", "workflow:direct"],
                "only one planner",
            ),
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, (skills, _) in cases.items():
                _write_scene_skill(root, name, skills, [])
            disclosure = create_progressive_skill_disclosure(
                AgentConfig.create_default(root)
            )
            disclosure.prepare_skill_index()

            for name, (_, message) in cases.items():
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
                    create_scene_policy_from_skill(
                        disclosure.open_skill(name, expected_type="scene")
                    )

    def test_missing_scene_reference_fails_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_scene_skill(root, "broken", ["workflow:not-there"], [])
            agent = Agent(
                AgentConfig.create_default(root),
                provider=MockProvider("unused"),
            )

            with self.assertRaisesRegex(KeyError, "workflow:not-there"):
                agent.run("hello", scene="broken")

    def test_scene_reference_without_registered_runner_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_scene_skill(
                root,
                "unsupported",
                ["workflow:direct", "search:private"],
                [],
            )
            agent = Agent(
                AgentConfig.create_default(root),
                provider=MockProvider("unused"),
            )

            with self.assertRaisesRegex(
                ValueError,
                "without registered SkillRunners: search",
            ):
                agent.run("hello", scene="unsupported")

    def test_user_scene_creation_is_next_run_only_and_user_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(tmp),
                provider=MockProvider("finished"),
            )
            alice = agent.for_user("alice")
            request = SkillSceneInput(
                name="audit",
                description="Review financial records",
                triggers=["ledger review"],
                instructions="Check evidence before reporting anomalies.",
            )

            created = alice.skills.create_scene(request)

            self.assertEqual("scene:audit", created.scene_key)
            self.assertEqual("next_run", created.available_from)
            self.assertEqual(
                {
                    "scene:audit",
                    "prompt:audit",
                    "memory:audit",
                    "planner:audit",
                    "workflow:audit",
                },
                set(created.skill_keys),
            )
            with self.assertRaisesRegex(FileExistsError, "replace existing Skills"):
                alice.skills.create_scene(request)

            result = alice.run("Perform a ledger review")
            selected = _event_data(
                agent.runtime.create_store("alice"),
                result.run_id,
                "scene.selected",
            )
            self.assertEqual("audit", result.workflow)
            self.assertEqual("scene:audit", selected["scene_key"])
            with self.assertRaisesRegex(KeyError, "scene:audit"):
                agent.for_user("bob").run("hello", scene="audit")

    def test_model_can_create_scene_without_hot_reloading_current_run(self) -> None:
        provider = MockProvider(
            "finished",
            tool_responses=[
                ModelResponse(
                    "",
                    [
                        ToolCall(
                            "create-scene",
                            "create_skill_scene",
                            {
                                "name": "research",
                                "description": "Investigate cited sources",
                                "triggers": ["source investigation"],
                            },
                        )
                    ],
                    "tool_calls",
                ),
                ModelResponse(
                    "",
                    [ToolCall("list-current", "list_skills", {})],
                    "tool_calls",
                ),
                ModelResponse("created", [], "model_finished"),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(AgentConfig.create_default(tmp), provider=provider)

            result = agent.for_user("alice").run(
                "Create scene for source investigation"
            )

            list_result = next(
                message
                for message in provider.last_messages
                if message.get("tool_call_id") == "list-current"
            )
            listed = json.loads(list_result["content"])
            listed_keys = {
                f"{item['type']}:{item['name']}" for item in listed["skills"]
            }
            self.assertEqual("code", result.workflow)
            self.assertNotIn("scene:research", listed_keys)

            next_result = agent.for_user("alice").run(
                "Investigate this source",
                scene="research",
            )
            self.assertEqual("research", next_result.workflow)


def _event_data(store, run_id: str, event_type: str) -> dict[str, object]:
    return next(
        event.data
        for event in store.read_run_events(run_id)
        if event.event_type == event_type
    )


def _write_scene_skill(
    root: Path,
    name: str,
    skills: list[str],
    triggers: list[str],
    *,
    is_default: bool = False,
) -> None:
    path = root / "skills" / "scene" / name
    path.mkdir(parents=True, exist_ok=True)
    path.joinpath("skill.toml").write_text(
        "\n".join(
            [
                "schema_version = 3",
                f"name = {json.dumps(name)}",
                'type = "scene"',
                f"description = {json.dumps(name + ' task scene')}",
                'version = "0.1.0"',
                f"triggers = {json.dumps(triggers)}",
                f"default = {str(is_default).lower()}",
                "",
                "[configuration]",
                f"skills = {json.dumps(skills)}",
            ]
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()

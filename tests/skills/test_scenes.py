import json
import tempfile
import unittest
from pathlib import Path

from super_agent import Agent
from core.config import AgentConfig
from core.provider.chat import MockProvider, ModelResponse, ToolCall
from skill.ecosystem.scenes import (
    SkillSceneInput,
    read_scene_included_skills,
)
from skill.loaders.defaults import create_progressive_skill_disclosure
from skill.evolution.records import read_evaluation_records


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
                    "scheduler:default",
                    "scene_manager:default",
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
                    "scheduler:default",
                    "scene_manager:default",
                    "workflow:code",
                },
            ),
        ]
        for prompt, scene, workflow, skill, instruction, expected_keys in cases:
            with self.subTest(scene=scene), tempfile.TemporaryDirectory() as tmp:
                provider = MockProvider("finished")
                agent = Agent(
                    AgentConfig.create_default(tmp),
                    provider=provider,
                    use_storage=True,
                )

                result = agent.run(prompt)
                agent.learn_from_run(result.run_id)

                store = agent.runtime.create_event_store()
                selected = _event_data(store, result.run_id, "scene.selected")
                evaluated = {
                    record.revision.key
                    for record in read_evaluation_records(store, source_type="agent_run")
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
            agent = Agent(
                AgentConfig.create_default(root), provider=provider, use_storage=True
            )

            result = agent.run("Implement a repository change", scene="common")

            selected = _event_data(
                agent.runtime.create_event_store(),
                result.run_id,
                "scene.selected",
            )
            self.assertEqual("direct", result.workflow)
            self.assertEqual("scene:common", selected["scene_key"])
            self.assertEqual("selected by task request", selected["reason"])

            configured_agent = Agent(
                AgentConfig.create_default(root),
                provider=MockProvider("configured"),
                use_storage=True,
            )
            configured_agent.use_only_scenes("common")
            configured_result = configured_agent.run("Implement another change")
            configured_event = _event_data(
                configured_agent.runtime.create_event_store(),
                configured_result.run_id,
                "scene.selected",
            )
            self.assertEqual("direct", configured_result.workflow)
            self.assertEqual(
                "selected as the only scene allowed by Agent",
                configured_event["reason"],
            )

    def test_agent_can_restrict_or_disable_scenes_in_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(tmp),
                provider=MockProvider("finished"),
                use_storage=True,
            )
            agent.use_only_scenes("code")

            restricted = agent.run("Summarize these notes")
            restricted_plan = _event_data(
                agent.runtime.create_event_store(),
                restricted.run_id,
                "task.scheduled",
            )
            self.assertEqual("scene:code", restricted_plan["scene"])
            with self.assertRaisesRegex(ValueError, "outside the Agent scene policy"):
                agent.run("hello", scene="common")

            agent.disable_scenes()
            direct = agent.run("Answer directly")
            direct_plan = _event_data(
                agent.runtime.create_event_store(),
                direct.run_id,
                "task.scheduled",
            )
            self.assertIsNone(direct_plan["scene"])
            self.assertIsNone(direct_plan["workflow"])
            self.assertEqual("direct", direct.workflow)

    def test_run_can_disable_scenes_without_changing_agent_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(tmp),
                provider=MockProvider("finished"),
                use_storage=True,
            )

            direct = agent.run("hello", use_scenes=False)
            automatic = agent.run("hello")

            direct_plan = next(
                event.data
                for event in direct.events
                if event.event_type == "task.scheduled"
            )
            automatic_plan = next(
                event.data
                for event in automatic.events
                if event.event_type == "task.scheduled"
            )
            self.assertIsNone(direct_plan["scene"])
            self.assertEqual("scene:common", automatic_plan["scene"])
            with self.assertRaisesRegex(ValueError, "scenes are disabled"):
                agent.run("hello", scene="code", use_scenes=False)

    def test_scene_selection_rejects_ambiguous_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            disclosure = create_progressive_skill_disclosure(
                AgentConfig.create_default(tmp)
            )
            disclosure.prepare_skill_index()
            with self.assertRaisesRegex(ValueError, "duplicate scenes"):
                disclosure.select_skill_scene_for_prompt(
                    "hello",
                    allowed_scenes=("common", "common"),
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

    def test_scene_includes_reject_incomplete_or_nested_chains(self) -> None:
        cases = {
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
                    read_scene_included_skills(
                        disclosure.open_skill(name, expected_type="scene")
                    )

    def test_scene_can_select_content_without_a_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_scene_skill(root, "answer", ["prompt:common"], [])
            disclosure = create_progressive_skill_disclosure(
                AgentConfig.create_default(root)
            )
            disclosure.prepare_skill_index()

            references = read_scene_included_skills(
                disclosure.open_skill("answer", expected_type="scene")
            )
            self.assertEqual(["prompt:common"], [item.key for item in references])

            result = Agent(
                AgentConfig.create_default(root),
                provider=MockProvider("finished"),
            ).run("hello", scene="answer")
            plan = next(
                event.data
                for event in result.events
                if event.event_type == "task.scheduled"
            )
            self.assertEqual("scene:answer", plan["scene"])
            self.assertIsNone(plan["workflow"])
            self.assertEqual("direct", result.workflow)

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

    def test_scene_reference_without_registered_loader_fails(self) -> None:
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
                "without registered SkillLoaders: search",
            ):
                agent.run("hello", scene="unsupported")

    def test_user_scene_creation_is_next_run_only_and_user_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(tmp),
                provider=MockProvider("finished"),
                use_storage=True,
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
                agent.runtime.create_event_store("alice"),
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
            agent = Agent(
                AgentConfig.create_default(tmp), provider=provider, use_storage=True
            )

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

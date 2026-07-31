import json
import tempfile
import unittest
from pathlib import Path

from core.config import AgentConfig
from core.provider.chat import MockProvider, ModelResponse, ToolCall
from core.skill_use.files.scenes import read_scene_included_skills
from core.skill_use.defaults import create_progressive_skill_disclosure
from super_agent import Agent


class SkillSceneTests(unittest.TestCase):
    def test_builtin_scenes_are_ordinary_skill_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            disclosure = create_progressive_skill_disclosure(
                AgentConfig.create_default(tmp)
            )
            disclosure.prepare_skill_index()

            common = read_scene_included_skills(
                disclosure.open_skill("common", expected_type="scene")
            )
            code = read_scene_included_skills(
                disclosure.open_skill("code", expected_type="scene")
            )

            self.assertEqual(
                ["prompt:common", "memory:default", "workflow:direct"],
                [item.key for item in common],
            )
            self.assertEqual(
                ["prompt:code", "memory:default", "workflow:code"],
                [item.key for item in code],
            )

    def test_explicit_scene_loads_its_group_without_an_extra_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = MockProvider("finished")
            result = Agent(
                AgentConfig.create_default(tmp),
                provider=provider,
                use_storage=True,
            ).run("Summarize these notes", scene="common")

            self.assertEqual("finished", result.text)
            self.assertEqual("direct", result.workflow)
            self.assertEqual(
                [
                    "scene:common",
                    "memory:default",
                    "prompt:common",
                    "workflow:direct",
                ],
                result.skills,
            )
            self.assertEqual([], provider.tool_requests)
            self.assertIn("# General task chain", provider.last_messages[0]["content"])

    def test_model_can_activate_one_allowed_scene_through_the_skill_action(self) -> None:
        provider = MockProvider(
            tool_responses=[
                ModelResponse(
                    "",
                    [ToolCall("scene", "activate_skill", {"name": "code", "type": "scene"})],
                    "tool_calls",
                ),
                ModelResponse("implemented", [], "model_finished"),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(tmp),
                provider=provider,
                use_storage=True,
            )
            agent.use_only_scenes("code")

            result = agent.run("Handle this repository task")

            self.assertEqual("implemented", result.text)
            self.assertEqual(
                {"scene:code", "prompt:code", "memory:default", "workflow:code"},
                set(result.skills),
            )
            activation_result = provider.last_messages[-1]["content"]
            self.assertIn("Repository coding chain", activation_result)

    def test_model_scene_action_obeys_agent_scene_scope(self) -> None:
        provider = MockProvider(
            tool_responses=[
                ModelResponse(
                    "",
                    [ToolCall("scene", "activate_skill", {"name": "common", "type": "scene"})],
                    "tool_calls",
                )
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(tmp),
                provider=provider,
                use_storage=True,
            )
            agent.use_only_scenes("code")

            with self.assertRaisesRegex(PermissionError, "outside.*allowed scenes"):
                agent.run("Choose a scene")

    def test_scene_with_storage_skill_fails_explicitly_when_stateless(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "scene:common.*storage"):
                Agent(
                    AgentConfig.create_default(tmp),
                    provider=MockProvider("unused"),
                    use_storage=False,
                ).run("hello", scene="common")

    def test_scene_rejects_nested_and_duplicate_workflow_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_scene(root, "nested", ["scene:common"])
            _write_scene(
                root,
                "duplicate",
                ["workflow:direct", "workflow:code"],
            )
            disclosure = create_progressive_skill_disclosure(
                AgentConfig.create_default(root)
            )
            disclosure.prepare_skill_index()

            with self.assertRaisesRegex(ValueError, "cannot include another scene"):
                read_scene_included_skills(
                    disclosure.open_skill("nested", expected_type="scene")
                )
            with self.assertRaisesRegex(ValueError, "only one workflow"):
                read_scene_included_skills(
                    disclosure.open_skill("duplicate", expected_type="scene")
                )


def _write_scene(root: Path, name: str, skills: list[str]) -> None:
    path = root / "skills" / "scene" / name
    path.mkdir(parents=True)
    path.joinpath("skill.toml").write_text(
        "\n".join(
            [
                'type = "scene"',
                f"description = {json.dumps(name + ' scene')}",
                "",
                "[configuration]",
                f"skills = {json.dumps(skills)}",
            ]
        ),
        encoding="utf-8",
    )

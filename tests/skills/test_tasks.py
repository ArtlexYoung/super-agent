import tempfile
import unittest
from dataclasses import replace

from core.config import AgentConfig
from core.provider.chat import MockProvider, ModelResponse, ToolCall
from core.skill_use.defaults import create_progressive_skill_disclosure
from core.skill_use.workflow import create_task_policy_from_skill
from super_agent import Agent


class TaskSkillTests(unittest.TestCase):
    def test_builtin_task_skills_combine_instructions_and_run_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            disclosure = create_progressive_skill_disclosure(
                AgentConfig.create_default(tmp)
            )
            disclosure.prepare_skill_index()

            common = disclosure.open_skill("common", expected_type="task")
            code = disclosure.open_skill("code", expected_type="task")

            self.assertEqual("direct", create_task_policy_from_skill(common).mode)
            self.assertEqual("loop", create_task_policy_from_skill(code).mode)
            self.assertIn("General task chain", common.read_instructions().content)
            self.assertIn("Repository coding chain", code.read_instructions().content)

    def test_explicit_task_skill_needs_no_storage_or_extra_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = MockProvider("finished")
            result = Agent(
                AgentConfig.create_default(tmp),
                provider=provider,
                use_storage=False,
            ).run("Summarize these notes", skill="common")

            self.assertEqual("finished", result.text)
            self.assertEqual("common", result.workflow)
            self.assertEqual(["task:common"], result.skills)
            self.assertEqual([], provider.tool_requests)
            self.assertIn("# General task chain", provider.last_messages[0]["content"])

    def test_model_can_activate_one_task_skill(self) -> None:
        provider = MockProvider(
            tool_responses=[
                ModelResponse(
                    "",
                    [ToolCall("task", "activate_skill", {"name": "code", "type": "task"})],
                    "tool_calls",
                ),
                ModelResponse("implemented", [], "model_finished"),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = Agent(
                AgentConfig.create_default(tmp),
                provider=provider,
                use_storage=False,
            ).run("Handle this repository task")

            self.assertEqual("implemented", result.text)
            self.assertIn("task:code", result.skills)
            self.assertIn("Repository coding chain", provider.last_messages[-1]["content"])

    def test_explicit_task_skill_rejects_another_task_skill(self) -> None:
        provider = MockProvider(
            tool_responses=[
                ModelResponse(
                    "",
                    [ToolCall("task", "activate_skill", {"name": "common", "type": "task"})],
                    "tool_calls",
                )
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(AgentConfig.create_default(tmp), provider=provider)

            with self.assertRaisesRegex(PermissionError, "outside this run"):
                agent.run("Use another task", skill="code")

    def test_explicit_task_skill_replaces_configured_task_short_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = AgentConfig.create_default(tmp)
            config = replace(config, agent=replace(config.agent, skills=["common"]))

            result = Agent(config, provider=MockProvider("coded")).run(
                "Code this",
                skill="code",
            )

            self.assertEqual(["task:code"], result.skills)
            self.assertEqual("code", result.workflow)


if __name__ == "__main__":
    unittest.main()

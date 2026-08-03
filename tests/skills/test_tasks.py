import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from adapter.cli_adapter import attach_code_config_to_agent
from core.config import CommonConfig
from core.provider.chat import MockProvider, ModelResponse, ToolCall
from core.skill_use.defaults import create_progressive_skill_disclosure
from core.skill_use.workflow import create_task_policy_from_skill
from super_agent import Agent


class TaskSkillTests(unittest.TestCase):
    def test_builtin_task_skills_combine_instructions_and_run_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            disclosure = create_progressive_skill_disclosure(
                CommonConfig.create_default(tmp)
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
                CommonConfig.create_default(tmp),
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
                CommonConfig.create_default(tmp),
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
            agent = Agent(CommonConfig.create_default(tmp), provider=provider)

            with self.assertRaisesRegex(PermissionError, "outside this run"):
                agent.run("Use another task", skill="code")

    def test_explicit_task_skill_replaces_configured_task_short_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = CommonConfig.create_default(tmp)
            config = replace(config, agent=replace(config.agent, skills=["common"]))

            result = Agent(config, provider=MockProvider("coded")).run(
                "Code this",
                skill="code",
            )

            self.assertEqual(["task:code"], result.skills)
            self.assertEqual("code", result.workflow)

    def test_code_task_lazily_adds_validated_workspace_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_config = root / "code.toml"
            code_config.write_text(
                """
schema_version = 1
kind = "code"

[workspace]
root = "workspace"
ignore = [".git", "build"]

[actions]
read = "allow"
write = "ask"
execute = "deny"

[verification]
commands = [["python3.11", "-m", "unittest"]]
""".strip(),
                encoding="utf-8",
            )
            provider = MockProvider("configured")
            agent = Agent(CommonConfig.create_default(root), provider=provider)
            attach_code_config_to_agent(agent, code_config)

            result = agent.run("Inspect this project", skill="code")

            system = provider.last_messages[0]["content"]
            self.assertEqual("configured", result.text)
            workspace = system.split("# Coding workspace", 1)[1]
            settings = json.loads(workspace.splitlines()[1])
            self.assertEqual(str(root / "workspace"), settings["root"])
            self.assertEqual([".git", "build"], settings["ignored_paths"])
            self.assertEqual("allow", settings["read"])
            self.assertEqual("ask", settings["write"])
            self.assertEqual("deny", settings["execute"])
            self.assertEqual(
                [["python3.11", "-m", "unittest"]],
                settings["verification_commands"],
            )
            self.assertIn("does not grant file or process authority", system)

    def test_invalid_code_config_does_not_affect_non_code_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_config = root / "code.toml"
            code_config.write_text('[workspace]\nroot = "."\n', encoding="utf-8")
            agent = Agent(CommonConfig.create_default(root), provider=MockProvider("ok"))
            attach_code_config_to_agent(agent, code_config)

            result = agent.run("Summarize this", skill="common")

            self.assertEqual("ok", result.text)
            with self.assertRaisesRegex(ValueError, "schema_version must be 1"):
                agent.run("Modify this", skill="code")


if __name__ == "__main__":
    unittest.main()

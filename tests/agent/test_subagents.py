import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from core.config import CommonConfig
from core.provider.chat import MockProvider, ModelResponse, ToolCall
from super_agent import Agent


class SubAgentTests(unittest.TestCase):
    def test_add_subagent_uses_explicit_name_or_clear_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main = _agent(Path(tmp), "main")
            first = main.add_subagent(_agent(Path(tmp), "coder"), name="coder")
            second = main.add_subagent(_agent(Path(tmp), "reviewer"))

            self.assertEqual("coder", first)
            self.assertEqual("subagent01", second)

    def test_add_subagent_rejects_duplicate_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = _agent(root, "main")
            main.add_subagent(_agent(root, "coder"), name="worker")

            with self.assertRaisesRegex(ValueError, "already exists: worker"):
                main.add_subagent(_agent(root, "reviewer"), name="worker")

    def test_model_delegates_only_through_the_subagent_action(self) -> None:
        provider = _delegate_provider("coder", "write the implementation", "main-final")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = _agent(root, "main", provider)
            coder = _agent(root, "coder", MockProvider("coder-result"))
            main.add_subagent(coder, name="coder", description="writes code")

            result = main.run("delegate coding")

            self.assertEqual("main-final", result.text)
            self.assertEqual(["coder"], [item.name for item in result.subagent_results])
            self.assertEqual("write the implementation", result.subagent_results[0].prompt)
            self.assertIn("coder-result", str(provider.last_messages))

    def test_model_that_returns_final_text_does_not_run_subagents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_provider = MockProvider("child")
            main = _agent(root, "main", MockProvider("main"))
            main.add_subagent(_agent(root, "child", child_provider), name="child")

            result = main.run("answer directly")

            self.assertEqual([], result.subagent_results)
            self.assertEqual([], child_provider.last_messages)

    def test_nested_subagents_finish_through_their_own_model_loops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = _agent(
                root,
                "main",
                _delegate_provider("coder", "implement and review", "main-final"),
            )
            coder = _agent(
                root,
                "coder",
                _delegate_provider("reviewer", "review implementation", "coder-final"),
            )
            reviewer = _agent(root, "reviewer", MockProvider("reviewed"))
            main.add_subagent(coder, name="coder", created_by_agent=True)
            coder.add_subagent(reviewer, name="reviewer", created_by_agent=True)

            result = main.run("build this")

            coder_result = result.subagent_results[0]
            reviewer_result = coder_result.subagent_results[0]
            self.assertEqual("coder-final", coder_result.text)
            self.assertEqual("reviewed", reviewer_result.text)
            self.assertTrue(coder_result.created_by_agent)
            self.assertTrue(reviewer_result.created_by_agent)

    def test_subagent_selects_its_own_task_skill(self) -> None:
        child_provider = MockProvider(
            tool_responses=[
                ModelResponse(
                    "",
                    [ToolCall("task", "activate_skill", {"name": "code", "type": "task"})],
                    "tool_calls",
                ),
                ModelResponse("coded", [], "model_finished"),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = _agent(
                root,
                "main",
                _delegate_provider("coder", "code this", "main-final"),
            )
            coder = _agent(root, "coder", child_provider)
            main.add_subagent(coder, name="coder")

            result = main.run("delegate")
            child_events = coder._create_event_store().read_run_events(
                result.subagent_results[0].run_id
            )
            completed = next(
                event for event in child_events if event.event_type == "task.completed"
            )

            self.assertIn("task:code", completed.data["skills"])

    def test_agent_chain_checks_warn_without_blocking_registration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = _agent(root, "main", max_depth=2)
            coder = _agent(root, "coder")
            reviewer = _agent(root, "reviewer")
            main.add_subagent(coder, name="coder")
            coder.add_subagent(reviewer, name="reviewer")
            reviewer.add_subagent(main, name="main")

            warnings = main._check_subagent_links()

            self.assertTrue(any("main -> coder -> reviewer -> main" in item for item in warnings))
            self.assertTrue(any("depth is 4 layers" in item for item in warnings))


def _delegate_provider(name: str, prompt: str, final: str) -> MockProvider:
    return MockProvider(
        tool_responses=[
            ModelResponse(
                "",
                [ToolCall("delegate", "run_subagent", {"name": name, "prompt": prompt})],
                "tool_calls",
            ),
            ModelResponse(final, [], "model_finished"),
        ]
    )


def _agent(
    root: Path,
    name: str,
    provider: MockProvider | None = None,
    *,
    max_depth: int | None = None,
) -> Agent:
    config = CommonConfig.create_default(root)
    settings = replace(
        config.agent,
        name=name,
        max_agent_chain_depth=max_depth,
    )
    return Agent(
        replace(config, agent=settings),
        provider=provider or MockProvider("ok"),
        use_storage=True,
    )

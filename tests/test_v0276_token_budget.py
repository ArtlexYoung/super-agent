import tempfile
import unittest
from pathlib import Path

from super_agent.core.context import AgentContext
from super_agent.core.event import RunIdentity
from super_agent.core.model import Message, ModelRequest, TokenBudget
from super_agent.core.provider import MockModel, ModelProfile, ModelRouter
from super_agent.core.run import RuntimeExtensionContext, RuntimeControl, collect_run, stream_run, RunInput
from super_agent.skill.agent_builder import TokenBudgetExtension
from super_agent.skill.library import AgentLibrary
from super_agent import Agent


class TokenBudgetTests(unittest.TestCase):
    def test_budget_reserves_output_before_declared_capacity_is_exceeded(self):
        budget = TokenBudget(reserve_output_tokens=4)
        self.assertTrue(budget.requires_rollover(7, 10))
        self.assertFalse(budget.requires_rollover(6, 10))
        self.assertFalse(budget.requires_rollover(10, None))

    def test_extension_raises_instead_of_repeating_an_impossible_rollover(self):
        extension = TokenBudgetExtension(TokenBudget(reserve_output_tokens=20))
        context = RuntimeExtensionContext(
            RunIdentity(),
            1,
            ModelRequest((Message("user", "too large"),)),
            estimated_input_tokens=1,
            context_window_tokens=10,
        )
        self.assertIsInstance(extension.before_model_call(context), RuntimeControl)
        with self.assertRaisesRegex(RuntimeError, "still cannot fit"):
            extension.before_model_call(context)

    def test_context_window_plugin_rolls_over_only_when_explicitly_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            builtin = Path(__file__).parents[1] / "src" / "super_agent" / "skill" / "builtin"
            library = AgentLibrary((builtin,))
            model = ModelRouter(
                (ModelProfile("small", MockModel("answer"), context_window_tokens=5_000),)
            )
            agent = Agent(model, name="budgeted")
            agent.use_agent_library(library)
            agent.set_token_budget(TokenBudget(reserve_output_tokens=1))
            agent.enable_plugin("plugin:super-agent/context-window")
            result = agent.run(
                "hello",
                context=AgentContext(messages=(Message("user", "x" * 6_000),)),
            )
            self.assertEqual("answer", result.text)
            self.assertGreaterEqual(len(result.windows), 2)

    def test_plain_runtime_does_not_apply_a_token_budget(self):
        model = ModelRouter(
            (ModelProfile("small", MockModel("answer"), context_window_tokens=10),)
        )
        result = collect_run(stream_run(RunInput("hello", model)))
        self.assertEqual(1, len(result.windows))


if __name__ == "__main__":
    unittest.main()

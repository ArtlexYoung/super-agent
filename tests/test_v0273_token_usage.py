import unittest

from super_agent.core.config import Config, ModelConfig, config_from_dict
from super_agent.core.model import Message, ModelEvent, ModelRequest, TokenUsage
from super_agent.core.provider import MockModel, ModelProfile, ModelRouter
from super_agent.core.run import RunInput, collect_run, stream_run


class TokenUsageTests(unittest.TestCase):
    def test_token_usage_normalizes_aliases_and_marks_estimates(self):
        usage = TokenUsage.from_mapping(
            {"prompt_tokens": 8, "completion_tokens": 3}, source="estimated"
        )
        self.assertEqual(
            {"input_tokens": 8, "output_tokens": 3},
            {key: usage.to_dict()[key] for key in ("input_tokens", "output_tokens")},
        )
        self.assertEqual(
            {"input_tokens": "estimated", "output_tokens": "estimated"},
            usage.to_dict()["token_sources"],
        )

    def test_run_result_keeps_provider_and_estimated_sources_separate(self):
        result = collect_run(
            stream_run(RunInput("hello", MockModel("answer")))
        )
        self.assertEqual("estimated", result.usage["token_sources"]["input_tokens"])
        self.assertEqual("estimated", result.usage["token_sources"]["output_tokens"])

    def test_model_capacity_is_visible_without_calling_the_model(self):
        profiles = (
            ModelProfile("large", MockModel("large"), context_window_tokens=32_000),
            ModelProfile("small", MockModel("small"), context_window_tokens=8_000),
        )
        router = ModelRouter(profiles)
        request = ModelRequest((Message("user", "choose"),))
        self.assertEqual(8_000, router.context_window_tokens_for(request))
        self.assertEqual(32_000, router.list_model_profiles()[0]["context_window_tokens"])

    def test_model_capacity_can_be_declared_in_toml_values(self):
        config = config_from_dict(
            {
                "models": [
                    {
                        "name": "local",
                        "provider": "mock",
                        "model": "answer",
                        "context_window_tokens": 16_000,
                    }
                ]
            }
        )
        self.assertEqual(16_000, config.models[0].context_window_tokens)


if __name__ == "__main__":
    unittest.main()

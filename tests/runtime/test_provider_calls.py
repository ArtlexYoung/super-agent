from __future__ import annotations

import unittest

from core.evaluation.review import parse_review_response

from core.provider.chat import (
    ActionTurn,
    FinalTurn,
    MockProvider,
    ModelResponse,
    ProviderCall,
    ToolCall,
    call_chat_model,
    read_model_turn,
)


class ProviderCallTests(unittest.TestCase):
    def test_review_response_has_a_strict_machine_contract(self) -> None:
        report = parse_review_response(
            '{"verdict":"changes_requested","findings":[{"severity":"major",'
            '"title":"Missing check","evidence":"No test result",'
            '"action":"Run the declared check"}],"checks":["schema"]}'
        )

        self.assertFalse(report.passed)
        self.assertEqual("major", report.findings[0].severity)
        with self.assertRaisesRegex(ValueError, "passing review cannot contain findings"):
            parse_review_response(
                '{"verdict":"pass","findings":[{"severity":"info",'
                '"title":"x","evidence":"y","action":"z"}],"checks":[]}'
            )
    def test_provider_text_becomes_one_final_turn(self) -> None:
        self.assertEqual(
            FinalTurn("finished"),
            read_model_turn(ModelResponse("finished", [], "completed")),
        )

    def test_provider_tool_calls_become_explicit_actions(self) -> None:
        turn = read_model_turn(
            ModelResponse(
                "I will inspect the Skill.",
                [ToolCall("call-1", "read_skill", {"skill_id": "prompt:common"})],
                "tool_calls",
            )
        )

        self.assertIsInstance(turn, ActionTurn)
        self.assertEqual("I will inspect the Skill.", turn.text)
        self.assertEqual("read_skill", turn.items[0].name)
        self.assertEqual({"skill_id": "prompt:common"}, turn.items[0].arguments)

    def test_empty_provider_turn_fails_instead_of_degrading(self) -> None:
        with self.assertRaisesRegex(ValueError, "neither final text nor actions"):
            read_model_turn(ModelResponse("", [], "completed"))

    def test_call_uses_only_provider_values(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        call = ProviderCall(
            profile_key="model:test",
            model="test",
            purpose="answer",
            messages=({"role": "user", "content": "hello"},),
            input_cost_per_million=0.1,
            output_cost_per_million=0.2,
            cache_creation_cost_per_million=0.3,
            cache_read_cost_per_million=0.4,
        )

        response = call_chat_model(
            call,
            MockProvider("finished"),
            lambda event_type, data: events.append((event_type, data)),
        )

        self.assertEqual("finished", response.text)
        self.assertEqual(
            ["model.call.selected", "model.call.completed"],
            [event_type for event_type, _data in events],
        )
        metrics = events[-1][1]
        self.assertEqual(0.3, metrics["pricing"]["cache_creation_cost_per_million"])
        self.assertTrue(metrics["estimated_cost_excludes_cache"])

    def test_provider_failure_is_recorded_and_raised_without_fallback(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []

        class FailingProvider:
            def send_chat_messages(self, messages: list[dict], model: str) -> str:
                raise ConnectionError("offline")

        with self.assertRaisesRegex(ConnectionError, "offline"):
            call_chat_model(
                ProviderCall(
                    "model:test",
                    "test",
                    "answer",
                    ({"role": "user", "content": "hello"},),
                ),
                FailingProvider(),  # type: ignore[arg-type]
                lambda event_type, data: events.append((event_type, data)),
            )

        self.assertEqual("model.call.failed", events[-1][0])
        self.assertNotIn("fallback", str(events).lower())


if __name__ == "__main__":
    unittest.main()

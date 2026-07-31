from __future__ import annotations

import unittest

from core.provider.chat import MockProvider, ModelResponse, ToolCall
from core.runtime import Actions, Final, ModelCall, Runtime, read_model_turn


class CoreRuntimeTests(unittest.TestCase):
    def test_provider_text_becomes_one_final_turn(self) -> None:
        self.assertEqual(
            Final("finished"),
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

        self.assertEqual(
            Actions(
                (
                    # Provider-specific calls stop at this normalized boundary.
                    turn.items[0],
                ),
                "I will inspect the Skill.",
            ),
            turn,
        )
        self.assertEqual("read_skill", turn.items[0].name)
        self.assertEqual({"skill_id": "prompt:common"}, turn.items[0].arguments)

    def test_empty_provider_turn_fails_instead_of_degrading(self) -> None:
        with self.assertRaisesRegex(ValueError, "neither final text nor actions"):
            read_model_turn(ModelResponse("", [], "completed"))

    def test_runtime_calls_provider_without_skill_or_state_objects(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        call = ModelCall(
            profile_key="model:test",
            model="test",
            purpose="answer",
            messages=({"role": "user", "content": "hello"},),
        )

        response = Runtime().call_model(
            call,
            MockProvider("finished"),
            lambda event_type, data: events.append((event_type, data)),
        )

        self.assertEqual("finished", response.text)
        self.assertEqual(
            ["model.call.selected", "model.call.completed"],
            [event_type for event_type, _data in events],
        )

    def test_provider_failure_is_recorded_and_raised_without_fallback(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []

        class FailingProvider:
            def send_chat_messages(self, messages: list[dict], model: str) -> str:
                raise ConnectionError("offline")

        with self.assertRaisesRegex(ConnectionError, "offline"):
            Runtime().call_model(
                ModelCall(
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

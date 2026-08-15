from __future__ import annotations

import hashlib
import json
import unittest

from skill.learning.run_learning import parse_review_response

from core.provider import (
    ActionTurn,
    FinalTurn,
    MockProvider,
    ModelPricing,
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
            pricing=ModelPricing(0.1, 0.2, 0.3, 0.4),
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

    def test_call_audits_ordered_input_lineage_without_message_content(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        messages = ({"role": "system", "content": "rules"}, {"role": "user", "content": "secret prompt"}, {"role": "assistant", "content": "work"}, {"role": "tool", "content": "private result"})
        tools = ({"type": "function", "function": {"name": "inspect", "parameters": {"type": "object"}}},)
        encoded = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        call = ProviderCall("model:test", "test", "answer", messages, tools, disclosure_references=("disclosure://skill/example/hash",), max_input_characters=len(encoded))

        call_chat_model(call, MockProvider("finished"), lambda event_type, data: events.append((event_type, data)))

        lineage = events[0][1]["input"]
        self.assertEqual(hashlib.sha256(encoded.encode()).hexdigest(), lineage["sha256"])
        self.assertEqual(len(encoded), lineage["characters"])
        self.assertEqual(len(encoded), lineage["limit_characters"])
        self.assertEqual("accepted", lineage["status"])
        self.assertFalse(lineage["truncated"])
        self.assertEqual([0, 1, 2, 3], [item["position"] for item in lineage["messages"]])
        self.assertEqual(["runtime", "user", "model", "tool"], [item["source"] for item in lineage["messages"]])
        self.assertEqual(["inspect"], lineage["tools"]["names"])
        self.assertEqual(["disclosure://skill/example/hash"], lineage["disclosure_references"])
        self.assertNotIn("secret prompt", str(lineage))
        self.assertNotIn("private result", str(lineage))

    def test_oversized_input_is_audited_and_rejected_before_provider_use(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        provider = MockProvider("must not run")
        messages = ({"role": "user", "content": "hello"},)
        encoded = json.dumps({"messages": messages, "tools": ()}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

        with self.assertRaisesRegex(ValueError, "max_model_input_characters"):
            call_chat_model(ProviderCall("model:test", "test", "answer", messages, max_input_characters=len(encoded) - 1), provider, lambda event_type, data: events.append((event_type, data)))

        self.assertEqual([], provider.last_messages)
        self.assertEqual(["model.call.rejected"], [event_type for event_type, _data in events])
        self.assertEqual("rejected", events[0][1]["input"]["status"])
        self.assertFalse(events[0][1]["input"]["truncated"])

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

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.config import CommonConfig
from core.provider import MockProvider, ModelResponse, ToolCall
from core.runtime.tasks.queue import AgentTaskQueue, AgentTaskQueueSettings
from super_agent import Agent


class MultiAgentTaskTests(unittest.TestCase):
    def test_deep_optimization_reuses_native_queues_at_two_agent_levels(self) -> None:
        batch_provider = MockProvider(
            tool_responses=[
                ModelResponse(
                    "",
                    [ToolCall("activate", "activate_skill", {
                        "name": "code-multi-deep-optimization",
                        "type": "task",
                    })],
                    "tool_calls",
                ),
                ModelResponse(
                    "",
                    [
                        _call("create-a", "create_agent_task", "try A", "experiment"),
                        _call("create-b", "create_agent_task", "try B", "experiment"),
                    ],
                    "tool_calls",
                ),
                ModelResponse(
                    "",
                    [
                        _call("dispatch-a", "dispatch_agent_task", "", "", "agent-task-01"),
                        _call("dispatch-b", "dispatch_agent_task", "", "", "agent-task-02"),
                    ],
                    "tool_calls",
                ),
                ModelResponse(
                    "",
                    [_wait_call("wait-experiments", "all_tasks_finished")],
                    "tool_calls",
                ),
                ModelResponse("batch evidence", [], "model_finished"),
            ]
        )
        main_provider = MockProvider(
            tool_responses=[
                ModelResponse(
                    "",
                    [_call(
                        "create-batch",
                        "create_agent_task",
                        "run a diverse measured batch",
                        "optimization-batch",
                    )],
                    "tool_calls",
                ),
                ModelResponse(
                    "",
                    [_call(
                        "dispatch-batch",
                        "dispatch_agent_task",
                        "",
                        "",
                        "agent-task-01",
                    )],
                    "tool_calls",
                ),
                ModelResponse(
                    "",
                    [_wait_call("wait-batch", "all_tasks_finished")],
                    "tool_calls",
                ),
                ModelResponse("global result", [], "model_finished"),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_lead = _agent(root, "batch-lead", batch_provider)
            batch_lead.add_subagent(
                _agent(root, "experiment-a", MockProvider("experiment A evidence")),
                name="experiment-a",
                purpose="experiment",
            )
            batch_lead.add_subagent(
                _agent(root, "experiment-b", MockProvider("experiment B evidence")),
                name="experiment-b",
                purpose="experiment",
            )
            main = _agent(root, "main", main_provider)
            main.add_subagent(
                batch_lead,
                name="batch-lead",
                purpose="optimization-batch",
            )

            result = main.run(
                "find the best measured implementation",
                skill="code-multi-deep-optimization",
            )

        batch = result.subagent_results[0]
        self.assertEqual("code-multi-deep-optimization", result.workflow)
        self.assertEqual("completed", result.agent_tasks[0]["status"])
        self.assertEqual("batch evidence", batch.text)
        self.assertEqual(
            {"experiment A evidence", "experiment B evidence"},
            {item.text for item in batch.subagent_results},
        )

    def test_model_can_activate_task_queue_during_a_run(self) -> None:
        provider = MockProvider(
            tool_responses=[
                ModelResponse(
                    "",
                    [ToolCall("activate", "activate_skill", {
                        "name": "common-multi-producer-consumer",
                        "type": "task",
                    })],
                    "tool_calls",
                ),
                ModelResponse(
                    "",
                    [_call("create", "create_agent_task", "write", "implementation")],
                    "tool_calls",
                ),
                ModelResponse(
                    "",
                    [_call("dispatch", "dispatch_agent_task", "", "", "agent-task-01")],
                    "tool_calls",
                ),
                ModelResponse(
                    "",
                    [_wait_call("wait", "all_tasks_finished")],
                    "tool_calls",
                ),
                ModelResponse("assembled", [], "model_finished"),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = _agent(root, "main", provider)
            main.add_subagent(
                _agent(root, "coder", MockProvider("coded")),
                name="coder",
                purpose="implementation",
            )

            result = main.run("coordinate the work")

        activated = next(
            message for message in provider.last_messages
            if message.get("name") == "activate_skill"
        )
        activation_result = json.loads(activated["content"])
        self.assertIn("create_agent_task", activation_result["tools"])
        self.assertIn("run_subagent", activation_result["removed_tools"])
        self.assertEqual("common-multi-producer-consumer", result.workflow)
        self.assertEqual("completed", result.agent_tasks[0]["status"])
        self.assertEqual("coded", result.subagent_results[0].text)

    def test_skill_dispatches_tasks_concurrently_and_waits_for_completion(self) -> None:
        provider = MockProvider(
            tool_responses=[
                ModelResponse(
                    "",
                    [
                        _call("create-1", "create_agent_task", "write", "implementation"),
                        _call("create-2", "create_agent_task", "review", "code-review"),
                    ],
                    "tool_calls",
                ),
                ModelResponse(
                    "",
                    [
                        _call("dispatch-1", "dispatch_agent_task", "", "", "agent-task-01"),
                        _call("dispatch-2", "dispatch_agent_task", "", "", "agent-task-02"),
                    ],
                    "tool_calls",
                ),
                ModelResponse(
                    "",
                    [_wait_call("wait-any", "any_task_completed")],
                    "tool_calls",
                ),
                ModelResponse(
                    "",
                    [_wait_call("wait-all", "all_tasks_finished")],
                    "tool_calls",
                ),
                ModelResponse("assembled", [], "model_finished"),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = _agent(root, "main", provider)
            main.add_subagent(_agent(root, "coder", MockProvider("coded")), name="coder", purpose="implementation")
            main.add_subagent(_agent(root, "reviewer", MockProvider("reviewed")), name="reviewer", purpose="code-review")

            result = main.run(
                "coordinate the work",
                skill="common-multi-producer-consumer",
            )

        self.assertEqual("assembled", result.text)
        self.assertEqual({"completed"}, {item["status"] for item in result.agent_tasks})
        self.assertEqual({"coder", "reviewer"}, {item["agent_name"] for item in result.agent_tasks})
        self.assertEqual({"coded", "reviewed"}, {item.text for item in result.subagent_results})
        names = {tool["function"]["name"] for tool in provider.tool_requests[0][1]}
        self.assertIn("wait_for_agent_tasks", names)
        self.assertNotIn("run_subagent", names)
        event_types = [event.event_type for event in result.events]
        self.assertIn("agent_task.dispatched", event_types)
        self.assertIn("agent_task.wait.started", event_types)
        self.assertIn("agent_task.wait.woke", event_types)
        self.assertEqual(
            list(range(1, len(result.events) + 1)),
            [event.sequence for event in result.events],
        )

    def test_contract_matching_routes_without_prompt_keywords(self) -> None:
        provider = MockProvider(
            tool_responses=[
                ModelResponse("", [_call("create", "create_agent_task", "review", "code-review")], "tool_calls"),
                ModelResponse("", [_call("dispatch", "dispatch_agent_task", "", "", "agent-task-01")], "tool_calls"),
                ModelResponse("", [_wait_call("wait", "all_tasks_finished")], "tool_calls"),
                ModelResponse("done", [], "model_finished"),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = _agent(root, "main", provider)
            main.add_subagent(_agent(root, "coder", MockProvider("wrong")), name="coder", purpose="implementation")
            main.add_subagent(_agent(root, "reviewer", MockProvider("right")), name="reviewer", purpose="code-review")
            result = main.run("coordinate", skill="common-multi-producer-consumer")

        self.assertEqual("reviewer", result.agent_tasks[0]["agent_name"])
        self.assertEqual("right", result.subagent_results[0].text)

    def test_rotate_selection_changes_agent_across_sequential_tasks(self) -> None:
        assignments = []
        events = []

        def consume(name: str, prompt: str, _record_options) -> dict[str, object]:
            assignments.append(name)
            return {"name": name, "text": prompt, "run_id": f"run-{prompt}"}

        queue = AgentTaskQueue(
            AgentTaskQueueSettings(
                max_tasks=4,
                max_wait_seconds=1,
                agent_selection="rotate",
            ),
            [
                {"name": "alpha", "purpose": "experiment", "required_features": ["text"]},
                {"name": "beta", "purpose": "experiment", "required_features": ["text"]},
            ],
            consume,
            lambda name, data: events.append((name, data)),
        )
        tools = {tool.name: tool for tool in queue.create_tools()}
        for number in range(1, 4):
            task_id = f"agent-task-{number:02d}"
            tools["create_agent_task"].handler({
                "prompt": f"experiment-{number}",
                "purpose": "experiment",
                "required_features": ["text"],
            })
            dispatched = tools["dispatch_agent_task"].handler({"task_id": task_id})
            self.assertEqual("skill_rotation", dispatched["selected_by"])
            self.assertEqual(2, dispatched["eligible_agent_count"])
            tools["wait_for_agent_tasks"].handler({
                "trigger": "selected_tasks_finished",
                "task_ids": [task_id],
                "max_wait_seconds": 1,
            })

        tools["create_agent_task"].handler({
            "prompt": "fixed",
            "purpose": "experiment",
            "required_features": ["text"],
        })
        with self.assertRaisesRegex(ValueError, "cannot be fixed"):
            tools["dispatch_agent_task"].handler({
                "task_id": "agent-task-04",
                "agent_name": "alpha",
            })
        queue.close()

        self.assertEqual(["alpha", "beta", "alpha"], assignments)
        dispatches = [data for name, data in events if name == "agent_task.dispatched"]
        self.assertEqual({"rotate"}, {item["agent_selection"] for item in dispatches})
        self.assertEqual({2}, {item["eligible_agent_count"] for item in dispatches})

    def test_weighted_cost_selection_prefers_high_weight_low_price_agent(self) -> None:
        assignments = []
        events = []

        def consume(name: str, prompt: str, _record_options) -> dict[str, object]:
            assignments.append(name)
            return {"name": name, "text": prompt, "run_id": "run"}

        queue = AgentTaskQueue(
            AgentTaskQueueSettings(max_wait_seconds=1),
            [
                _priced_agent("expensive", weight=1, input_price=8),
                _priced_agent("efficient", weight=2, input_price=0.5),
            ],
            consume,
            lambda name, data: events.append((name, data)),
        )
        tools = {tool.name: tool for tool in queue.create_tools()}
        with self.assertRaisesRegex(ValueError, "integer from 0"):
            tools["create_agent_task"].handler({
                "prompt": "invalid estimate",
                "purpose": "experiment",
                "required_features": ["text"],
                "estimated_output_tokens": True,
            })
        tools["create_agent_task"].handler({
            "prompt": "experiment",
            "purpose": "experiment",
            "required_features": ["text"],
        })
        dispatched = tools["dispatch_agent_task"].handler({"task_id": "agent-task-01"})
        tools["wait_for_agent_tasks"].handler({
            "trigger": "all_tasks_finished",
            "max_wait_seconds": 1,
        })
        queue.close()

        self.assertEqual(["efficient"], assignments)
        self.assertEqual("weighted_cost_reliability", dispatched["selected_by"])
        self.assertEqual(2.0, dispatched["weight"])
        self.assertEqual(0.5, dispatched["pricing"]["total_cost_per_million"])
        self.assertEqual(3, dispatched["cost_estimate"]["tokens"]["input_tokens"])
        self.assertTrue(dispatched["cost_estimate"]["excludes_unprovided_usage"])

    def test_estimated_output_tokens_choose_the_lower_expected_call_cost(self) -> None:
        assignments = []
        queue = AgentTaskQueue(
            AgentTaskQueueSettings(max_wait_seconds=1),
            [
                _priced_agent("cheap-input", input_price=0.1, output_price=8),
                _priced_agent("cheap-output", input_price=4, output_price=0.2),
            ],
            lambda name, prompt, _options: assignments.append(name) or {
                "name": name,
                "text": prompt,
                "run_id": "run",
            },
            lambda _name, _data: None,
        )
        tools = {tool.name: tool for tool in queue.create_tools()}
        tools["create_agent_task"].handler({
            "prompt": "experiment",
            "purpose": "experiment",
            "required_features": ["text"],
            "estimated_output_tokens": 1_000,
        })
        dispatched = tools["dispatch_agent_task"].handler({"task_id": "agent-task-01"})
        tools["wait_for_agent_tasks"].handler({
            "trigger": "all_tasks_finished",
            "max_wait_seconds": 1,
        })
        queue.close()

        self.assertEqual(["cheap-output"], assignments)
        self.assertEqual(1_000, dispatched["cost_estimate"]["tokens"]["output_tokens"])
        self.assertAlmostEqual(
            0.000212,
            dispatched["cost_estimate"]["estimated_cost"],
        )

    def test_recovered_agent_remains_ranked_by_run_reliability(self) -> None:
        calls = []

        def consume(name: str, prompt: str, _record_options) -> dict[str, object]:
            calls.append(name)
            if name == "primary" and calls.count(name) == 1:
                raise ConnectionError("provider offline")
            return {"name": name, "text": prompt, "run_id": f"run-{len(calls)}"}

        queue = AgentTaskQueue(
            AgentTaskQueueSettings(
                max_wait_seconds=1,
                circuit_breaker_wait_seconds=0.01,
            ),
            [_priced_agent("primary"), _priced_agent("fallback")],
            consume,
            lambda _name, _data: None,
        )
        tools = {tool.name: tool for tool in queue.create_tools()}
        _create_and_dispatch(tools, 1)
        tools["wait_for_agent_tasks"].handler({
            "trigger": "all_tasks_finished",
            "max_wait_seconds": 1,
        })
        time.sleep(0.03)
        second = _create_and_dispatch(tools, 2)
        tools["wait_for_agent_tasks"].handler({
            "trigger": "selected_tasks_finished",
            "task_ids": ["agent-task-02"],
            "max_wait_seconds": 1,
        })
        queue.close()

        self.assertEqual(["primary", "fallback", "fallback"], calls)
        self.assertEqual("fallback", second["agent_name"])
        self.assertEqual(1, second["successful_tasks"])
        self.assertEqual(1.0, second["reliability"])

    def test_unavailable_agent_opens_circuit_and_falls_back(self) -> None:
        calls = []
        events = []

        def consume(name: str, prompt: str, _record_options) -> dict[str, object]:
            calls.append(name)
            if name == "primary":
                raise ConnectionError("provider offline")
            return {"name": name, "text": prompt, "run_id": "fallback-run"}

        queue = AgentTaskQueue(
            AgentTaskQueueSettings(max_wait_seconds=1),
            [_priced_agent("primary", weight=2), _priced_agent("fallback", weight=1)],
            consume,
            lambda name, data: events.append((name, data)),
        )
        tools = {tool.name: tool for tool in queue.create_tools()}
        tools["create_agent_task"].handler({
            "prompt": "experiment",
            "purpose": "experiment",
            "required_features": ["text"],
        })
        tools["dispatch_agent_task"].handler({"task_id": "agent-task-01"})
        waited = tools["wait_for_agent_tasks"].handler({
            "trigger": "all_tasks_finished",
            "max_wait_seconds": 1,
        })
        queue.close()

        task = waited["tasks"][0]
        event_names = [name for name, _data in events]
        self.assertEqual(["primary", "fallback"], calls)
        self.assertEqual("completed", task["status"])
        self.assertEqual("fallback", task["agent_name"])
        self.assertEqual("primary", task["last_agent_name"])
        self.assertEqual(1, task["fallback_count"])
        self.assertIn("agent_task.circuit_opened", event_names)
        self.assertIn("agent_task.fallback_selected", event_names)
        opened = next(data for name, data in events if name == "agent_task.circuit_opened")
        self.assertEqual(0.5, opened["reliability"])

    def test_open_circuit_retries_half_open_and_closes_after_success(self) -> None:
        attempts = 0
        events = []

        def consume(name: str, prompt: str, _record_options) -> dict[str, object]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("provider timed out")
            return {"name": name, "text": prompt, "run_id": "recovered-run"}

        queue = AgentTaskQueue(
            AgentTaskQueueSettings(
                max_wait_seconds=1,
                circuit_breaker_wait_seconds=0.01,
            ),
            [_priced_agent("worker")],
            consume,
            lambda name, data: events.append((name, data)),
        )
        tools = {tool.name: tool for tool in queue.create_tools()}
        tools["create_agent_task"].handler({
            "prompt": "experiment",
            "purpose": "experiment",
            "required_features": ["text"],
        })
        tools["dispatch_agent_task"].handler({"task_id": "agent-task-01"})
        result = tools["wait_for_agent_tasks"].handler({
            "trigger": "all_tasks_finished",
            "max_wait_seconds": 1,
        })
        queue.close()

        event_names = [name for name, _data in events]
        self.assertEqual(2, attempts)
        self.assertEqual("completed", result["tasks"][0]["status"])
        self.assertEqual(2, result["tasks"][0]["attempt_count"])
        self.assertIn("agent_task.retry_scheduled", event_names)
        self.assertIn("agent_task.circuit_half_open", event_names)
        self.assertIn("agent_task.circuit_closed", event_names)

    def test_failed_half_open_probe_reopens_circuit_and_finishes_failed(self) -> None:
        attempts = 0
        events = []

        def consume(_name: str, _prompt: str, _record_options) -> dict[str, object]:
            nonlocal attempts
            attempts += 1
            raise ConnectionError("still offline")

        queue = AgentTaskQueue(
            AgentTaskQueueSettings(
                max_wait_seconds=1,
                circuit_breaker_wait_seconds=0.01,
            ),
            [_priced_agent("worker")],
            consume,
            lambda name, data: events.append((name, data)),
        )
        tools = {tool.name: tool for tool in queue.create_tools()}
        tools["create_agent_task"].handler({
            "prompt": "experiment",
            "purpose": "experiment",
            "required_features": ["text"],
        })
        tools["dispatch_agent_task"].handler({"task_id": "agent-task-01"})
        result = tools["wait_for_agent_tasks"].handler({
            "trigger": "all_tasks_finished",
            "max_wait_seconds": 1,
        })
        queue.close()

        event_names = [name for name, _data in events]
        self.assertEqual(2, attempts)
        self.assertEqual("failed", result["tasks"][0]["status"])
        self.assertEqual(2, event_names.count("agent_task.circuit_opened"))
        self.assertNotIn("agent_task.circuit_closed", event_names)

    def test_no_suitable_agent_is_a_visible_dispatch_failure(self) -> None:
        provider = MockProvider(
            tool_responses=[
                ModelResponse("", [_call("create", "create_agent_task", "audit", "security")], "tool_calls"),
                ModelResponse("", [_call("dispatch", "dispatch_agent_task", "", "", "agent-task-01")], "tool_calls"),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = _agent(root, "main", provider)
            main.add_subagent(_agent(root, "coder"), name="coder", purpose="implementation")
            with self.assertRaisesRegex(ValueError, "no suitable subagent"):
                main.run("coordinate", skill="common-multi-producer-consumer")

    def test_one_agent_consumes_its_queue_serially_and_wait_is_capped(self) -> None:
        active = 0
        peak = 0
        lock = threading.Lock()

        def consume(_name: str, prompt: str, _record_options) -> dict[str, object]:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return {"text": prompt, "run_id": prompt}

        events = []
        queue = AgentTaskQueue(
            AgentTaskQueueSettings(max_tasks=4, max_wait_seconds=0.01),
            [{"name": "worker", "purpose": "implementation", "required_features": ["text"]}],
            consume,
            lambda name, data: events.append((name, data)),
        )
        tools = {tool.name: tool for tool in queue.create_tools()}
        tools["create_agent_task"].handler({"prompt": "one", "purpose": "implementation", "required_features": ["text"]})
        tools["create_agent_task"].handler({"prompt": "two", "purpose": "implementation", "required_features": ["text"]})
        tools["dispatch_agent_task"].handler({"task_id": "agent-task-01"})
        tools["dispatch_agent_task"].handler({"task_id": "agent-task-02"})
        capped = tools["wait_for_agent_tasks"].handler({
            "trigger": "all_tasks_finished",
            "max_wait_seconds": 1,
        })
        queue.close()
        result = tools["list_agent_tasks"].handler({})

        self.assertEqual(1, peak)
        self.assertTrue(capped["wait_was_capped"])
        self.assertEqual("timeout", capped["reason"])
        self.assertEqual({"one", "two"}, {item["result"]["text"] for item in result["tasks"]})
        self.assertIn("agent_task.completed", [name for name, _data in events])

    def test_failed_and_cancelled_tasks_are_terminal_and_auditable(self) -> None:
        def fail(_name: str, _prompt: str) -> dict[str, object]:
            raise RuntimeError("child failed")

        events = []
        queue = AgentTaskQueue(
            AgentTaskQueueSettings(max_tasks=4),
            [{"name": "worker", "purpose": "implementation", "required_features": ["text"]}],
            fail,
            lambda name, data: events.append((name, data)),
        )
        tools = {tool.name: tool for tool in queue.create_tools()}
        tools["create_agent_task"].handler({"prompt": "fail", "purpose": "implementation", "required_features": ["text"]})
        tools["dispatch_agent_task"].handler({"task_id": "agent-task-01"})
        tools["create_agent_task"].handler({"prompt": "cancel", "purpose": "implementation", "required_features": ["text"]})
        tools["cancel_agent_task"].handler({"task_id": "agent-task-02"})
        result = tools["wait_for_agent_tasks"].handler({"trigger": "any_task_failed", "max_wait_seconds": 1})
        selected = tools["wait_for_agent_tasks"].handler({
            "trigger": "selected_tasks_finished",
            "task_ids": ["agent-task-01", "agent-task-02"],
            "max_wait_seconds": 1,
        })
        queue.close()

        self.assertEqual("any_task_failed", result["reason"])
        states = {item["task_id"]: item["status"] for item in result["tasks"]}
        self.assertEqual("failed", states["agent-task-01"])
        self.assertEqual("cancelled", states["agent-task-02"])
        self.assertEqual("selected_tasks_finished", selected["reason"])
        self.assertIn("agent_task.failed", [name for name, _data in events])
        self.assertIn("agent_task.cancelled", [name for name, _data in events])
        self.assertNotIn("agent_task.circuit_opened", [name for name, _data in events])


def _call(call_id: str, name: str, prompt: str, purpose: str, task_id: str | None = None) -> ToolCall:
    if name == "create_agent_task":
        arguments = {"prompt": prompt, "purpose": purpose, "required_features": ["text"]}
    else:
        arguments = {"task_id": task_id}
    return ToolCall(call_id, name, arguments)


def _wait_call(call_id: str, trigger: str) -> ToolCall:
    return ToolCall(call_id, "wait_for_agent_tasks", {"trigger": trigger, "max_wait_seconds": 1})


def _create_and_dispatch(tools, number: int) -> dict[str, object]:
    tools["create_agent_task"].handler({
        "prompt": f"experiment-{number}",
        "purpose": "experiment",
        "required_features": ["text"],
    })
    return tools["dispatch_agent_task"].handler({
        "task_id": f"agent-task-{number:02d}",
    })


def _priced_agent(
    name: str,
    *,
    weight: float = 1,
    input_price: float = 0,
    output_price: float = 0,
) -> dict[str, object]:
    return {
        "name": name,
        "purpose": "experiment",
        "required_features": ["text"],
        "weight": weight,
        "models": [{
            "model": f"{name}-model",
            "supports": ["text"],
            "purposes": ["experiment"],
            "input_cost_per_million": input_price,
            "output_cost_per_million": output_price,
            "total_cost_per_million": input_price + output_price,
        }],
    }


def _agent(root: Path, name: str, provider: MockProvider | None = None) -> Agent:
    config = CommonConfig.create_default(root)
    from dataclasses import replace

    return Agent(
        replace(config, agent=replace(config.agent, name=name)),
        provider=provider or MockProvider("ok"),
        use_storage=False,
    )


if __name__ == "__main__":
    unittest.main()

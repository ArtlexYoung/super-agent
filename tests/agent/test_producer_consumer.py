import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.config import CommonConfig
from core.provider.chat import MockProvider, ModelResponse, ToolCall
from core.skill_use.tasks import AgentTaskQueue, AgentTaskQueueSettings
from super_agent import Agent


class ProducerConsumerTests(unittest.TestCase):
    def test_model_can_activate_task_queue_during_a_run(self) -> None:
        provider = MockProvider(
            tool_responses=[
                ModelResponse(
                    "",
                    [ToolCall("activate", "activate_skill", {
                        "name": "producer-consumer",
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
        self.assertEqual("producer-consumer", result.workflow)
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

            result = main.run("coordinate the work", skill="producer-consumer")

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
            result = main.run("coordinate", skill="producer-consumer")

        self.assertEqual("reviewer", result.agent_tasks[0]["agent_name"])
        self.assertEqual("right", result.subagent_results[0].text)

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
                main.run("coordinate", skill="producer-consumer")

    def test_one_agent_consumes_its_queue_serially_and_wait_is_capped(self) -> None:
        active = 0
        peak = 0
        lock = threading.Lock()

        def consume(_name: str, prompt: str) -> dict[str, object]:
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


def _call(call_id: str, name: str, prompt: str, purpose: str, task_id: str | None = None) -> ToolCall:
    if name == "create_agent_task":
        arguments = {"prompt": prompt, "purpose": purpose, "required_features": ["text"]}
    else:
        arguments = {"task_id": task_id}
    return ToolCall(call_id, name, arguments)


def _wait_call(call_id: str, trigger: str) -> ToolCall:
    return ToolCall(call_id, "wait_for_agent_tasks", {"trigger": trigger, "max_wait_seconds": 1})


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

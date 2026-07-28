import json
import tempfile
import unittest
from pathlib import Path

from agents.agent import Agent
from runtime.config import AgentConfig
from runtime.safety import ActionRequest
from runtime.storage import StorageEventQuery
from runtime.store import create_local_runtime_store
from provider.chat import MockProvider
from skill.disclosure import ProgressiveDisclosureCore
from skill.kinds.memory import MiniMemory, create_memory_from_skill_disclosure
from support import write_memory_skill, write_workflow_skill


class MiniMemoryTests(unittest.TestCase):
    def test_memory_adds_structured_item_and_builds_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))

            item = memory.add_memory_item("Prefer short answers.", source_run_id="run-1")

            self.assertEqual("agent", item.scope)
            self.assertEqual("run-1", item.source_run_id)
            self.assertEqual([item], memory.list_memory_items())
            self.assertIn("Prefer short answers.", memory.build_prompt_instruction("short answers"))

    def test_recall_filters_scope_and_ranks_lexical_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))
            memory.add_memory_item("Python package release checklist.", scope="project")
            memory.add_memory_item("Garden watering schedule.", scope="project")
            memory.add_memory_item("Python preference for this user.", scope="agent")

            recalled = memory.recall_memory("python package", scope="project", limit=2)

            self.assertEqual(1, len(recalled))
            self.assertEqual("Python package release checklist.", recalled[0].text)
            self.assertTrue(all(item.scope == "project" for item in recalled))

    def test_memory_writes_events_and_forgetting_removes_active_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = _memory(root)
            item = memory.add_memory_item("Temporary fact.")

            memory.forget_memory(item.item_id)

            self.assertEqual([], memory.list_memory_items())
            events = memory.store.backend.read_events(
                StorageEventQuery(
                    user_id=memory.store.user_id,
                    agent_name=memory.store.agent_name,
                    stream_type="memory",
                )
            )
            self.assertEqual(
                ["memory.added", "memory.forgotten"],
                [event.event_type for event in events],
            )
            self.assertEqual(item.item_id, events[1].data["item_ids"][0])

    def test_consolidation_deterministically_merges_duplicate_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))
            memory.add_memory_item("Prefer concise answers.", source_run_id="run-1")
            memory.add_memory_item(" prefer   concise answers. ", source_run_id="run-2")
            memory.add_memory_item("Keep source links.", source_run_id="run-3")

            consolidated = memory.consolidate_memory()

            self.assertEqual(1, len(consolidated))
            self.assertEqual("Prefer concise answers.", consolidated[0].text)
            self.assertEqual(2, len(memory.list_memory_items()))
            self.assertEqual([], memory.consolidate_memory())

    def test_recall_uses_model_to_supersede_archive_and_forget_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = create_local_runtime_store(root)
            seed = MiniMemory(store)
            old = seed.add_memory_item("Python project uses version 3.11.", "project")
            current = seed.add_memory_item("Python project now uses version 3.12.", "project")
            temporary = seed.add_memory_item("Temporary Python migration note.", "project")
            wrong = seed.add_memory_item("Wrong Python project fact.", "project")
            action_requests: list[ActionRequest] = []

            def organize(messages):
                self.assertIn("source_item_ids", messages[0]["content"])
                return json.dumps(
                    {
                        "operations": [
                            {
                                "type": "supersede",
                                "source_item_ids": [old.item_id, current.item_id],
                                "text": "Python project uses version 3.12.",
                                "reason": "newer version wins",
                            },
                            {
                                "type": "archive",
                                "source_item_ids": [temporary.item_id],
                                "reason": "migration finished",
                            },
                            {
                                "type": "forget",
                                "source_item_ids": [wrong.item_id],
                                "reason": "known to be incorrect",
                            },
                        ]
                    }
                )

            def execute(request, action):
                action_requests.append(request)
                return action()

            memory = MiniMemory(
                store,
                send_text_model_messages=organize,
                execute_action=execute,
            )

            recalled = memory.recall_memory("Python project", "project")

            self.assertEqual(["Python project uses version 3.12."], [item.text for item in recalled])
            self.assertTrue(action_requests)
            self.assertTrue(all(request.actor == "agent:memory" for request in action_requests))
            event_types = [
                event.event_type
                for event in store.backend.read_events(
                    StorageEventQuery(
                        user_id=store.user_id,
                        agent_name=store.agent_name,
                        stream_type="memory",
                    )
                )
            ]
            self.assertIn("memory.superseded", event_types)
            self.assertIn("memory.archived", event_types)
            self.assertIn("memory.forgotten", event_types)
            self.assertEqual("memory.organization.completed", event_types[-1])

    def test_invalid_model_organization_keeps_recall_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = MiniMemory(
                create_local_runtime_store(Path(tmp)),
                send_text_model_messages=lambda messages: "not-json",
            )
            memory.add_memory_item("Python preference one.")
            memory.add_memory_item("Python preference two.")

            recalled = memory.recall_memory("Python")

            self.assertEqual(2, len(recalled))
            events = memory.store.backend.read_events(
                StorageEventQuery(
                    user_id=memory.store.user_id,
                    agent_name=memory.store.agent_name,
                    stream_type="memory",
                )
            )
            self.assertEqual("memory.organization.failed", events[-1].event_type)

    def test_memory_policy_is_loaded_from_memory_skill_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "skills" / "memory" / "project"
            skill_dir.mkdir(parents=True)
            (skill_dir / "skill.toml").write_text(
                """
schema_version = 2
name = "project"
capability = "memory"
description = "Project memory"
version = "0.1.0"
triggers = []

[configuration]
default_scope = "project"
recall_limit = 3
include_in_prompt = true
include_usage_habits = false
""".strip(),
                encoding="utf-8",
            )
            store = create_local_runtime_store(root / "state")
            disclosure = ProgressiveDisclosureCore([root / "skills"], store)
            disclosure.prepare_skill_index()
            memory = create_memory_from_skill_disclosure(
                disclosure.open_skill("project", "memory"),
                store,
            )

            self.assertEqual("project", memory.policy.default_scope)
            self.assertEqual(3, memory.policy.recall_limit)
            self.assertFalse(memory.policy.include_usage_habits)

    def test_agent_includes_recalled_memory_in_system_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            write_memory_skill(root)
            MiniMemory(
                create_local_runtime_store(root / ".super-agent", agent_name="demo")
            ).add_memory_item("User likes concise answers.")
            provider = MockProvider("ok")
            agent = _make_agent(root, provider)

            agent.run("Give a concise answer")

            self.assertIn("Memory", provider.last_messages[0]["content"])
            self.assertIn("User likes concise answers.", provider.last_messages[0]["content"])

    def test_agent_organizes_memory_before_main_model_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            write_memory_skill(root)
            seed = MiniMemory(
                create_local_runtime_store(root / ".super-agent", agent_name="demo")
            )
            first = seed.add_memory_item("Python project uses 3.11.")
            second = seed.add_memory_item("Python project now uses 3.12.")
            provider = _SequenceProvider(
                [
                    json.dumps(
                        {
                            "operations": [
                                {
                                    "type": "supersede",
                                    "source_item_ids": [first.item_id, second.item_id],
                                    "text": "Python project uses 3.12.",
                                    "reason": "newer project state",
                                }
                            ]
                        }
                    ),
                    "final answer",
                ]
            )
            agent = _make_agent(root, provider)

            result = agent.run("Which Python version does the project use?")

            self.assertEqual("final answer", result.text)
            self.assertEqual(2, len(provider.requests))
            active = MiniMemory(agent.runtime.create_store()).list_memory_items()
            self.assertEqual(["Python project uses 3.12."], [item.text for item in active])
            event_types = [
                event.event_type for event in agent.for_user("local").runs.read_trace(result.run_id).events
            ]
            self.assertIn("action.checked", event_types)

    def test_memory_self_updates_usage_habits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))

            memory.usage_habits.record_agent_run(workflow="direct", skills=["echo"])
            memory.usage_habits.record_agent_run(workflow="direct", skills=["echo"])

            habits = memory.usage_habits.read_usage_habits()
            self.assertEqual(2, habits["total_runs"])
            self.assertEqual(2, habits["workflows"]["direct"])
            self.assertEqual(2, habits["skills"]["echo"])
            self.assertIn("workflow direct used 2 times", memory.build_prompt_instruction())

    def test_agent_updates_usage_habits_after_successful_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            write_memory_skill(root)
            provider = MockProvider("ok")
            agent = _make_agent(root, provider)

            agent.run("first")
            second = agent.run("second")
            memory = MiniMemory(agent.runtime.create_store())

            self.assertEqual(2, memory.usage_habits.read_usage_habits()["total_runs"])
            self.assertIn("workflow direct used 1 times", provider.last_messages[0]["content"])
            checked_resources = [
                event.data["resource"]
                for event in agent.for_user("local").runs.read_trace(second.run_id).events
                if event.event_type == "action.checked"
            ]
            self.assertIn("memory:habits", checked_resources)


def _make_agent(root: Path, provider: MockProvider) -> Agent:
    config_path = root / "agent.toml"
    config_path.write_text(
        """
[agent]
name = "demo"
system = "Base system."
workflow = "direct"
memory = "default"
skills = []

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return Agent(AgentConfig.load_from_file(config_path), provider=provider)


def _memory(root: Path) -> MiniMemory:
    return MiniMemory(create_local_runtime_store(root))


class _SequenceProvider(MockProvider):
    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self.responses = list(responses)
        self.requests: list[list[dict[str, object]]] = []

    def send_chat_messages(self, messages, model):
        self.requests.append(messages)
        self.last_messages = messages
        if not self.responses:
            raise AssertionError("unexpected provider call")
        return self.responses.pop(0)

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from threading import Barrier

from super_agent import Agent
from core.config import AgentConfig
from core.models import RunIdentity
from core.checks import ActionRequest
from adapter.storage import JsonlStorage
from core.events import StorageEventQuery
from skill.state.events import EventStore, create_local_event_store
from core.provider.chat import MockProvider
from skill.disclosure import ProgressiveDisclosureCore
from skill.kinds.memory import (
    MemoryItem,
    MemoryTextModel,
    MiniMemory,
    create_memory_from_skill_disclosure,
)
from support import write_memory_skill, write_workflow_skill


class MiniMemoryTests(unittest.TestCase):
    def test_memory_adds_structured_item_and_builds_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))

            item = memory.add_long_term_memory(
                "Prefer short answers.",
                source_run_id="run-1",
            )

            self.assertEqual("agent", item.scope)
            self.assertEqual("long_term", item.memory_type)
            self.assertIsNone(item.conversation_id)
            self.assertEqual("run-1", item.source_run_id)
            self.assertEqual([item], memory.list_memory_items())
            self.assertIn("Prefer short answers.", memory.build_prompt_instruction("short answers"))

    def test_temporary_memory_requires_and_records_conversation_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))

            with self.assertRaisesRegex(ValueError, "current conversation"):
                memory.add_temporary_memory("Conversation detail.")

            item = memory.add_temporary_memory(
                "Conversation detail.",
                conversation_id="conversation-a",
            )

            self.assertEqual("temporary", item.memory_type)
            self.assertEqual("conversation-a", item.conversation_id)
            self.assertEqual(
                [item],
                memory.list_memory_items(
                    memory_type="temporary",
                    conversation_id="conversation-a",
                ),
            )

    def test_untyped_memory_stream_is_rejected_instead_of_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = create_local_event_store(Path(tmp))
            store.append_event(
                "memory",
                "memory",
                "memory.added",
                data={"item": {}},
            )

            with self.assertRaisesRegex(ValueError, "unknown memory stream"):
                MiniMemory(store).list_memory_items()

    def test_temporary_memory_cannot_cross_conversation_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = create_local_event_store(Path(tmp))
            seed = MiniMemory(store)
            alpha = seed.add_temporary_memory(
                "Alpha-only Python detail.",
                conversation_id="conversation-a",
            )
            beta = seed.add_temporary_memory(
                "Beta-only Python detail.",
                conversation_id="conversation-b",
            )
            shared = seed.add_long_term_memory("Shared Python preference.")
            memory = _runtime_memory(store, "conversation-a")

            listed = memory.list_memory_items()
            recalled = memory.recall_memory("Python")

            self.assertEqual(
                {alpha.item_id, shared.item_id},
                {item.item_id for item in listed},
            )
            self.assertEqual(
                {alpha.item_id, shared.item_id},
                {item.item_id for item in recalled},
            )
            self.assertNotIn(beta.item_id, {item.item_id for item in recalled})
            beta_memory = _runtime_memory(store, "conversation-b")
            self.assertEqual(
                {beta.item_id, shared.item_id},
                {item.item_id for item in beta_memory.list_memory_items()},
            )
            with self.assertRaisesRegex(PermissionError, "different conversation"):
                memory.list_memory_items(
                    memory_type="temporary",
                    conversation_id="conversation-b",
                )
            with self.assertRaisesRegex(KeyError, "current context"):
                memory.forget_memory(beta.item_id)

    def test_organization_prepares_each_memory_type_without_mixing_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = create_local_event_store(Path(tmp))
            seed = MiniMemory(store)
            seed.add_long_term_memory("Python preference one.")
            seed.add_long_term_memory("Python preference two.")
            seed.add_temporary_memory(
                "Python task detail one.",
                conversation_id="conversation-a",
            )
            seed.add_temporary_memory(
                "Python task detail two.",
                conversation_id="conversation-a",
            )
            seed.add_temporary_memory(
                "Python private detail from B.",
                conversation_id="conversation-b",
            )
            candidate_groups: list[list[dict[str, object]]] = []

            def organize(messages):
                payload = json.loads(messages[1]["content"])
                candidate_groups.append(payload["candidates"])
                return json.dumps({"operations": []})

            memory = _runtime_memory(store, "conversation-a", organize)

            recalled = memory.recall_memory("Python")

            self.assertEqual(4, len(recalled))
            self.assertEqual([], candidate_groups)
            long_term_plan = memory.prepare_memory_organization(
                "Python",
                memory_type="long_term",
            )
            temporary_plan = memory.prepare_memory_organization(
                "Python",
                memory_type="temporary",
            )

            self.assertIsNotNone(long_term_plan)
            self.assertIsNotNone(temporary_plan)
            self.assertEqual(2, len(candidate_groups))
            for candidates in candidate_groups:
                self.assertEqual(1, len({item["memory_type"] for item in candidates}))
                self.assertEqual(1, len({item["conversation_id"] for item in candidates}))
            self.assertNotIn(
                "Python private detail from B.",
                {item["text"] for group in candidate_groups for item in group},
            )

    def test_prompt_separates_long_term_and_current_conversation_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = create_local_event_store(Path(tmp))
            seed = MiniMemory(store)
            seed.add_long_term_memory("User prefers concise answers.")
            seed.add_temporary_memory(
                "Current task uses Python.",
                conversation_id="conversation-a",
            )
            seed.add_temporary_memory(
                "Another task uses Rust.",
                conversation_id="conversation-b",
            )

            prompt = _runtime_memory(store, "conversation-a").build_prompt_instruction()

            self.assertIn("Temporary memory for this conversation", prompt)
            self.assertIn("Long-term memory", prompt)
            self.assertIn("Current task uses Python.", prompt)
            self.assertIn("User prefers concise answers.", prompt)
            self.assertNotIn("Another task uses Rust.", prompt)

    def test_temporary_replacement_preserves_type_and_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = create_local_event_store(Path(tmp))
            seed = MiniMemory(store)
            old = seed.add_temporary_memory(
                "Python task uses version 3.11.",
                conversation_id="conversation-a",
            )
            current = seed.add_temporary_memory(
                "Python task now uses version 3.12.",
                conversation_id="conversation-a",
            )

            def organize(messages):
                return json.dumps(
                    {
                        "operations": [
                            {
                                "type": "supersede",
                                "source_item_ids": [old.item_id, current.item_id],
                                "text": "Python task uses version 3.12.",
                                "reason": "newer task state",
                            }
                        ]
                    }
                )

            memory = _runtime_memory(store, "conversation-a", organize)

            plan = memory.prepare_memory_organization(
                "Python task",
                memory_type="temporary",
            )
            self.assertIsNotNone(plan)
            self.assertEqual(
                2,
                len(
                    memory.recall_memory(
                        "Python task",
                        memory_type="temporary",
                    )
                ),
            )
            memory.apply_memory_organization(plan.plan_id)
            recalled = memory.recall_memory(
                "Python task",
                memory_type="temporary",
            )

            self.assertEqual(1, len(recalled))
            self.assertEqual("temporary", recalled[0].memory_type)
            self.assertEqual("conversation-a", recalled[0].conversation_id)

    def test_long_term_organization_can_promote_current_temporary_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, memory, source, payloads, action_requests = (
                _create_memory_promotion_scenario(Path(tmp))
            )

            plan = memory.prepare_memory_organization(
                "concise answers",
                memory_type="long_term",
            )

            self.assertIsNotNone(plan)
            self.assertEqual(1, len(payloads))
            self.assertEqual([], payloads[0]["candidates"])
            self.assertEqual(
                [source.item_id],
                payloads[0]["promotable_temporary_item_ids"],
            )
            self.assertEqual(
                [source.item_id],
                [item["item_id"] for item in payloads[0]["temporary_context"]],
            )
            self.assertEqual(
                [],
                memory.recall_memory(
                    "concise answers",
                    memory_type="long_term",
                ),
            )
            memory.apply_memory_organization(plan.plan_id)
            recalled = memory.recall_memory(
                "concise answers",
                memory_type="long_term",
            )
            self.assertEqual(
                ["User habitually prefers concise answers."],
                [item.text for item in recalled],
            )
            self.assertEqual("long_term", recalled[0].memory_type)
            self.assertIsNone(recalled[0].conversation_id)
            self.assertEqual(
                [source.item_id],
                [
                    item.item_id
                    for item in memory.list_memory_items(
                        memory_type="temporary",
                    )
                ],
            )
            promotion_actions = [
                request
                for request in action_requests
                if request.resource.startswith("memory:long_term:shared:")
            ]
            self.assertEqual(1, len(promotion_actions))
            self.assertEqual(("create",), tuple(promotion_actions[0].effects))

            events = store.read_events("memory", "long_term")
            promoted = next(
                event for event in events if event.event_type == "memory.promoted"
            )
            self.assertEqual([source.item_id], promoted.data["source_item_ids"])
            self.assertEqual(
                "conversation-a",
                promoted.data["source_conversation_id"],
            )

    def test_promotion_history_prevents_repromoting_a_temporary_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, memory, source, payloads, _ = _create_memory_promotion_scenario(
                Path(tmp)
            )
            plan = memory.prepare_memory_organization(
                "concise answers",
                memory_type="long_term",
            )
            self.assertIsNotNone(plan)
            memory.apply_memory_organization(plan.plan_id)
            recalled = memory.recall_memory(
                "concise answers",
                memory_type="long_term",
            )
            memory.forget_memory(recalled[0].item_id)
            second_plan = memory.prepare_memory_organization(
                "concise answers",
                memory_type="long_term",
            )
            self.assertIsNone(second_plan)
            self.assertEqual(1, len(payloads))
            self.assertEqual(
                [],
                memory.list_memory_items(memory_type="long_term"),
            )
            self.assertEqual(
                [source.item_id],
                [
                    item.item_id
                    for item in memory.list_memory_items(
                        memory_type="temporary",
                    )
                ],
            )

    def test_concurrent_identical_promotions_append_one_long_term_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = _PromotionBarrierStorage(root)
            store = EventStore(backend, root, "local", "super-agent")
            source = MiniMemory(store).add_temporary_memory(
                "The user repeatedly asks for concise answers.",
                conversation_id="conversation-a",
            )
            replacement = MemoryItem(
                item_id=f"memory-{'a' * 32}",
                text="User habitually prefers concise answers.",
                scope=source.scope,
                source_run_id="run-promotion",
                created_at="2026-07-28T00:00:00Z",
                memory_type="long_term",
                conversation_id=None,
            )

            def promote() -> None:
                store.memory.promote_temporary_memory_items_to_long_term(
                    [source.item_id],
                    "conversation-a",
                    asdict(replacement),
                    "stable response-style preference",
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(promote) for _ in range(2)]
                for future in futures:
                    future.result(timeout=10)

            promotion_events = backend.read_events(
                StorageEventQuery(
                    user_id=store.user_id,
                    agent_name=store.agent_name,
                    stream_type="memory",
                    stream_id="long_term",
                    event_type="memory.promoted",
                )
            )
            self.assertEqual(1, len(promotion_events))
            self.assertEqual(
                [replacement],
                MiniMemory(store).list_memory_items(memory_type="long_term"),
            )
            self.assertEqual(
                [source],
                MiniMemory(store).list_memory_items(
                    memory_type="temporary",
                    conversation_id="conversation-a",
                ),
            )

    def test_temporary_organization_cannot_promote_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = create_local_event_store(Path(tmp))
            seed = MiniMemory(store)
            first = seed.add_temporary_memory(
                "Concise answer preference one.",
                conversation_id="conversation-a",
            )
            seed.add_temporary_memory(
                "Concise answer preference two.",
                conversation_id="conversation-a",
            )

            def organize(messages):
                return json.dumps(
                    {
                        "operations": [
                            {
                                "type": "promote",
                                "source_item_ids": [first.item_id],
                                "text": "User prefers concise answers.",
                                "reason": "invalid temporary-side promotion",
                            }
                        ]
                    }
                )

            memory = _runtime_memory(store, "conversation-a", organize)

            with self.assertRaisesRegex(
                ValueError,
                "unknown temporary context|only long-term organization",
            ):
                memory.prepare_memory_organization(
                    "concise preference",
                    memory_type="temporary",
                )

            self.assertEqual(
                [],
                memory.list_memory_items(memory_type="long_term"),
            )

    def test_recall_filters_scope_and_ranks_lexical_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))
            memory.add_long_term_memory("Python package release checklist.", scope="project")
            memory.add_long_term_memory("Garden watering schedule.", scope="project")
            memory.add_long_term_memory("Python preference for this user.", scope="agent")

            recalled = memory.recall_memory("python package", scope="project", limit=2)

            self.assertEqual(1, len(recalled))
            self.assertEqual("Python package release checklist.", recalled[0].text)
            self.assertTrue(all(item.scope == "project" for item in recalled))

    def test_memory_writes_events_and_forgetting_removes_active_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = _memory(root)
            item = memory.add_long_term_memory("Disposable fact.")

            memory.forget_memory(item.item_id)

            self.assertEqual([], memory.list_memory_items())
            events = memory.store.read_events("memory")
            self.assertEqual(
                ["memory.added", "memory.forgotten"],
                [event.event_type for event in events],
            )
            self.assertEqual(item.item_id, events[1].data["item_ids"][0])

    def test_consolidation_deterministically_merges_duplicate_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))
            memory.add_long_term_memory("Prefer concise answers.", source_run_id="run-1")
            memory.add_long_term_memory(" prefer   concise answers. ", source_run_id="run-2")
            memory.add_long_term_memory("Keep source links.", source_run_id="run-3")

            consolidated = memory.consolidate_memory()

            self.assertEqual(1, len(consolidated))
            self.assertEqual("Prefer concise answers.", consolidated[0].text)
            self.assertEqual(2, len(memory.list_memory_items()))
            self.assertEqual([], memory.consolidate_memory())

    def test_consolidation_never_merges_temporary_and_long_term_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))
            for source_run_id in ("run-1", "run-2"):
                memory.add_long_term_memory(
                    "Prefer concise answers.",
                    source_run_id=source_run_id,
                )
                memory.add_temporary_memory(
                    "Prefer concise answers.",
                    source_run_id=source_run_id,
                    conversation_id="conversation-a",
                )

            consolidated = memory.consolidate_memory(
                conversation_id="conversation-a",
            )

            self.assertEqual(2, len(consolidated))
            self.assertEqual(
                {("long_term", None), ("temporary", "conversation-a")},
                {(item.memory_type, item.conversation_id) for item in consolidated},
            )
            self.assertEqual(
                2,
                len(memory.list_memory_items(conversation_id="conversation-a")),
            )

    def test_prepared_organization_requires_explicit_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = create_local_event_store(root)
            seed = MiniMemory(store)
            old = seed.add_long_term_memory("Python project uses version 3.11.", "project")
            current = seed.add_long_term_memory("Python project now uses version 3.12.", "project")
            temporary = seed.add_long_term_memory("Temporary Python migration note.", "project")
            wrong = seed.add_long_term_memory("Wrong Python project fact.", "project")
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

            before = memory.recall_memory("Python project", "project")
            plan = memory.prepare_memory_organization(
                "Python project",
                scope="project",
                memory_type="long_term",
            )

            self.assertIsNotNone(plan)
            self.assertEqual(4, len(before))
            self.assertEqual([], action_requests)
            self.assertEqual(4, len(memory.recall_memory("Python project", "project")))
            memory.apply_memory_organization(plan.plan_id)
            recalled = memory.recall_memory("Python project", "project")
            self.assertEqual(
                ["Python project uses version 3.12."],
                [item.text for item in recalled],
            )
            self.assertTrue(all(item.memory_type == "long_term" for item in recalled))
            self.assertTrue(all(item.conversation_id is None for item in recalled))
            self.assertTrue(action_requests)
            self.assertTrue(all(request.actor == "agent:memory" for request in action_requests))
            event_types = [
                event.event_type
                for event in store.read_events("memory")
            ]
            self.assertIn("memory.superseded", event_types)
            self.assertIn("memory.archived", event_types)
            self.assertIn("memory.forgotten", event_types)
            self.assertEqual("memory.organization.applied", event_types[-1])

    def test_applying_a_stale_organization_plan_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = create_local_event_store(Path(tmp))
            seed = MiniMemory(store)
            old = seed.add_long_term_memory("Python project uses version 3.11.")
            current = seed.add_long_term_memory(
                "Python project now uses version 3.12."
            )

            def organize(messages):
                return json.dumps(
                    {
                        "operations": [
                            {
                                "type": "supersede",
                                "source_item_ids": [old.item_id, current.item_id],
                                "text": "Python project uses version 3.12.",
                                "reason": "newer version wins",
                            }
                        ]
                    }
                )

            memory = MiniMemory(store, send_text_model_messages=organize)
            plan = memory.prepare_memory_organization(
                "Python project",
                memory_type="long_term",
            )
            self.assertIsNotNone(plan)
            memory.forget_memory(old.item_id)

            with self.assertRaisesRegex(RuntimeError, "candidates are stale"):
                memory.apply_memory_organization(plan.plan_id)

            self.assertEqual(
                [current],
                memory.list_memory_items(memory_type="long_term"),
            )

    def test_invalid_organization_plan_fails_without_changing_recall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = MiniMemory(
                create_local_event_store(Path(tmp)),
                send_text_model_messages=lambda messages: "not-json",
            )
            memory.add_long_term_memory("Python preference one.")
            memory.add_long_term_memory("Python preference two.")

            self.assertEqual(2, len(memory.recall_memory("Python")))
            with self.assertRaisesRegex(ValueError, "valid JSON"):
                memory.prepare_memory_organization(
                    "Python",
                    memory_type="long_term",
                )
            self.assertEqual(2, len(memory.recall_memory("Python")))

            events = memory.store.read_events("memory")
            self.assertEqual("memory.organization.failed", events[-1].event_type)

    def test_memory_policy_is_loaded_from_memory_skill_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "skills" / "memory" / "project"
            skill_dir.mkdir(parents=True)
            (skill_dir / "skill.toml").write_text(
                """
schema_version = 3
name = "project"
type = "memory"
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
            store = create_local_event_store(root / "state")
            disclosure = ProgressiveDisclosureCore([root / "skills"])
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
                create_local_event_store(root / ".super-agent", agent_name="demo")
            ).add_long_term_memory("User likes concise answers.")
            provider = MockProvider("ok")
            agent = _make_agent(root, provider)

            agent.run("Give a concise answer")

            self.assertIn("Long-term memory", provider.last_messages[0]["content"])
            self.assertIn("User likes concise answers.", provider.last_messages[0]["content"])

    def test_agent_recall_does_not_organize_memory_implicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            write_memory_skill(root)
            seed = MiniMemory(
                create_local_event_store(root / ".super-agent", agent_name="demo")
            )
            first = seed.add_long_term_memory("Python project uses 3.11.")
            second = seed.add_long_term_memory("Python project now uses 3.12.")
            provider = MockProvider("final answer")
            agent = _make_agent(root, provider)

            result = agent.run("Which Python version does the project use?")

            self.assertEqual("final answer", result.text)
            active = MiniMemory(agent.runtime.create_event_store()).list_memory_items()
            self.assertEqual(
                {first.item_id, second.item_id},
                {item.item_id for item in active},
            )
            event_types = [
                event.event_type for event in agent.for_user("local").runs.read_trace(result.run_id).events
            ]
            self.assertNotIn("memory.organization.preparing", event_types)

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
            memory = MiniMemory(agent.runtime.create_event_store())

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
skills = ["memory:default"]

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return Agent(
        AgentConfig.load_from_file(config_path),
        provider=provider,
        use_storage=True,
    )


def _memory(root: Path) -> MiniMemory:
    return MiniMemory(create_local_event_store(root))


def _create_memory_promotion_scenario(
    root: Path,
) -> tuple[
    EventStore,
    MiniMemory,
    MemoryItem,
    list[dict[str, object]],
    list[ActionRequest],
]:
    store = create_local_event_store(root)
    seed = MiniMemory(store)
    source = seed.add_temporary_memory(
        "The user repeatedly asks for concise answers.",
        conversation_id="conversation-a",
    )
    seed.add_temporary_memory(
        "The user asks for detailed answers in this other task.",
        conversation_id="conversation-b",
    )
    payloads: list[dict[str, object]] = []
    action_requests: list[ActionRequest] = []

    def organize(messages):
        payload = json.loads(messages[1]["content"])
        payloads.append(payload)
        source_id = payload["promotable_temporary_item_ids"][0]
        return json.dumps(
            {
                "operations": [
                    {
                        "type": "promote",
                        "source_item_ids": [source_id],
                        "text": "User habitually prefers concise answers.",
                        "reason": "stable response-style preference",
                    }
                ]
            }
        )

    def execute(request, action):
        action_requests.append(request)
        return action()

    memory = _runtime_memory(
        store,
        "conversation-a",
        organize,
        execute=execute,
    )
    return store, memory, source, payloads, action_requests


def _runtime_memory(
    store,
    conversation_id: str,
    organize: MemoryTextModel | None = None,
    execute=None,
) -> MiniMemory:
    identity = RunIdentity.create(
        store.user_id,
        store.agent_name,
        conversation_id=conversation_id,
    )
    return MiniMemory(
        store,
        identity,
        send_text_model_messages=organize,
        execute_action=execute or (lambda request, action: action()),
    )


class _PromotionBarrierStorage:
    name = "jsonl"

    def __init__(self, root: Path) -> None:
        self._storage = JsonlStorage(root)
        self._promotion_barrier = Barrier(2)

    def append_event(self, **arguments):
        if arguments.get("event_type") == "memory.promoted":
            self._promotion_barrier.wait(timeout=5)
        return self._storage.append_event(**arguments)

    def read_events(self, query):
        return self._storage.read_events(query)

    def delete_events(self, query):
        return self._storage.delete_events(query)

import tempfile
import unittest
from pathlib import Path

from core.checks import ActionRequest
from core.models import RunIdentity
from skill.disclosure import ProgressiveDisclosureCore
from skill.state.events import create_local_event_store
from skill.state.memory import Memory, MemorySettings, create_memory_from_skill


class MemoryTests(unittest.TestCase):
    def test_remembered_item_is_durable_and_has_no_conversation_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))

            item = memory.remember_long_term(
                "Prefer short answers.",
                source_run_id="run-1",
            )

            self.assertEqual("agent", item.scope)
            self.assertEqual("run-1", item.source_run_id)
            self.assertEqual([item], memory.list_long_term())
            self.assertNotIn("memory_type", item.__dataclass_fields__)
            self.assertNotIn("conversation_id", item.__dataclass_fields__)
            self.assertIn(
                "Prefer short answers.",
                memory.build_prompt_instruction("short answers"),
            )

    def test_conversation_messages_are_not_written_to_memory_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = create_local_event_store(Path(tmp))
            identity = RunIdentity.create(
                "local",
                "super-agent",
                conversation_id="conversation-a",
            )
            memory = Memory(
                store,
                identity,
                execute_action=lambda request, action: action(),
            )

            self.assertEqual([], memory.list_long_term())
            self.assertEqual([], store.read_events("memory"))

    def test_old_temporary_stream_is_rejected_instead_of_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = create_local_event_store(Path(tmp))
            store.append_event(
                "memory",
                "temporary:conversation-a",
                "memory.added",
                data={"item": {}},
            )

            with self.assertRaisesRegex(ValueError, "unknown memory streams"):
                Memory(store).list_long_term()

    def test_recall_filters_scope_and_ranks_text_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))
            memory.remember_long_term("Python package release checklist.", "project")
            memory.remember_long_term("Garden watering schedule.", "project")
            memory.remember_long_term("Python preference for this user.", "agent")

            recalled = memory.recall_long_term(
                "python package",
                "project",
                2,
            )

            self.assertEqual(
                ["Python package release checklist."],
                [item.text for item in recalled],
            )

    def test_forget_is_explicit_and_removes_the_active_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))
            item = memory.remember_long_term("Disposable fact.")

            memory.forget_long_term(item.item_id, "no longer useful")

            self.assertEqual([], memory.list_long_term())
            events = memory.store.read_events("memory")
            self.assertEqual(
                ["memory.remembered", "memory.forgotten"],
                [event.event_type for event in events],
            )
            self.assertEqual("no longer useful", events[-1].data["reason"])

    def test_organization_applies_multiple_changes_in_one_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))
            first = memory.remember_long_term("Prefer concise answers.")
            duplicate = memory.remember_long_term("Answers should be concise.")
            wrong = memory.remember_long_term("User prefers long answers.")
            event_count = len(memory.store.read_events("memory"))

            replacements = memory.organize_long_term(
                [
                    {
                        "operation": "merge",
                        "item_ids": [first.item_id, duplicate.item_id],
                        "text": "User prefers concise answers.",
                        "reason": "same stable preference",
                    },
                    {
                        "operation": "forget",
                        "item_ids": [wrong.item_id],
                        "reason": "contradicted by current conversation",
                    },
                ]
            )

            self.assertEqual(
                ["User prefers concise answers."],
                [item.text for item in replacements],
            )
            self.assertEqual(replacements, memory.list_long_term())
            events = memory.store.read_events("memory")
            self.assertEqual(event_count + 1, len(events))
            self.assertEqual("memory.organized", events[-1].event_type)

    def test_invalid_organization_does_not_write_a_memory_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))
            item = memory.remember_long_term("Keep this fact.")
            before = memory.store.read_events("memory")

            with self.assertRaisesRegex(ValueError, "at least two"):
                memory.organize_long_term(
                    [
                        {
                            "operation": "merge",
                            "item_ids": [item.item_id],
                            "text": "Invalid merge.",
                        }
                    ]
                )

            self.assertEqual(before, memory.store.read_events("memory"))
            self.assertEqual([item], memory.list_long_term())

    def test_organization_cannot_reuse_items_or_cross_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))
            agent_item = memory.remember_long_term("Agent preference.", "agent")
            project_item = memory.remember_long_term("Project convention.", "project")

            with self.assertRaisesRegex(ValueError, "combine scopes"):
                memory.organize_long_term(
                    [
                        {
                            "operation": "merge",
                            "item_ids": [agent_item.item_id, project_item.item_id],
                            "text": "Invalid combined memory.",
                        }
                    ]
                )

            with self.assertRaisesRegex(ValueError, "reuse"):
                memory.organize_long_term(
                    [
                        {
                            "operation": "replace",
                            "item_ids": [agent_item.item_id],
                            "text": "Updated preference.",
                        },
                        {
                            "operation": "forget",
                            "item_ids": [agent_item.item_id],
                        },
                    ]
                )

    def test_runtime_changes_pass_through_the_action_checker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = create_local_event_store(Path(tmp))
            requests: list[ActionRequest] = []

            def execute(request, action):
                requests.append(request)
                return action()

            memory = Memory(
                store,
                RunIdentity.create("local", "super-agent"),
                execute_action=execute,
            )
            item = memory.remember_long_term("Checked memory.")
            memory.forget_long_term(item.item_id)

            self.assertEqual(2, len(requests))
            self.assertEqual("agent:memory", requests[0].actor)
            self.assertEqual(("create",), tuple(requests[0].effects))
            self.assertEqual(("delete",), tuple(requests[1].effects))

    def test_memory_skill_settings_and_instructions_are_loaded_centrally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skills" / "memory" / "project"
            skill.mkdir(parents=True)
            (skill / "skill.toml").write_text(
                """
schema_version = 3
name = "project"
type = "memory"
description = "Project memory"
version = "1.0.0"

[entry]
instructions = "SKILL.md"

[configuration]
default_scope = "project"
recall_limit = 7
include_in_prompt = true
include_usage_habits = false
""".strip(),
                encoding="utf-8",
            )
            (skill / "SKILL.md").write_text(
                "Persist only durable project knowledge.",
                encoding="utf-8",
            )
            disclosure = ProgressiveDisclosureCore([root / "skills"])
            disclosure.prepare_skill_index()

            memory = create_memory_from_skill(
                disclosure.open_skill("project", "memory"),
                create_local_event_store(root / "state"),
            )

            self.assertEqual("project", memory.settings.default_scope)
            self.assertEqual(7, memory.settings.recall_limit)
            self.assertIn(
                "Persist only durable project knowledge.",
                memory.build_prompt_instruction(),
            )

    def test_usage_habits_remain_separate_from_memory_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _memory(Path(tmp))

            memory.usage_habits.record_agent_run("model-loop", ["memory:default"])

            self.assertEqual([], memory.list_long_term())
            prompt = memory.usage_habits.build_prompt_instruction()
            self.assertIn("total runs: 1", prompt)
            self.assertIn("memory:default", prompt)


def _memory(root: Path) -> Memory:
    return Memory(
        create_local_event_store(root),
        settings=MemorySettings(instructions="Remember durable knowledge only."),
    )

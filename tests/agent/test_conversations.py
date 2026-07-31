import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from super_agent import Agent
from core.provider.chat import MockProvider, ModelResponse, ToolCall
from core.config import AgentConfig
from adapter.storage import JsonlStorage
from adapter.conversations import (
    append_conversation_turn,
    read_conversation,
)
from core.state.memory import Memory
from core.evolution.records import read_evaluation_records
from support import RecordingProvider, SequenceProvider


class ConversationRuntimeTests(unittest.TestCase):
    def test_conversation_management_replays_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(tmp), provider=MockProvider(), use_storage=True
            )
            conversations = agent.for_user("local").conversations
            conversation = conversations.create("Original")
            store = agent.runtime.create_event_store()

            append_conversation_turn(
                store,
                conversation.conversation_id,
                "Remember this",
                "Remembered",
                run_id="run-1",
                run_result={"run_id": "run-1"},
            )
            renamed = conversations.rename(conversation.conversation_id, "Renamed")
            cleared = conversations.clear(conversation.conversation_id)

            self.assertEqual("Renamed", renamed.title)
            self.assertEqual([], cleared.messages)
            self.assertEqual(
                [conversation.conversation_id],
                [item.conversation_id for item in conversations.list()],
            )
            conversations.delete(conversation.conversation_id)
            self.assertEqual([], conversations.list())

    def test_second_turn_loads_history_from_runtime_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = SequenceProvider(["first answer", "second answer"])
            agent = Agent(
                AgentConfig.create_default(tmp), provider=provider, use_storage=True
            )
            user = agent.for_user("local")
            conversation = user.conversations.create()

            user.run("first question", conversation_id=conversation.conversation_id)
            user.run("second question", conversation_id=conversation.conversation_id)

            self.assertEqual(
                ["system", "user", "assistant", "user"],
                [message["role"] for message in provider.calls[1]],
            )
            self.assertEqual("first question", provider.calls[1][1]["content"])
            self.assertEqual("first answer", provider.calls[1][2]["content"])
            stored = user.conversations.read(conversation.conversation_id)
            self.assertEqual(
                ["first question", "first answer", "second question", "second answer"],
                [message.content for message in stored.messages],
            )
            self.assertTrue(stored.messages[-1].run_result)

    def test_explicit_messages_cannot_compete_with_stored_conversation_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(tmp), provider=MockProvider(), use_storage=True
            )
            user = agent.for_user("local")
            conversation = user.conversations.create()

            with self.assertRaisesRegex(ValueError, "cannot be combined"):
                user.run(
                    "hello",
                    conversation_id=conversation.conversation_id,
                    messages=[{"role": "user", "content": "duplicate history"}],
                )

    def test_failed_run_does_not_append_a_partial_conversation_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                AgentConfig.create_default(tmp),
                provider=RecordingProvider(RuntimeError("provider failed")),
                use_storage=True,
            )
            user = agent.for_user("local")
            conversation = user.conversations.create("Failure test")

            with self.assertRaisesRegex(RuntimeError, "provider failed"):
                user.run("do not persist this", conversation_id=conversation.conversation_id)

            stored = user.conversations.read(conversation.conversation_id)
            self.assertEqual([], stored.messages)

    def test_user_scopes_isolate_conversations_memory_evaluations_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = AgentConfig.create_default(tmp)
            config = replace(
                config,
                agent=replace(config.agent, skills=["prompt:common"]),
            )
            agent = Agent(
                config,
                provider=MockProvider("ok"),
                use_storage=True,
            )
            conversation_id = "shared-conversation-id"

            alpha = agent.for_user("user-alpha")
            beta = agent.for_user("user-beta")
            alpha_result = alpha.run("alpha secret", conversation_id=conversation_id)
            beta_result = beta.run("beta secret", conversation_id=conversation_id)
            alpha.runs.learn(alpha_result.run_id)
            beta.runs.learn(beta_result.run_id)
            alpha_store = agent.runtime.create_event_store("user-alpha")
            beta_store = agent.runtime.create_event_store("user-beta")
            Memory(alpha_store).remember_long_term("alpha memory")

            self.assertEqual(
                ["alpha secret", "ok"],
                [
                    item.content
                    for item in read_conversation(alpha_store, conversation_id).messages
                ],
            )
            self.assertEqual(
                ["beta secret", "ok"],
                [
                    item.content
                    for item in read_conversation(beta_store, conversation_id).messages
                ],
            )
            self.assertEqual([], Memory(beta_store).list_long_term())
            self.assertTrue(read_evaluation_records(alpha_store))
            self.assertTrue(read_evaluation_records(beta_store))
            self.assertNotEqual(
                alpha_store.disclosure.cache_root,
                beta_store.disclosure.cache_root,
            )

    def test_subagent_inherits_identity_without_duplicating_main_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = JsonlStorage(root / ".super-agent")
            main = _named_agent(
                root,
                "main",
                "main answer",
                storage,
                provider=MockProvider(
                    tool_responses=[
                        ModelResponse(
                            "",
                            [
                                ToolCall(
                                    "delegate",
                                    "run_subagent",
                                    {"name": "worker", "prompt": "delegate this"},
                                )
                            ],
                            "tool_calls",
                        ),
                        ModelResponse("main answer", [], "model_finished"),
                    ]
                ),
            )
            worker = _named_agent(root, "worker", "worker answer", storage)
            main.add_subagent(worker, name="worker")
            main_user = main.for_user("user-a")
            conversation = main_user.conversations.create()

            result = main_user.run(
                "delegate this",
                conversation_id=conversation.conversation_id,
            )

            stored = main_user.conversations.read(conversation.conversation_id)
            child_run = worker.runtime.create_event_store("user-a").read_run(
                result.subagent_results[0].run_id
            )
            self.assertEqual(2, len(stored.messages))
            self.assertEqual([], worker.for_user("user-a").conversations.list())
            self.assertEqual(conversation.conversation_id, child_run.conversation_id)
            self.assertEqual("user-a", child_run.user_id)
            self.assertEqual(result.run_id, child_run.parent_run_id)

    def test_new_agent_instance_reads_existing_conversations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AgentConfig.create_default(root)
            first = Agent(
                config, provider=MockProvider("persisted answer"), use_storage=True
            )
            first_user = first.for_user("local")
            conversation = first_user.conversations.create()
            first_user.run("persist this", conversation_id=conversation.conversation_id)

            second = Agent(config, provider=MockProvider(), use_storage=True)

            loaded = second.for_user("local").conversations.read(
                conversation.conversation_id
            )
            self.assertEqual(
                ["persist this", "persisted answer"],
                [item.content for item in loaded.messages],
            )

    def test_skill_evolution_workspaces_are_isolated_by_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            response = json.dumps(
                {
                    "write_files": {
                        "skill.toml": "\n".join(
                            [
                                "schema_version = 3",
                                'name = "private-note"',
                                'type = "prompt"',
                                'description = "Private notes"',
                                'version = "0.1.0"',
                                "agent_created = true",
                                "agent_can_update = true",
                                "",
                                "[entry]",
                                'instructions = "SKILL.md"',
                            ]
                        ),
                        "SKILL.md": "Write private notes.",
                    },
                    "delete_files": [],
                }
            )
            agent = Agent(
                AgentConfig.create_default(tmp),
                provider=MockProvider(response),
                use_storage=True,
            )
            alpha = agent.for_user("alpha").skills.create_evolution_manager()
            beta = agent.for_user("beta").skills.create_evolution_manager()

            candidate = alpha.create_skill_candidate("private-note", "write private notes")

            self.assertNotEqual(alpha.evolution_root, beta.evolution_root)
            self.assertTrue(candidate.skill_path.is_dir())
            with self.assertRaisesRegex(KeyError, "skill candidate not found"):
                beta.evaluate_skill_candidate(candidate.candidate_id, [])

    def test_sqlite_backend_replays_conversations_across_agent_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = AgentConfig.create_default(tmp)
            config = replace(
                config,
                storage=replace(config.storage, backend="sqlite"),
            )
            first = Agent(
                config, provider=MockProvider("sqlite answer"), use_storage=True
            )
            first_user = first.for_user("alice")
            conversation = first_user.conversations.create()
            first_user.run(
                "persist in sqlite",
                conversation_id=conversation.conversation_id,
            )

            loaded = (
                Agent(config, provider=MockProvider(), use_storage=True)
                .for_user("alice")
                .conversations.read(conversation.conversation_id)
            )

            self.assertEqual(
                ["persist in sqlite", "sqlite answer"],
                [message.content for message in loaded.messages],
            )
            self.assertTrue((config.storage.path / "events.sqlite3").is_file())


def _named_agent(
    root: Path,
    name: str,
    response: str,
    storage: JsonlStorage,
    *,
    provider: MockProvider | None = None,
) -> Agent:
    config = AgentConfig.create_default(root)
    config = replace(config, agent=replace(config.agent, name=name))
    return Agent(
        config,
        provider=provider or MockProvider(response),
        storage=storage,
    )

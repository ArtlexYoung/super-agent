import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agents.agent import Agent
from provider.chat import MockProvider
from runtime.config import AgentConfig
from runtime.storage import JsonlStorage
from skill.kinds.memory import MiniMemory


class ConversationRuntimeTests(unittest.TestCase):
    def test_conversation_management_replays_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(AgentConfig.create_default(tmp), provider=MockProvider())
            conversation = agent.create_conversation("Original")
            store = agent.runtime.create_store()

            store.append_conversation_message(
                conversation.conversation_id,
                "user",
                "Remember this",
            )
            renamed = agent.rename_conversation(conversation.conversation_id, "Renamed")
            cleared = agent.clear_conversation(conversation.conversation_id)

            self.assertEqual("Renamed", renamed.title)
            self.assertEqual([], cleared.messages)
            self.assertEqual(
                [conversation.conversation_id],
                [item.conversation_id for item in agent.list_conversations()],
            )
            agent.delete_conversation(conversation.conversation_id)
            self.assertEqual([], agent.list_conversations())

    def test_second_turn_loads_history_from_runtime_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = _SequenceProvider(["first answer", "second answer"])
            agent = Agent(AgentConfig.create_default(tmp), provider=provider)
            conversation = agent.create_conversation()

            agent.run("first question", conversation_id=conversation.conversation_id)
            agent.run("second question", conversation_id=conversation.conversation_id)

            self.assertEqual(
                ["system", "user", "assistant", "user"],
                [message["role"] for message in provider.calls[1]],
            )
            self.assertEqual("first question", provider.calls[1][1]["content"])
            self.assertEqual("first answer", provider.calls[1][2]["content"])
            stored = agent.read_conversation(conversation.conversation_id)
            self.assertEqual(
                ["first question", "first answer", "second question", "second answer"],
                [message.content for message in stored.messages],
            )
            self.assertTrue(stored.messages[-1].run_result)

    def test_explicit_messages_cannot_compete_with_stored_conversation_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(AgentConfig.create_default(tmp), provider=MockProvider())
            conversation = agent.create_conversation()

            with self.assertRaisesRegex(ValueError, "cannot be combined"):
                agent.run(
                    "hello",
                    conversation_id=conversation.conversation_id,
                    messages=[{"role": "user", "content": "duplicate history"}],
                )

    def test_user_scopes_isolate_conversations_memory_evaluations_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(AgentConfig.create_default(tmp), provider=MockProvider("ok"))
            conversation_id = "shared-conversation-id"

            agent.run("alpha secret", user_id="user-alpha", conversation_id=conversation_id)
            agent.run("beta secret", user_id="user-beta", conversation_id=conversation_id)
            alpha_store = agent.runtime.create_store("user-alpha")
            beta_store = agent.runtime.create_store("user-beta")
            MiniMemory(alpha_store).add_memory_item("alpha memory")

            self.assertEqual(
                ["alpha secret", "ok"],
                [item.content for item in alpha_store.read_conversation(conversation_id).messages],
            )
            self.assertEqual(
                ["beta secret", "ok"],
                [item.content for item in beta_store.read_conversation(conversation_id).messages],
            )
            self.assertEqual([], MiniMemory(beta_store).list_memory_items())
            self.assertTrue(alpha_store.read_evaluation_records())
            self.assertTrue(beta_store.read_evaluation_records())
            self.assertNotEqual(alpha_store.cache_root, beta_store.cache_root)

    def test_subagent_inherits_identity_without_duplicating_main_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = JsonlStorage(root / ".super-agent")
            main = _named_agent(root, "main", "main answer", storage)
            worker = _named_agent(root, "worker", "worker answer", storage)
            main.add_subagent(worker, name="worker")
            conversation = main.create_conversation(user_id="user-a")

            result = main.run(
                "delegate this",
                user_id="user-a",
                conversation_id=conversation.conversation_id,
            )

            stored = main.read_conversation(
                conversation.conversation_id,
                user_id="user-a",
            )
            child_run = worker.runtime.create_store("user-a").read_run(
                result.subagent_results[0].run_id
            )
            self.assertEqual(2, len(stored.messages))
            self.assertEqual([], worker.list_conversations("user-a"))
            self.assertEqual(conversation.conversation_id, child_run.conversation_id)
            self.assertEqual("user-a", child_run.user_id)
            self.assertEqual(result.run_id, child_run.parent_run_id)

    def test_new_agent_instance_reads_existing_conversations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AgentConfig.create_default(root)
            first = Agent(config, provider=MockProvider("persisted answer"))
            conversation = first.create_conversation()
            first.run("persist this", conversation_id=conversation.conversation_id)

            second = Agent(config, provider=MockProvider())

            loaded = second.read_conversation(conversation.conversation_id)
            self.assertEqual(
                ["persist this", "persisted answer"],
                [item.content for item in loaded.messages],
            )

    def test_skill_evolution_workspaces_are_isolated_by_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(AgentConfig.create_default(tmp), provider=MockProvider("instructions"))
            alpha = agent.create_skill_evolution_manager("alpha")
            beta = agent.create_skill_evolution_manager("beta")

            candidate = alpha.create_skill_candidate("private-note", "write private notes")

            self.assertNotEqual(alpha.evolution_root, beta.evolution_root)
            self.assertTrue(candidate.skill_path.is_dir())
            with self.assertRaisesRegex(KeyError, "skill candidate not found"):
                beta.evaluate_skill_candidate(candidate.candidate_id, [])


class _SequenceProvider(MockProvider):
    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self.responses = list(responses)
        self.calls: list[list[dict[str, object]]] = []

    def send_chat_messages(self, messages, model):
        self.calls.append(list(messages))
        return self.responses.pop(0)


def _named_agent(
    root: Path,
    name: str,
    response: str,
    storage: JsonlStorage,
) -> Agent:
    config = AgentConfig.create_default(root)
    config = replace(config, agent=replace(config.agent, name=name))
    return Agent(config, provider=MockProvider(response), storage=storage)

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from super_agent import Agent
from core.provider.chat import OpenAICompatibleProvider, ProviderConnection
from core.provider.pool import ProviderPool
from core.config import CommonConfig
from core.provider.secrets import UserSecretResolver
from core.models import RunIdentity, validate_user_id


class IdentityAndSecretIsolationTests(unittest.TestCase):
    def test_every_user_entry_point_uses_the_same_identity_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(CommonConfig.create_default(Path(tmp)), use_storage=True)
            user = agent.for_user("  alice  ")
            store = agent._create_event_store("  alice  ")
            identity = RunIdentity.create("  alice  ", "  demo  ")

            self.assertEqual("alice", validate_user_id("  alice  "))
            self.assertEqual("alice", user.user_id)
            self.assertEqual("alice", store.user_id)
            self.assertEqual("alice", identity.user_id)
            self.assertEqual("demo", identity.agent_name)

        for invalid in ("", "\n", "a" * 201):
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaises(ValueError):
                    validate_user_id(invalid)

    def test_user_secret_lookup_and_provider_cache_are_isolated(self) -> None:
        values = {
            ("alice", "MODEL_API_KEY"): "alice-secret",
            ("bob", "MODEL_API_KEY"): "bob-secret",
        }
        resolver = UserSecretResolver(lambda user_id, name: values.get((user_id, name)))
        connection = ProviderConnection(
            "openai-compatible",
            "https://api.example.test/v1",
            "MODEL_API_KEY",
        )
        root_pool = ProviderPool({})
        alice_pool = root_pool.create_user_provider_pool(
            resolver.get_environment_for_user("alice")
        )
        bob_pool = root_pool.create_user_provider_pool(
            resolver.get_environment_for_user("bob")
        )

        alice_provider = alice_pool.get_chat_provider("model:shared", connection)
        bob_provider = bob_pool.get_chat_provider("model:shared", connection)

        self.assertIsInstance(alice_provider, OpenAICompatibleProvider)
        self.assertIsInstance(bob_provider, OpenAICompatibleProvider)
        assert isinstance(alice_provider, OpenAICompatibleProvider)
        assert isinstance(bob_provider, OpenAICompatibleProvider)
        self.assertEqual("alice-secret", alice_provider.api_key)
        self.assertEqual("bob-secret", bob_provider.api_key)
        self.assertIsNot(alice_provider, bob_provider)
        self.assertEqual([], list(resolver.get_environment_for_user("alice")))

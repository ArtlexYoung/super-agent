"""Lazy provider instances shared by model profiles with the same connection."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping

from core.models import validate_user_id
from core.provider.chat import (
    ChatProvider,
    ProviderConnection,
    create_chat_provider,
    normalize_provider_connection,
)


UserSecretLookup = Callable[[str, str], str | None]


class UserSecretResolver:
    """Create a non-enumerable environment view for one validated user."""

    def __init__(
        self,
        lookup: UserSecretLookup | None = None,
        process_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.lookup = lookup
        self.process_environment = (
            os.environ if process_environment is None else process_environment
        )

    def get_environment_for_user(self, user_id: str) -> Mapping[str, str]:
        clean_user_id = validate_user_id(user_id)
        if self.lookup is None:
            return self.process_environment
        return _UserSecretEnvironment(clean_user_id, self.lookup)


class _UserSecretEnvironment(Mapping[str, str]):
    def __init__(self, user_id: str, lookup: UserSecretLookup) -> None:
        self.user_id = user_id
        self.lookup = lookup

    def __getitem__(self, name: str) -> str:
        if not isinstance(name, str) or not name:
            raise KeyError(name)
        value = self.lookup(self.user_id, name)
        if value is None:
            raise KeyError(name)
        if not isinstance(value, str):
            raise TypeError("user secret lookup must return a string or None")
        return value

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0


class ProviderPool:
    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self.environment = os.environ if environment is None else environment
        self._providers_by_profile: dict[str, ChatProvider] = {}
        self._providers_by_connection: dict[ProviderConnection, ChatProvider] = {}

    def add_chat_provider(self, profile_key: str, provider: ChatProvider) -> None:
        key = _clean_profile_key(profile_key)
        if key in self._providers_by_profile:
            raise ValueError(f"model profile already has a provider: {key}")
        self._providers_by_profile[key] = provider

    def create_user_provider_pool(
        self,
        environment: Mapping[str, str],
    ) -> "ProviderPool":
        pool = ProviderPool(environment)
        pool._providers_by_profile = dict(self._providers_by_profile)
        return pool

    def get_chat_provider(
        self,
        profile_key: str,
        connection: ProviderConnection,
    ) -> ChatProvider:
        key = _clean_profile_key(profile_key)
        selected = self._providers_by_profile.get(key)
        if selected is not None:
            return selected
        normalized = normalize_provider_connection(connection)
        provider = self._providers_by_connection.get(normalized)
        if provider is None:
            provider = create_chat_provider(normalized, self.environment)
            self._providers_by_connection[normalized] = provider
        return provider


def _clean_profile_key(value: str) -> str:
    key = value.strip().lower()
    if not key:
        raise ValueError("model profile key cannot be empty")
    return key

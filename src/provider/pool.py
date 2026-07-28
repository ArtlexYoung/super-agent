"""Lazy provider instances shared by model profiles with the same connection."""

from __future__ import annotations

import os
from typing import Mapping

from provider.chat import (
    ChatProvider,
    ProviderConnection,
    create_chat_provider,
    normalize_provider_connection,
)


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

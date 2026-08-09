"""Public zero-setup Super Agent entry point."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core.runtime.agent import Agent as RuntimeAgent

if TYPE_CHECKING:
    from core.state.backend import StorageBackend
    from core.state.store import DisclosureStorage, EventStore


class Agent(RuntimeAgent):
    """Wire external storage and user views onto the Core Agent."""

    def _create_storage_backend(
        self,
        backend: str,
        path: str,
        url_env: str | None,
    ) -> StorageBackend:
        from adapter.storage import create_storage_backend

        return create_storage_backend(backend, path, url_env)

    def _create_user_agent_view(self, user_id: str) -> object:
        from adapter.user import UserAgent

        return UserAgent(self, user_id)

    def _create_disclosure_storage(
        self,
        cache_root: Path,
        store: EventStore,
    ) -> DisclosureStorage:
        from adapter.storage.disclosure import DisclosureStorage as DurableDisclosureStorage

        return DurableDisclosureStorage(cache_root, store)

__all__ = ["Agent"]

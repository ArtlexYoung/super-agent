"""Public zero-setup Super Agent entry point."""

from core.events import StorageBackend
from core.runtime.agent import Agent as RuntimeAgent


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

__all__ = ["Agent"]

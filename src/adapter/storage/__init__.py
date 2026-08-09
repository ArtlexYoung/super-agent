from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.events import StorageBackend
    from core.state.events import EventStore


def create_storage_backend(
    backend: str,
    path: str,
    url_env: str | None = None,
) -> StorageBackend:
    if backend == "jsonl":
        from adapter.storage.jsonl import JsonlStorage

        return JsonlStorage(path)
    if backend == "sqlite":
        from adapter.storage.sqlite import SqliteStorage

        return SqliteStorage(path)
    if backend == "mysql":
        from adapter.storage.sql.mysql import MySqlStorage

        return MySqlStorage(url_env)
    if backend == "postgresql":
        from adapter.storage.sql.postgresql import PostgreSqlStorage

        return PostgreSqlStorage(url_env)
    raise ValueError(f"unknown storage backend: {backend}")


def create_local_event_store(
    root: str | Path,
    *,
    user_id: str = "local",
    agent_name: str = "super-agent",
) -> EventStore:
    """Create a JSONL EventStore for tests and local Skill tooling."""
    from adapter.storage.jsonl import JsonlStorage
    from adapter.storage.disclosure import DisclosureStorage
    from core.state.events import EventStore

    path = Path(root).expanduser().absolute()
    return EventStore(
        JsonlStorage(path),
        path,
        user_id,
        agent_name,
        disclosure_factory=lambda cache_root, store: DisclosureStorage(
            cache_root,
            store,
        ),
    )

__all__ = [
    "JsonlStorage",
    "MySqlStorage",
    "PostgreSqlStorage",
    "SqliteStorage",
    "create_storage_backend",
    "create_local_event_store",
]


def __getattr__(name: str) -> object:
    if name == "JsonlStorage":
        from adapter.storage.jsonl import JsonlStorage

        return JsonlStorage
    if name == "SqliteStorage":
        from adapter.storage.sqlite import SqliteStorage

        return SqliteStorage
    if name == "MySqlStorage":
        from adapter.storage.sql.mysql import MySqlStorage

        return MySqlStorage
    if name == "PostgreSqlStorage":
        from adapter.storage.sql.postgresql import PostgreSqlStorage

        return PostgreSqlStorage
    if name in {"StorageCopyReport", "StorageCopyUserResult", "copy_storage_events"}:
        from adapter.storage.copy import (
            StorageCopyReport,
            StorageCopyUserResult,
            copy_storage_events,
        )

        return {
            "StorageCopyReport": StorageCopyReport,
            "StorageCopyUserResult": StorageCopyUserResult,
            "copy_storage_events": copy_storage_events,
        }[name]
    raise AttributeError(name)

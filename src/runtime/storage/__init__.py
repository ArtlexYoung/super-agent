from runtime.storage.contracts import StorageBackend, StorageEvent, StorageEventQuery
from runtime.storage.copy import StorageCopyReport, StorageCopyUserResult, copy_storage_events
from runtime.storage.jsonl import JsonlStorage
from runtime.storage.sql import MySqlStorage, PostgreSqlStorage
from runtime.storage.sqlite import SqliteStorage


def create_storage_backend(
    backend: str,
    path: str,
    url_env: str | None = None,
) -> StorageBackend:
    if backend == "jsonl":
        return JsonlStorage(path)
    if backend == "sqlite":
        return SqliteStorage(path)
    if backend == "mysql":
        return MySqlStorage(url_env)
    if backend == "postgresql":
        return PostgreSqlStorage(url_env)
    raise ValueError(f"unknown storage backend: {backend}")

__all__ = [
    "JsonlStorage",
    "MySqlStorage",
    "PostgreSqlStorage",
    "SqliteStorage",
    "StorageCopyReport",
    "StorageCopyUserResult",
    "StorageBackend",
    "StorageEvent",
    "StorageEventQuery",
    "copy_storage_events",
    "create_storage_backend",
]

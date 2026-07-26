from runtime.storage.contracts import StorageBackend, StorageEvent, StorageEventQuery
from runtime.storage.copy import StorageCopyReport, StorageCopyUserResult, copy_storage_events
from runtime.storage.jsonl import JsonlStorage
from runtime.storage.sqlite import SqliteStorage


def create_storage_backend(backend: str, path: str) -> StorageBackend:
    if backend == "jsonl":
        return JsonlStorage(path)
    if backend == "sqlite":
        return SqliteStorage(path)
    raise ValueError(f"storage backend is not available in this release: {backend}")

__all__ = [
    "JsonlStorage",
    "SqliteStorage",
    "StorageCopyReport",
    "StorageCopyUserResult",
    "StorageBackend",
    "StorageEvent",
    "StorageEventQuery",
    "copy_storage_events",
    "create_storage_backend",
]

from runtime.storage.contracts import StorageBackend, StorageEvent, StorageEventQuery
from runtime.storage.jsonl import JsonlStorage


def create_storage_backend(backend: str, path: str) -> StorageBackend:
    if backend == "jsonl":
        return JsonlStorage(path)
    raise ValueError(f"storage backend is not available in this release: {backend}")

__all__ = [
    "JsonlStorage",
    "StorageBackend",
    "StorageEvent",
    "StorageEventQuery",
    "create_storage_backend",
]

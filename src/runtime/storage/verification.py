"""Run the same multiuser isolation proof against every storage backend."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from runtime.storage.contracts import StorageBackend, StorageEventQuery
from runtime.store import RuntimeStore
from skill.kinds.memory import MiniMemory


STORAGE_BACKEND_NAMES = ("jsonl", "sqlite", "mysql", "postgresql")
STORAGE_ISOLATION_SCHEMA_VERSION = 1
DEFAULT_REMOTE_URL_ENVIRONMENTS = {
    "mysql": "SUPER_AGENT_TEST_MYSQL_URL",
    "postgresql": "SUPER_AGENT_TEST_POSTGRESQL_URL",
}


@dataclass(frozen=True)
class StorageIsolationResult:
    backend: str
    status: str
    duration_ms: float
    checks: list[str]
    stored_event_count: int
    unavailable_reason: str = ""
    error_type: str = ""


@dataclass(frozen=True)
class StorageIsolationReport:
    results: list[StorageIsolationResult]

    @property
    def all_available_backends_passed(self) -> bool:
        return all(result.status != "failed" for result in self.results)

    @property
    def all_backends_verified(self) -> bool:
        return all(result.status == "passed" for result in self.results)


def verify_multiuser_isolation_across_storage_backends(
    local_root: str | Path,
    remote_url_environments: dict[str, str] | None = None,
    backend_names: list[str] | None = None,
) -> StorageIsolationReport:
    """Verify conversations, memory, habits, users, and Agents stay isolated."""
    selected_names = list(
        STORAGE_BACKEND_NAMES if backend_names is None else backend_names
    )
    _validate_backend_names(selected_names)
    environments = dict(DEFAULT_REMOTE_URL_ENVIRONMENTS)
    environments.update(remote_url_environments or {})
    root = Path(local_root).expanduser().absolute()
    results = [
        _verify_one_storage_backend(name, root / name, environments)
        for name in selected_names
    ]
    return StorageIsolationReport(results)


def storage_isolation_report_to_dict(
    report: StorageIsolationReport,
) -> dict[str, object]:
    return {
        "schema_version": STORAGE_ISOLATION_SCHEMA_VERSION,
        "all_available_backends_passed": report.all_available_backends_passed,
        "all_backends_verified": report.all_backends_verified,
        "verified_backend_count": sum(
            result.status == "passed" for result in report.results
        ),
        "backends": [
            {
                "backend": result.backend,
                "status": result.status,
                "duration_ms": result.duration_ms,
                "checks": list(result.checks),
                "stored_event_count": result.stored_event_count,
                "unavailable_reason": result.unavailable_reason,
                "error_type": result.error_type,
            }
            for result in report.results
        ],
    }


def _verify_one_storage_backend(
    name: str,
    root: Path,
    remote_url_environments: dict[str, str],
) -> StorageIsolationResult:
    started_at = perf_counter()
    try:
        backend = _create_verification_storage_backend(
            name,
            root,
            remote_url_environments,
        )
    except _StorageBackendUnavailable as error:
        return StorageIsolationResult(
            backend=name,
            status="unavailable",
            duration_ms=_elapsed_milliseconds(started_at),
            checks=[],
            stored_event_count=0,
            unavailable_reason=str(error),
        )
    suffix = uuid4().hex
    user_ids = [f"super-agent-proof-{suffix}-a", f"super-agent-proof-{suffix}-b"]
    try:
        checks, event_count = _run_multiuser_isolation_checks(
            backend,
            root,
            user_ids,
        )
        _delete_verification_events(backend, user_ids)
        if any(
            backend.read_events(StorageEventQuery(user_id=user_id))
            for user_id in user_ids
        ):
            raise AssertionError("storage verification cleanup failed")
        checks.append("temporary_state_cleanup")
        return StorageIsolationResult(
            backend=name,
            status="passed",
            duration_ms=_elapsed_milliseconds(started_at),
            checks=checks,
            stored_event_count=event_count,
        )
    except Exception as error:
        try:
            _delete_verification_events(backend, user_ids)
        except Exception:
            pass
        return StorageIsolationResult(
            backend=name,
            status="failed",
            duration_ms=_elapsed_milliseconds(started_at),
            checks=[],
            stored_event_count=0,
            error_type=type(error).__name__,
        )


def _create_verification_storage_backend(
    name: str,
    root: Path,
    remote_url_environments: dict[str, str],
) -> StorageBackend:
    from runtime.storage import create_storage_backend

    url_env = remote_url_environments.get(name)
    # Dedicated test URLs keep verification writes away from production Agent storage.
    if name in DEFAULT_REMOTE_URL_ENVIRONMENTS and (
        not url_env or not os.environ.get(url_env)
    ):
        environment_name = url_env or DEFAULT_REMOTE_URL_ENVIRONMENTS[name]
        raise _StorageBackendUnavailable(
            f"environment variable {environment_name} is not configured"
        )
    try:
        return create_storage_backend(name, str(root), url_env)
    except RuntimeError as error:
        raise _StorageBackendUnavailable(str(error)) from error


def _run_multiuser_isolation_checks(
    backend: StorageBackend,
    local_root: Path,
    user_ids: list[str],
) -> tuple[list[str], int]:
    user_a, user_b = user_ids
    agent_name = "proof-agent"
    store_a = RuntimeStore(
        backend=backend,
        local_root=local_root,
        user_id=user_a,
        agent_name=agent_name,
    )
    store_b = RuntimeStore(
        backend=backend,
        local_root=local_root,
        user_id=user_b,
        agent_name=agent_name,
    )
    subagent_store = RuntimeStore(
        backend=backend,
        local_root=local_root,
        user_id=user_a,
        agent_name="proof-subagent",
    )

    _write_isolated_domain_state(store_a, "alice")
    _write_isolated_domain_state(store_b, "bob")
    MiniMemory(subagent_store).add_memory_item("subagent-only")

    checks = list(
        dict.fromkeys(
            [
                _require_conversation_isolation(store_a, "alice"),
                _require_conversation_isolation(store_b, "bob"),
                _require_memory_isolation(store_a, "alice"),
                _require_memory_isolation(store_b, "bob"),
                _require_habit_isolation(store_a, "alice-skill"),
                _require_habit_isolation(store_b, "bob-skill"),
                _require_agent_isolation(store_a, subagent_store),
            ]
        )
    )
    event_count = sum(
        len(backend.read_events(StorageEventQuery(user_id=user_id)))
        for user_id in user_ids
    )
    return checks, event_count


def _write_isolated_domain_state(store: RuntimeStore, marker: str) -> None:
    conversation_id = "shared-conversation"
    store.create_conversation(marker, conversation_id=conversation_id)
    store.append_conversation_message(
        conversation_id,
        "user",
        f"{marker}-only",
        run_id=f"{marker}-run",
    )
    MiniMemory(store).add_memory_item(f"{marker}-only")
    store.record_usage_habits("direct", [f"{marker}-skill"])


def _require_conversation_isolation(store: RuntimeStore, marker: str) -> str:
    messages = store.read_conversation("shared-conversation").messages
    if [message.content for message in messages] != [f"{marker}-only"]:
        raise AssertionError("conversation user isolation failed")
    return "conversation_user_isolation"


def _require_memory_isolation(store: RuntimeStore, marker: str) -> str:
    if [item["text"] for item in store.list_memory_items()] != [f"{marker}-only"]:
        raise AssertionError("memory user isolation failed")
    return "memory_user_isolation"


def _require_habit_isolation(store: RuntimeStore, skill_name: str) -> str:
    habits = store.read_usage_habits()
    if habits["skills"] != {skill_name: 1}:
        raise AssertionError("usage habit user isolation failed")
    return "skill_usage_user_isolation"


def _require_agent_isolation(
    main_store: RuntimeStore,
    subagent_store: RuntimeStore,
) -> str:
    main_texts = [item["text"] for item in main_store.list_memory_items()]
    subagent_texts = [item["text"] for item in subagent_store.list_memory_items()]
    if "subagent-only" in main_texts or subagent_texts != ["subagent-only"]:
        raise AssertionError("Agent storage isolation failed")
    return "agent_scope_isolation"


def _delete_verification_events(
    backend: StorageBackend,
    user_ids: list[str],
) -> None:
    for user_id in user_ids:
        backend.delete_events(StorageEventQuery(user_id=user_id))


def _validate_backend_names(names: list[str]) -> None:
    if not names:
        raise ValueError("storage verification requires at least one backend")
    if len(names) != len(set(names)):
        raise ValueError("storage verification backend names must be unique")
    unknown = sorted(set(names) - set(STORAGE_BACKEND_NAMES))
    if unknown:
        raise ValueError(f"unknown storage verification backend: {', '.join(unknown)}")


def _elapsed_milliseconds(started_at: float) -> float:
    return round(max(0.0, (perf_counter() - started_at) * 1_000), 3)


class _StorageBackendUnavailable(RuntimeError):
    pass

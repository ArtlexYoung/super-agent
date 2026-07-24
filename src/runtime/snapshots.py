"""Versioned runtime snapshots and deterministic execution locks."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from capability.contracts import AgentCapabilitySet
from provider.chat import ChatProvider
from runtime.config import AgentConfig
from runtime.events import RunContext, RunEvent, run_event_to_dict
from runtime.models import RunResult
from skill.disclosure import SkillIndex


RUN_SNAPSHOT_SCHEMA_VERSION = 1
RUNTIME_LOCK_SCHEMA_VERSION = 1
RUN_EXPORT_SCHEMA_VERSION = 1
RUN_SNAPSHOT_FILE = "snapshot.json"
RUNTIME_LOCK_FILE = "runtime.lock.json"
RUN_SNAPSHOT_FIELDS = {
    "schema_version",
    "run_id",
    "agent_name",
    "parent_run_id",
    "status",
    "prompt",
    "started_at",
    "finished_at",
    "event_count",
    "last_event_type",
    "runtime_lock_path",
    "runtime_lock_sha256",
    "workflow",
    "used_skills",
    "stop_reason",
    "error",
}
RUNTIME_LOCK_FIELDS = {
    "schema_version",
    "agent",
    "model",
    "paths",
    "capabilities",
    "skills",
}
RUN_STATUSES = {"running", "completed", "failed"}


@dataclass(frozen=True)
class RunSnapshot:
    schema_version: int
    run_id: str
    agent_name: str
    parent_run_id: str | None
    status: str
    prompt: str
    started_at: str
    finished_at: str | None
    event_count: int
    last_event_type: str
    runtime_lock_path: str | None
    runtime_lock_sha256: str | None
    workflow: str | None
    used_skills: list[str]
    stop_reason: str | None
    error: dict[str, str] | None


class RunSnapshotSession:
    def __init__(
        self,
        store: "RunSnapshotStore",
        context: RunContext,
        prompt: str,
    ) -> None:
        self.store = store
        self.context = context
        self.prompt = prompt
        self.runtime_lock_path: Path | None = None
        self.runtime_lock_sha256: str | None = None
        self.context.record_event(
            "runtime.snapshot.started",
            {"snapshot_path": str(store._snapshot_path(context.run_id))},
        )
        self._write_snapshot(status="running")

    def record_skill_index(
        self,
        skill_index: SkillIndex,
        config: AgentConfig,
        capabilities: AgentCapabilitySet,
        provider: ChatProvider,
    ) -> None:
        if self.runtime_lock_path is not None:
            raise RuntimeError("runtime lock has already been recorded")
        path = self.store._runtime_lock_path(self.context.run_id)
        content = _runtime_lock_to_dict(config, capabilities, skill_index, provider)
        data = _json_bytes(content)
        _write_bytes_atomically(path, data)
        self.runtime_lock_path = Path(RUNTIME_LOCK_FILE)
        self.runtime_lock_sha256 = hashlib.sha256(data).hexdigest()
        self.context.record_event(
            "runtime.locked",
            {
                "runtime_lock_path": str(path),
                "runtime_lock_sha256": self.runtime_lock_sha256,
                "skill_count": len(skill_index.entries),
            },
        )
        self._write_snapshot(status="running")

    def record_run_completed(self, result: RunResult) -> None:
        self._write_snapshot(
            status="completed",
            workflow=result.workflow,
            used_skills=result.skills,
            stop_reason=result.stop_reason,
        )

    def record_run_failed(self, error: Exception) -> None:
        self._write_snapshot(
            status="failed",
            error={"error_type": type(error).__name__, "message": str(error)},
        )

    def _write_snapshot(
        self,
        *,
        status: str,
        workflow: str | None = None,
        used_skills: list[str] | None = None,
        stop_reason: str | None = None,
        error: dict[str, str] | None = None,
    ) -> None:
        events = self.context.store.read_run_events(self.context.run_id)
        snapshot = RunSnapshot(
            schema_version=RUN_SNAPSHOT_SCHEMA_VERSION,
            run_id=self.context.run_id,
            agent_name=self.context.agent_name,
            parent_run_id=self.context.parent_run_id,
            status=status,
            prompt=self.prompt,
            started_at=events[0].created_at,
            finished_at=events[-1].created_at if status != "running" else None,
            event_count=len(events),
            last_event_type=events[-1].event_type,
            runtime_lock_path=(
                None if self.runtime_lock_path is None else str(self.runtime_lock_path)
            ),
            runtime_lock_sha256=self.runtime_lock_sha256,
            workflow=workflow,
            used_skills=list(used_skills or []),
            stop_reason=stop_reason,
            error=error,
        )
        _write_bytes_atomically(
            self.store._snapshot_path(self.context.run_id),
            _json_bytes(run_snapshot_to_dict(snapshot)),
        )


class RunSnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def start_run(
        self,
        context: RunContext,
        *,
        prompt: str,
    ) -> RunSnapshotSession:
        return RunSnapshotSession(self, context, prompt)

    def read_run_snapshot(self, run_id: str) -> RunSnapshot:
        path = self._snapshot_path(run_id)
        if not path.is_file():
            raise KeyError(f"run snapshot not found: {run_id}")
        return run_snapshot_from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_run_snapshots(self) -> list[RunSnapshot]:
        if not self.root.is_dir():
            return []
        snapshots = [
            self.read_run_snapshot(path.parent.name)
            for path in self.root.glob(f"*/{RUN_SNAPSHOT_FILE}")
        ]
        return sorted(
            snapshots,
            key=lambda item: (item.started_at, item.run_id),
            reverse=True,
        )

    def export_run(self, run_id: str, path: Path) -> Path:
        snapshot = self.read_run_snapshot(run_id)
        events = _read_events_for_snapshot(self.root, snapshot)
        runtime_lock = _read_runtime_lock(self.root, snapshot)
        document = {
            "schema_version": RUN_EXPORT_SCHEMA_VERSION,
            "snapshot": run_snapshot_to_dict(snapshot),
            "runtime_lock": runtime_lock,
            "events": [run_event_to_dict(event) for event in events],
        }
        _write_bytes_atomically(path, _json_bytes(document))
        return path

    def explain_run(self, run_id: str) -> dict[str, object]:
        snapshot = self.read_run_snapshot(run_id)
        events = _read_events_for_snapshot(self.root, snapshot)
        return {
            "schema_version": 1,
            "snapshot": run_snapshot_to_dict(snapshot),
            "runtime_lock": _read_runtime_lock(self.root, snapshot),
            "selection_decisions": _latest_selection_decisions(events),
            "disclosure_path": [
                run_event_to_dict(event)
                for event in events
                if event.event_type == "skill.disclosed"
            ],
            "events": [run_event_to_dict(event) for event in events],
        }

    def _snapshot_path(self, run_id: str) -> Path:
        return self.root / _clean_run_id(run_id) / RUN_SNAPSHOT_FILE

    def _runtime_lock_path(self, run_id: str) -> Path:
        return self.root / _clean_run_id(run_id) / RUNTIME_LOCK_FILE


def run_snapshot_to_dict(snapshot: RunSnapshot) -> dict[str, object]:
    if snapshot.schema_version != RUN_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(
            f"migrate run snapshot schema_version {snapshot.schema_version} to "
            f"run snapshot schema_version {RUN_SNAPSHOT_SCHEMA_VERSION}"
        )
    return {
        "schema_version": snapshot.schema_version,
        "run_id": snapshot.run_id,
        "agent_name": snapshot.agent_name,
        "parent_run_id": snapshot.parent_run_id,
        "status": snapshot.status,
        "prompt": snapshot.prompt,
        "started_at": snapshot.started_at,
        "finished_at": snapshot.finished_at,
        "event_count": snapshot.event_count,
        "last_event_type": snapshot.last_event_type,
        "runtime_lock_path": snapshot.runtime_lock_path,
        "runtime_lock_sha256": snapshot.runtime_lock_sha256,
        "workflow": snapshot.workflow,
        "used_skills": list(snapshot.used_skills),
        "stop_reason": snapshot.stop_reason,
        "error": snapshot.error,
    }


def run_snapshot_from_dict(value: object) -> RunSnapshot:
    data = _require_exact_object(value, RUN_SNAPSHOT_FIELDS, "run snapshot")
    schema_version = _required_integer(data, "schema_version")
    if schema_version != RUN_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(
            f"migrate run snapshot schema_version {schema_version} to "
            f"run snapshot schema_version {RUN_SNAPSHOT_SCHEMA_VERSION}"
        )
    status = _required_string(data, "status")
    if status not in RUN_STATUSES:
        raise ValueError(f"unknown run snapshot status: {status}")
    parent_run_id = _optional_string(data, "parent_run_id")
    finished_at = _optional_string(data, "finished_at")
    runtime_lock_path = _optional_string(data, "runtime_lock_path")
    runtime_lock_sha256 = _optional_string(data, "runtime_lock_sha256")
    workflow = _optional_string(data, "workflow")
    stop_reason = _optional_string(data, "stop_reason")
    error = _optional_error(data.get("error"))
    return RunSnapshot(
        schema_version=schema_version,
        run_id=_required_string(data, "run_id"),
        agent_name=_required_string(data, "agent_name"),
        parent_run_id=parent_run_id,
        status=status,
        prompt=_required_string(data, "prompt", allow_empty=True),
        started_at=_required_string(data, "started_at"),
        finished_at=finished_at,
        event_count=_required_integer(data, "event_count"),
        last_event_type=_required_string(data, "last_event_type"),
        runtime_lock_path=runtime_lock_path,
        runtime_lock_sha256=runtime_lock_sha256,
        workflow=workflow,
        used_skills=_required_string_list(data, "used_skills"),
        stop_reason=stop_reason,
        error=error,
    )


def _runtime_lock_to_dict(
    config: AgentConfig,
    capabilities: AgentCapabilitySet,
    skill_index: SkillIndex,
    provider: ChatProvider,
) -> dict[str, object]:
    # Model credentials remain in their environment; the lock stores only the variable name.
    agent = config.agent
    model = config.model
    return {
        "schema_version": RUNTIME_LOCK_SCHEMA_VERSION,
        "agent": {
            "name": agent.name,
            "system": agent.system,
            "workflow": agent.workflow,
            "memory": agent.memory,
            "skills": list(agent.skills),
            "max_agent_chain_depth": agent.max_agent_chain_depth,
            "use_features": list(agent.use_features),
            "disable_names": list(agent.disable_names),
        },
        "model": {
            "provider": model.provider,
            "model": model.model,
            "base_url": model.base_url,
            "api_key_env": model.api_key_env,
            "adapter": f"{type(provider).__module__}.{type(provider).__qualname__}",
        },
        "paths": {
            "config_source": str(config.source),
            "skills": [str(path) for path in config.paths.skills],
            "memory": str(config.paths.memory),
        },
        "capabilities": _capability_versions(capabilities),
        "skills": [
            {
                "key": entry.reference.key,
                "name": entry.reference.name,
                "capability": entry.reference.capability,
                "version": entry.version,
                "content_sha256": entry.content_sha256,
                "provides": list(entry.provides),
                "requires": list(entry.requires),
            }
            for entry in skill_index.entries
        ],
    }


def _capability_versions(capabilities: AgentCapabilitySet) -> list[dict[str, str]]:
    values = [
        _capability_version("run_controller", capabilities.run_controller),
        _capability_version("skill_retriever", capabilities.skill_retriever),
        _capability_version("run_result_evaluator", capabilities.run_result_evaluator),
        _capability_version("skill_updater", capabilities.skill_updater),
        _capability_version("run_recorder", capabilities.run_recorder),
    ]
    values.extend(
        _capability_version(f"skill_executor:{name}", executor)
        for name, executor in sorted(capabilities.skill_executors.items())
    )
    return values


def _capability_version(slot: str, capability: object) -> dict[str, str]:
    return {
        "slot": slot,
        "name": str(getattr(capability, "name")),
        "version": str(getattr(capability, "version")),
    }


def _read_events_for_snapshot(root: Path, snapshot: RunSnapshot) -> list[RunEvent]:
    from runtime.events import RunTraceStore

    return RunTraceStore(root).read_run_events(snapshot.run_id)


def _read_runtime_lock(root: Path, snapshot: RunSnapshot) -> dict[str, object] | None:
    if snapshot.runtime_lock_path is None or snapshot.runtime_lock_sha256 is None:
        return None
    path = root / snapshot.run_id / RUNTIME_LOCK_FILE
    if snapshot.runtime_lock_path != RUNTIME_LOCK_FILE:
        raise ValueError(f"runtime lock path does not match run snapshot: {snapshot.run_id}")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != snapshot.runtime_lock_sha256:
        raise ValueError(f"runtime lock hash does not match run snapshot: {snapshot.run_id}")
    value = _require_exact_object(
        json.loads(data),
        RUNTIME_LOCK_FIELDS,
        "runtime lock",
    )
    if _required_integer(value, "schema_version") != RUNTIME_LOCK_SCHEMA_VERSION:
        raise ValueError("unsupported runtime lock schema_version")
    return value


def _latest_selection_decisions(events: list[RunEvent]) -> list[object]:
    for event in reversed(events):
        if event.event_type == "skills.selected":
            decisions = event.data.get("decisions", [])
            return list(decisions) if isinstance(decisions, list) else []
    return []


def _clean_run_id(run_id: str) -> str:
    value = run_id.strip()
    if not value or value in {".", ".."} or Path(value).name != value:
        raise ValueError(f"invalid run_id: {run_id}")
    return value


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_bytes_atomically(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _require_exact_object(
    value: object,
    fields: set[str],
    description: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be a JSON object")
    missing = fields - set(value)
    extra = set(value) - fields
    if missing or extra:
        raise ValueError(
            f"{description} fields do not match schema: "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return value


def _required_string(
    data: dict[str, Any],
    name: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = data[name]
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"{name} must be a {'string' if allow_empty else 'non-empty string'}")
    return value


def _optional_string(data: dict[str, Any], name: str) -> str | None:
    value = data[name]
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"{name} must be a non-empty string or null")
    return value


def _required_integer(data: dict[str, Any], name: str) -> int:
    value = data[name]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _required_string_list(data: dict[str, Any], name: str) -> list[str]:
    value = data[name]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be a string array")
    return list(value)


def _optional_error(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"error_type", "message"}:
        raise ValueError("run snapshot error must contain error_type and message")
    error_type = value["error_type"]
    message = value["message"]
    if not isinstance(error_type, str) or not isinstance(message, str):
        raise ValueError("run snapshot error values must be strings")
    return {"error_type": error_type, "message": message}

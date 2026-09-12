"""实现无依赖内存、分段 JSONL 和 SQLite 记录后端。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock

from super_agent.core.event import CheckpointConflictError, RunCheckpoint, utc_now
from super_agent.core.records import EventStore, Record, RecordBackend, RecordQuery


class MemoryStorage:
    """显式选择时使用的进程内记录后端。"""

    def __init__(self) -> None:
        self._records: list[Record] = []
        self._lock = RLock()

    def append(self, record: Record) -> Record:
        with self._lock:
            if any(item.event_id == record.event_id for item in self._records):
                raise ValueError(f"duplicate event ID: {record.event_id}")
            position = _next_position(self._records, record)
            stored = replace(record, position=position)
            self._records.append(stored)
            return stored

    def read(self, query: RecordQuery) -> list[Record]:
        with self._lock:
            return _select(list(self._records), query)

    def delete(self, query: RecordQuery) -> int:
        with self._lock:
            before = len(self._records)
            self._records = [record for record in self._records if not query.matches(record)]
            return before - len(self._records)


class MemoryCheckpointStore:
    """显式选择时使用的进程内检查点存储。"""

    def __init__(self) -> None:
        self._checkpoints: dict[str, RunCheckpoint] = {}
        self._lock = RLock()

    def save(self, checkpoint: RunCheckpoint) -> RunCheckpoint:
        with self._lock:
            current = self._checkpoints.get(checkpoint.checkpoint_id)
            if not _checkpoint_write_required(current, checkpoint):
                return checkpoint
            self._checkpoints[checkpoint.checkpoint_id] = checkpoint
            return checkpoint

    def read(self, checkpoint_id: str) -> RunCheckpoint:
        with self._lock:
            try:
                return self._checkpoints[checkpoint_id]
            except KeyError as error:
                raise KeyError(f"checkpoint not found: {checkpoint_id}") from error

    def delete(self, checkpoint_id: str) -> bool:
        with self._lock:
            return self._checkpoints.pop(checkpoint_id, None) is not None


class EventMemoryStore:
    """把任意记录后端收敛为 Memory Skill 所需的最小接口。"""

    def __init__(self, store: EventStore) -> None:
        self.store = store

    def read_items(self) -> list[dict[str, object]]:
        return [dict(record.data) for record in self.store.read("memory")]

    def append_item(
        self, memory_id: str, event_type: str, data: dict[str, object]
    ) -> object:
        return self.store.append("memory", memory_id, event_type, data)

    def read_memory_checkpoints(self) -> list[dict[str, object]]:
        """读取独立的记忆检查点流，不读取记忆正文。"""
        return [dict(record.data) for record in self.store.read("memory_checkpoint")]

    def append_memory_checkpoint(
        self, checkpoint_id: str, data: Mapping[str, object]
    ) -> object:
        """把检查点元数据写入现有记录后端。"""
        return self.store.append(
            "memory_checkpoint", checkpoint_id, "memory.checkpoint.created", data
        )


class EventCheckpointStore:
    """将检查点适配到现有 JSONL、SQLite 或远程记录后端。"""

    def __init__(self, store: EventStore) -> None:
        self.store = store
        self._lock = RLock()

    def save(self, checkpoint: RunCheckpoint) -> RunCheckpoint:
        with self._lock:
            records = self.store.read("checkpoint", checkpoint.checkpoint_id)
            current, _ = _latest_checkpoint(records)
            if not _checkpoint_write_required(current, checkpoint):
                return checkpoint
            event_id = _checkpoint_event_id(self.store, checkpoint)
            try:
                self.store.append(
                    "checkpoint",
                    checkpoint.checkpoint_id,
                    "checkpoint.saved",
                    checkpoint.to_dict(),
                    event_id=event_id,
                )
            except ValueError:
                duplicate = next(
                    (
                        item
                        for item in self.store.read("checkpoint", checkpoint.checkpoint_id)
                        if item.event_id == event_id
                    ),
                    None,
                )
                if duplicate is None:
                    raise
                _checkpoint_write_required(
                    RunCheckpoint.from_dict(duplicate.data), checkpoint
                )
                return checkpoint
            records = self.store.read("checkpoint", checkpoint.checkpoint_id)
            latest, latest_record = _latest_checkpoint(records)
            stale_ids = tuple(
                item.event_id for item in records if item is not latest_record
            )
            if stale_ids:
                _delete_checkpoint_records(self.store, checkpoint.checkpoint_id, stale_ids)
            _checkpoint_write_required(latest, checkpoint)
            return checkpoint

    def read(self, checkpoint_id: str) -> RunCheckpoint:
        with self._lock:
            records = self.store.read("checkpoint", checkpoint_id)
            checkpoint, _ = _latest_checkpoint(records)
            if checkpoint is None:
                raise KeyError(f"checkpoint not found: {checkpoint_id}")
            return checkpoint

    def delete(self, checkpoint_id: str) -> bool:
        with self._lock:
            return bool(self.store.delete("checkpoint", checkpoint_id))


class JsonlMemoryStore(EventMemoryStore):
    """默认的工作目录记忆适配器。"""

    def __init__(
        self,
        path: str | Path,
        user_id: str,
        agent_name: str,
    ) -> None:
        super().__init__(EventStore(JsonlStorage(path), user_id, agent_name))


class JsonlStorage:
    """默认本地后端；按月和大小分段，清理必须显式调用。"""

    def __init__(
        self,
        root: str | Path,
        *,
        max_segment_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        if max_segment_bytes < 1024:
            raise ValueError("JSONL segment size must be at least 1024 bytes")
        self.max_segment_bytes = max_segment_bytes
        self._lock = RLock()

    def append(self, record: Record) -> Record:
        with self._lock:
            records = self._read_all()
            if any(item.event_id == record.event_id for item in records):
                raise ValueError(f"duplicate event ID: {record.event_id}")
            stored = replace(record, position=_next_position(records, record))
            line = json.dumps(stored.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
            path = self._append_path(stored.created_at, len(line.encode()))
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
            return stored

    def read(self, query: RecordQuery) -> list[Record]:
        with self._lock:
            return _select(self._read_all(), query)

    def delete(self, query: RecordQuery) -> int:
        with self._lock:
            deleted = 0
            for path in self._paths():
                records = _read_jsonl(path)
                kept = [record for record in records if not query.matches(record)]
                deleted += len(records) - len(kept)
                if len(kept) == len(records):
                    continue
                if kept:
                    _replace_jsonl(path, kept)
                else:
                    path.unlink(missing_ok=True)
            return deleted

    def _read_all(self) -> list[Record]:
        records: list[Record] = []
        for path in self._paths():
            records.extend(_read_jsonl(path))
        return records

    def _paths(self) -> list[Path]:
        if self.root.suffix == ".jsonl":
            return [self.root] if self.root.is_file() else []
        if not self.root.exists():
            return []
        return sorted(self.root.glob("records-*.jsonl"))

    def _append_path(self, created_at: str, incoming_bytes: int) -> Path:
        if self.root.suffix == ".jsonl":
            return self.root
        month = created_at[:7]
        candidates = sorted(self.root.glob(f"records-{month}-*.jsonl")) if self.root.exists() else []
        if not candidates:
            return self.root / f"records-{month}-0001.jsonl"
        latest = candidates[-1]
        if latest.stat().st_size + incoming_bytes <= self.max_segment_bytes:
            return latest
        number = int(latest.stem.rsplit("-", 1)[1]) + 1
        return self.root / f"records-{month}-{number:04d}.jsonl"

class SqliteStorage:
    """使用标准库 SQLite 的单文件可选后端。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._lock = RLock()

    def append(self, record: Record) -> Record:
        with self._lock, self._connect() as connection:
            position = connection.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM records WHERE scope_key=? AND agent_name=? AND stream=? AND stream_id=?",
                (record.scope_key, record.agent_name, record.stream, record.stream_id),
            ).fetchone()[0]
            stored = replace(record, position=int(position))
            try:
                connection.execute(
                    "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    record_to_sql_row(stored),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(f"duplicate event ID: {record.event_id}") from error
        return stored

    def read(self, query: RecordQuery) -> list[Record]:
        clauses, parameters = build_sql_filters(query, "?")
        order = "DESC" if query.descending else "ASC"
        sql = "SELECT event_id,user_id,agent_name,scope_key,stream,stream_id,event_type,data,created_at,position FROM records"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY created_at {order}, position {order}, event_id {order}"
        if query.limit is not None:
            sql += " LIMIT ?"
            parameters.append(query.limit)
        with self._lock, self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [record_from_sql_row(row) for row in rows]

    def delete(self, query: RecordQuery) -> int:
        clauses, parameters = build_sql_filters(query, "?")
        if not clauses:
            raise ValueError("refusing to delete all SQLite records without a filter")
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM records WHERE " + " AND ".join(clauses), parameters)
            return max(0, cursor.rowcount)

    def verify(self) -> dict[str, object]:
        with self._lock, self._connect() as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
            count = connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        return {"backend": "sqlite", "ok": result == "ok", "detail": result, "records": count}

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                event_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                stream TEXT NOT NULL,
                stream_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                position INTEGER NOT NULL,
                UNIQUE(scope_key, agent_name, stream, stream_id, position)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS records_scope ON records(scope_key, agent_name, stream, stream_id, created_at)"
        )
        return connection

def create_storage(
    backend: str = "jsonl",
    path: str | Path = ".super-agent",
    *,
    database_url: str | None = None,
) -> RecordBackend:
    """按用户显式选择创建记录后端，不做不可见退化。"""
    selected = backend.strip().lower()
    if selected == "memory":
        return MemoryStorage()
    if selected == "jsonl":
        return JsonlStorage(path)
    if selected == "sqlite":
        target = Path(path)
        sqlite_path = target if target.suffix else target / "super-agent.sqlite3"
        return SqliteStorage(sqlite_path)
    if selected in {"mysql", "postgresql"}:
        if not database_url:
            raise ValueError(f"{selected} storage requires database_url")
        from super_agent.adapter.database import DatabaseStorage

        return DatabaseStorage(selected, database_url)
    raise ValueError(f"unknown storage backend: {backend}")


def verify_storage(backend: RecordBackend) -> dict[str, object]:
    verify = getattr(backend, "verify", None)
    if callable(verify):
        return verify()
    try:
        records = backend.read(RecordQuery(limit=1))
        return {"backend": type(backend).__name__, "ok": True, "sample_records": len(records)}
    except Exception as error:
        return {"backend": type(backend).__name__, "ok": False, "error": str(error)}


def _checkpoint_write_required(
    current: RunCheckpoint | None, incoming: RunCheckpoint
) -> bool:
    if current is None:
        return True
    if incoming.event_sequence < current.event_sequence:
        raise CheckpointConflictError(f"checkpoint is older: {incoming.checkpoint_id}")
    if incoming.event_sequence == current.event_sequence:
        if incoming.to_dict() != current.to_dict():
            raise CheckpointConflictError(f"checkpoint conflicts: {incoming.checkpoint_id}")
        return False
    return True


def _latest_checkpoint(
    records: list[Record],
) -> tuple[RunCheckpoint | None, Record | None]:
    values = [(RunCheckpoint.from_dict(item.data), item) for item in records]
    if not values:
        return None, None
    latest, record = max(values, key=lambda item: item[0].event_sequence)
    for candidate, _ in values:
        if candidate.event_sequence == latest.event_sequence:
            _checkpoint_write_required(latest, candidate)
    return latest, record


def _checkpoint_event_id(store: EventStore, checkpoint: RunCheckpoint) -> str:
    identity = json.dumps(
        [store.scope.storage_key, store.agent_name,
         checkpoint.checkpoint_id, checkpoint.event_sequence],
        ensure_ascii=False, separators=(",", ":"),
    )
    return f"checkpoint-{hashlib.sha256(identity.encode()).hexdigest()}"


def _delete_checkpoint_records(
    store: EventStore, checkpoint_id: str, event_ids: tuple[str, ...]
) -> None:
    query = RecordQuery(
        user_id=store.user_id, agent_name=store.agent_name,
        scope_key=store.scope.storage_key, stream="checkpoint",
        stream_id=checkpoint_id, event_ids=event_ids,
    )
    store.backend.delete(query)


def _select(records: list[Record], query: RecordQuery) -> list[Record]:
    selected = [record for record in records if query.matches(record)]
    selected.sort(key=lambda item: (item.created_at, item.position, item.event_id), reverse=query.descending)
    return selected if query.limit is None else selected[: query.limit]


def _next_position(records: Iterable[Record], target: Record) -> int:
    positions = [
        item.position
        for item in records
        if (item.scope_key, item.agent_name, item.stream, item.stream_id)
        == (target.scope_key, target.agent_name, target.stream, target.stream_id)
    ]
    return max(positions, default=0) + 1


def _read_jsonl(path: Path) -> list[Record]:
    records: list[Record] = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise TypeError("line is not an object")
                records.append(Record.from_dict(value))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(f"invalid JSONL record at {path}:{number}: {error}") from error
    return records


def _replace_jsonl(path: Path, records: Iterable[Record]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def build_sql_filters(
    query: RecordQuery, placeholder: str
) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    parameters: list[object] = []
    for column, value in (
        ("user_id", query.user_id),
        ("agent_name", query.agent_name),
        ("scope_key", query.scope_key),
        ("stream", query.stream),
        ("stream_id", query.stream_id),
    ):
        if value is not None:
            clauses.append(f"{column}={placeholder}")
            parameters.append(value)
    for column, values in (("event_type", query.event_types), ("event_id", query.event_ids)):
        if values:
            clauses.append(f"{column} IN ({','.join(placeholder for _ in values)})")
            parameters.extend(values)
    if query.before is not None:
        clauses.append(f"created_at<{placeholder}")
        parameters.append(query.before)
    return clauses, parameters


def record_to_sql_row(record: Record) -> tuple[object, ...]:
    return (
        record.event_id,
        record.user_id,
        record.agent_name,
        record.scope_key,
        record.stream,
        record.stream_id,
        record.event_type,
        json.dumps(dict(record.data), ensure_ascii=False, separators=(",", ":")),
        record.created_at,
        record.position,
    )


def record_from_sql_row(row: Iterable[object]) -> Record:
    values = list(row)
    data = json.loads(str(values[7]))
    if not isinstance(data, dict):
        raise ValueError("stored record data must be an object")
    return Record(
        event_id=str(values[0]),
        user_id=str(values[1]),
        agent_name=str(values[2]),
        scope_key=str(values[3]),
        stream=str(values[4]),
        stream_id=str(values[5]),
        event_type=str(values[6]),
        data=data,
        created_at=str(values[8]),
        position=int(values[9]),
    )


# --- 任务快照与操作台账 ---------------------------------------------------------
# 复用已有 EventStore 与 checkpoint 流：不新增存储框架，也不新增记录保留策略。

TASK_STREAM = "checkpoint"
SNAPSHOT_EVENT = "agent_task.snapshot"
LEDGER_EVENT = "agent_task.operation"
# 恢复结果的三种可能；只有"确定未开始"才允许重新执行。
CONFIRMED = "confirmed"
NOT_STARTED = "not_started"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class OperationIdentity:
    """跨重启稳定的逻辑操作身份，不使用会变化的模型 call_id。"""

    agent_name: str
    task_id: str
    purpose: str
    prompt_sha256: str
    criteria: tuple[str, str] = ()

    def __post_init__(self) -> None:
        for name in ("agent_name", "task_id", "purpose"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"operation {name} cannot be empty")
        if len(self.prompt_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.prompt_sha256
        ):
            raise ValueError("operation prompt hash must be a SHA-256 hex digest")

    @property
    def operation_id(self) -> str:
        return task_operation_id(self.agent_name, self.task_id, self.purpose, self.prompt_sha256)

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_name": self.agent_name,
            "task_id": self.task_id,
            "purpose": self.purpose,
            "prompt_sha256": self.prompt_sha256,
            "criteria": list(self.criteria),
            "operation_id": self.operation_id,
        }


@dataclass(frozen=True)
class OperationOutcome:
    """一次外部操作的已知结果。"""

    operation_id: str
    outcome: str
    evidence: str = ""
    recorded_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, str) or not self.operation_id.strip():
            raise ValueError("operation ID cannot be empty")
        if self.outcome not in {CONFIRMED, NOT_STARTED, UNKNOWN}:
            raise ValueError(f"unknown operation outcome: {self.outcome}")
        if self.outcome != NOT_STARTED and not str(self.evidence).strip():
            raise ValueError("confirmed or unknown outcomes require evidence")

    def to_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "outcome": self.outcome,
            "evidence": self.evidence,
            "recorded_at": self.recorded_at or utc_now(),
        }


@dataclass(frozen=True)
class RecoveryDecision:
    """一条持久化任务在恢复时得到的处置；只描述，不执行。"""

    task_id: str
    stage: str
    action: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {"task_id": self.task_id, "stage": self.stage,
                "action": self.action, "reason": self.reason}


def task_operation_id(agent_name: str, task_id: str, purpose: str, prompt_sha256: str) -> str:
    """逻辑操作身份：同一任务、同一目标版本、同一用途算同一次操作。"""
    payload = json.dumps(
        {"agent_name": agent_name, "task_id": task_id, "purpose": purpose,
         "prompt_sha256": prompt_sha256},
        ensure_ascii=False, sort_keys=True,
    )
    return "operation-" + hashlib.sha256(payload.encode()).hexdigest()[:32]


def operation_identity(agent_name: str, task: Mapping[str, object], *,
                       criteria: Sequence[str] = ()) -> OperationIdentity:
    """从持久化快照推导逻辑操作身份；沿用目标版本而不是运行时 call_id。"""
    prompt = task.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        raise ValueError("persisted task is missing its prompt")
    task_id = task.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("persisted task is missing its ID")
    purpose = task.get("purpose")
    return OperationIdentity(
        agent_name=agent_name,
        task_id=task_id,
        purpose=purpose if isinstance(purpose, str) and purpose else "auto",
        prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
        criteria=tuple(str(item) for item in criteria),
    )


class TaskPersistence:
    """按任务保存快照与操作台账；读写失败一律抛出，不静默退回内存。

    ``save_task`` 接受 ``TaskQueue.get_task`` 的投影并补上恢复真正需要的两样东西：
    目标原文与工作目录引用。除此之外它不发明字段——快照仍是任务状态的纯投影。
    """

    def __init__(self, store: EventStore) -> None:
        if not isinstance(store, EventStore):
            raise TypeError("task persistence requires an EventStore")
        self.store = store

    def save_task(self, task: Mapping[str, object], *, agent_name: str,
                  prompt: str | None = None,
                  workspace: str | None = None) -> Mapping[str, object]:
        """把当前任务投影为一条快照记录，不写对话正文。"""
        task_id = _persisted_text(task.get("task_id"), "task ID")
        goal = prompt if prompt is not None else task.get("prompt")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("task snapshot requires the goal prompt text")
        payload = dict(task)
        payload["prompt"] = goal
        if workspace is not None:
            payload["workspace"] = _persisted_text(workspace, "workspace reference")
        snapshot = {
            "agent_name": _persisted_text(agent_name, "agent name"),
            "saved_at": utc_now(),
            "task": _plain_json(payload),
        }
        self.store.replace_state(TASK_STREAM, _task_stream_id(agent_name, task_id),
                                 SNAPSHOT_EVENT, snapshot)
        return snapshot

    def save_tasks(self, queue: object, *, agent_name: str, source_group_id: str) -> int:
        """保存该作用域内全部任务；返回写入条数。"""
        written = 0
        for task in queue.list_tasks(source_group_id, include_results=True):
            self.save_task(task, agent_name=agent_name)
            written += 1
        return written

    def load_task(self, task_id: str, *, agent_name: str) -> Mapping[str, object] | None:
        records = self.store.read(TASK_STREAM, _task_stream_id(agent_name, task_id),
                                  event_types=(SNAPSHOT_EVENT,), limit=1, descending=True)
        if not records:
            return None
        task = records[0].data.get("task")
        if not isinstance(task, Mapping):
            raise ValueError("persisted task snapshot is malformed")
        return task

    def list_tasks(self, *, agent_name: str) -> list[Mapping[str, object]]:
        """列出该 Agent 已保存的任务快照，按任务 ID 排序。"""
        values: dict[str, Mapping[str, object]] = {}
        for record in self.store.read(TASK_STREAM, event_types=(SNAPSHOT_EVENT,)):
            if record.data.get("agent_name") != agent_name:
                continue
            task = record.data.get("task")
            if not isinstance(task, Mapping):
                continue
            task_id = task.get("task_id")
            if isinstance(task_id, str):
                values[task_id] = task
        return [values[key] for key in sorted(values)]

    def record_outcome(self, identity: OperationIdentity, outcome: str,
                       *, evidence: str) -> OperationOutcome:
        """记录一次外部操作的已知结果。"""
        value = OperationOutcome(identity.operation_id, outcome, evidence)
        self.store.append(TASK_STREAM, f"operation:{identity.operation_id}", LEDGER_EVENT,
                          {"identity": identity.to_dict(), **value.to_dict()})
        return value

    def operation_outcomes(self, *, agent_name: str) -> dict[str, OperationOutcome]:
        """读取该 Agent 的操作台账；同一操作以最后一条记录为准。"""
        values: dict[str, OperationOutcome] = {}
        for record in self.store.read(TASK_STREAM, event_types=(LEDGER_EVENT,)):
            identity = record.data.get("identity")
            if not isinstance(identity, Mapping) or identity.get("agent_name") != agent_name:
                continue
            operation_id = record.data.get("operation_id")
            outcome = record.data.get("outcome")
            if not isinstance(operation_id, str) or outcome not in {CONFIRMED, NOT_STARTED, UNKNOWN}:
                continue
            evidence = record.data.get("evidence")
            values[operation_id] = OperationOutcome(
                operation_id, outcome, evidence if isinstance(evidence, str) else "recorded",
                recorded_at=str(record.data.get("recorded_at") or record.created_at),
            )
        return values


def recovery_plan(
    persisted: Sequence[Mapping[str, object]],
    outcomes: Mapping[str, OperationOutcome],
    *,
    agent_name: str,
    current_agent_name: str,
) -> list[RecoveryDecision]:
    """为每条持久化任务给出恢复处置，不执行任何外部操作。

    只承认一位恢复执行者：Agent 身份不一致时明确拒绝，不静默接手别人的任务。
    """
    if agent_name != current_agent_name:
        raise PermissionError(
            f"recovery executor mismatch: snapshot belongs to {agent_name}, current is {current_agent_name}"
        )
    decisions: list[RecoveryDecision] = []
    for task in persisted:
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("persisted task is missing its ID")
        if not isinstance(task.get("prompt"), str) or not task.get("prompt"):
            # 不完整快照不能猜目标，也不能当成可执行任务。
            decisions.append(RecoveryDecision(task_id, str(task.get("stage") or "unknown"), "verify",
                                              "persisted snapshot is missing its goal; restore is blocked"))
            continue
        criteria = task.get("criteria")
        identity = operation_identity(agent_name, task,
                                      criteria=criteria if isinstance(criteria, list) else ())
        decisions.append(_recovery_decision(task, outcomes.get(identity.operation_id)))
    return decisions


def _recovery_decision(task: Mapping[str, object], outcome: OperationOutcome | None) -> RecoveryDecision:
    task_id = str(task.get("task_id"))
    stage = str(task.get("stage") or "pending")
    status = str(task.get("status") or "created")
    if task.get("delivery") is not None:
        # 已交付的任务是可复用证据，不重新执行。
        return RecoveryDecision(task_id, stage, "reuse",
                                "task was already delivered; reuse its recorded result")
    if outcome is None:
        if status in {"running", "queued"}:
            # 无从判断上次执行是否已产生外部效果，必须显式核对后才能继续。
            return RecoveryDecision(task_id, stage, "verify",
                                    "no recorded outcome for an interrupted attempt; verify before rerunning")
        return RecoveryDecision(task_id, stage, "execute",
                                f"task was {status} with no external operation to replay")
    if outcome.outcome == CONFIRMED:
        return RecoveryDecision(task_id, stage, "block",
                                f"external operation already confirmed; do not repeat it ({outcome.evidence})")
    if outcome.outcome == UNKNOWN:
        return RecoveryDecision(task_id, stage, "verify",
                                f"external operation result is unknown; verify at the target system ({outcome.evidence})")
    return RecoveryDecision(task_id, stage, "execute",
                            "external operation was confirmed not to have started")


def restore_task_from_snapshot(queue: object, snapshot: Mapping[str, object], *,
                               source_group_id: str) -> object:
    """把一条持久化快照放回队列；不重放任何外部副作用。"""
    return queue.restore_task(snapshot, source_group_id=source_group_id)


def _task_stream_id(agent_name: str, task_id: str) -> str:
    return f"task:{_persisted_text(agent_name, 'agent name')}:{_persisted_text(task_id, 'task ID')}"


def _persisted_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _plain_json(value: object) -> object:
    """把快照转成纯 JSON 结构，避免共享可变容器。"""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str, sort_keys=True))

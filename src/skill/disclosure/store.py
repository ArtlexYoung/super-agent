from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from skill.disclosure.models import SkillDisclosureEvent, SkillReference

if TYPE_CHECKING:
    from core.run import RunContext


DISCLOSURE_EVENT_SCHEMA_VERSION = 1


class SkillDisclosureStore:
    def __init__(
        self,
        cache_root: Path,
        *,
        run_context: "RunContext | None" = None,
    ) -> None:
        self.cache_root = cache_root
        self.history_path = cache_root / "history.jsonl"
        self.run_context = run_context
        self._sequence = _count_history_lines(self.history_path)

    def write_text(
        self,
        reference: SkillReference | None,
        stage: str,
        path: Path,
        content: str,
    ) -> None:
        digest, cache_hit = _write_bytes_if_changed(path, content.encode("utf-8"))
        self._record(reference, stage, path, digest, cache_hit)

    def write_json(
        self,
        reference: SkillReference | None,
        stage: str,
        path: Path,
        content: dict[str, object],
    ) -> None:
        data = json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        digest, cache_hit = _write_bytes_if_changed(path, data)
        self._record(reference, stage, path, digest, cache_hit)

    def read_content(self, path: str | Path) -> str:
        cache_path = Path(path).expanduser().resolve()
        root = self.cache_root.expanduser().resolve()
        if cache_path != root and root not in cache_path.parents:
            raise ValueError(f"path outside disclosure cache: {path}")
        return cache_path.read_text(encoding="utf-8")

    def read_history(self) -> list[SkillDisclosureEvent]:
        if not self.history_path.exists():
            return []
        return [
            _event_from_dict(json.loads(line))
            for line in self.history_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _record(
        self,
        reference: SkillReference | None,
        stage: str,
        path: Path,
        digest: str,
        cache_hit: bool,
    ) -> None:
        self._sequence += 1
        event = SkillDisclosureEvent(
            schema_version=DISCLOSURE_EVENT_SCHEMA_VERSION,
            sequence=self._sequence,
            created_at=_utc_now_text(),
            run_id="" if self.run_context is None else self.run_context.run_id,
            skill_key="*" if reference is None else reference.key,
            stage=stage,
            cache_path=path,
            content_sha256=digest,
            cache_hit=cache_hit,
        )
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(_event_to_dict(event), ensure_ascii=False, sort_keys=True) + "\n")
            file.flush()
            os.fsync(file.fileno())
        if self.run_context is not None:
            self.run_context.record_event(
                "skill.disclosed",
                {
                    "skill_key": event.skill_key,
                    "stage": stage,
                    "cache_path": str(path),
                    "content_sha256": digest,
                    "cache_hit": cache_hit,
                },
            )


def _write_bytes_if_changed(path: Path, content: bytes) -> tuple[str, bool]:
    # 先比较内容哈希，命中时保留文件时间，便于外部消费者稳定复用缓存路径。
    digest = hashlib.sha256(content).hexdigest()
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
        return digest, True
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)
    return digest, False


def _event_to_dict(event: SkillDisclosureEvent) -> dict[str, object]:
    return {
        "schema_version": event.schema_version,
        "sequence": event.sequence,
        "created_at": event.created_at,
        "run_id": event.run_id,
        "skill_key": event.skill_key,
        "stage": event.stage,
        "cache_path": str(event.cache_path),
        "content_sha256": event.content_sha256,
        "cache_hit": event.cache_hit,
    }


def _event_from_dict(data: object) -> SkillDisclosureEvent:
    if not isinstance(data, dict) or data.get("schema_version") != DISCLOSURE_EVENT_SCHEMA_VERSION:
        raise ValueError("invalid disclosure history event")
    return SkillDisclosureEvent(
        schema_version=DISCLOSURE_EVENT_SCHEMA_VERSION,
        sequence=_required_int(data, "sequence"),
        created_at=_required_string(data, "created_at"),
        run_id=_required_string(data, "run_id"),
        skill_key=_required_string(data, "skill_key"),
        stage=_required_string(data, "stage"),
        cache_path=Path(_required_string(data, "cache_path")),
        content_sha256=_required_string(data, "content_sha256"),
        cache_hit=_required_bool(data, "cache_hit"),
    )


def _required_string(data: dict[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str):
        raise ValueError(f"disclosure event {name} must be a string")
    return value


def _required_int(data: dict[str, object], name: str) -> int:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"disclosure event {name} must be an integer")
    return value


def _required_bool(data: dict[str, object], name: str) -> bool:
    value = data.get(name)
    if not isinstance(value, bool):
        raise ValueError(f"disclosure event {name} must be a boolean")
    return value


def _count_history_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

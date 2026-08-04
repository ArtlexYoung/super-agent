"""Cache and history storage for central progressive Skill disclosure."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from core.models import RunIdentity
from core.files import write_bytes_atomically
from core.state.views import disclosure_history_from_events

if TYPE_CHECKING:
    from core.state.events import EventStore


class RuntimeDisclosureStore:
    """Persist disclosed content only inside one user and Agent cache."""

    def __init__(
        self,
        cache_root: Path,
        store: EventStore,
    ) -> None:
        self.cache_root = cache_root.expanduser().absolute()
        self.history_path = self.cache_root / "history.json"
        self._store = store

    def write_text(
        self,
        identity: RunIdentity | None,
        skill_key: str,
        stage: str,
        path: Path,
        content: str,
    ) -> None:
        self._write_bytes(
            identity,
            skill_key,
            stage,
            path,
            content.encode("utf-8"),
        )

    def write_json(
        self,
        identity: RunIdentity | None,
        skill_key: str,
        stage: str,
        path: Path,
        content: dict[str, object],
    ) -> None:
        self._write_bytes(
            identity,
            skill_key,
            stage,
            path,
            (
                json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            ).encode("utf-8"),
        )

    def read_content(self, path: str | Path) -> str:
        return self._require_cache_path(path).read_text(encoding="utf-8")

    def read_history(self) -> list[dict[str, object]]:
        return disclosure_history_from_events(self._store.read_events())

    def _write_bytes(
        self,
        identity: RunIdentity | None,
        skill_key: str,
        stage: str,
        path: Path,
        content: bytes,
    ) -> None:
        cache_path = self._require_cache_path(path)
        digest = hashlib.sha256(content).hexdigest()
        cache_hit = (
            cache_path.is_file()
            and hashlib.sha256(cache_path.read_bytes()).hexdigest() == digest
        )
        if not cache_hit:
            write_bytes_atomically(cache_path, content)
        data: dict[str, object] = {
            "skill_key": skill_key,
            "stage": stage,
            "cache_path": str(cache_path),
            "content_sha256": digest,
            "cache_hit": cache_hit,
        }
        if identity is None:
            self._store.append_event(
                "disclosure",
                "management",
                "skill.disclosed",
                data=data,
            )
        else:
            self._store.append_run_event(identity, "skill.disclosed", data)
        self.refresh_history()

    def refresh_history(self) -> None:
        """Rewrite the derived history cache from the retained event stream."""
        if not self.cache_root.exists() and not self.history_path.exists():
            return
        write_bytes_atomically(
            self.history_path,
            (
                json.dumps(
                    self.read_history(),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8"),
        )

    def _require_cache_path(self, path: str | Path) -> Path:
        cache_path = Path(path).expanduser().resolve()
        root = self.cache_root.resolve()
        if cache_path != root and root not in cache_path.parents:
            raise ValueError(f"path outside disclosure cache: {path}")
        return cache_path

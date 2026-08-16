"""集中提供有界分页、稳定缓存路径和披露历史。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from core.model import Tool

if TYPE_CHECKING:
    from core.run import ToolContext


RecordEvent = Callable[[str, Mapping[str, object]], object]
MAX_PAGE_CHARACTERS = 20_000


@dataclass(frozen=True)
class DisclosedContent:
    reference: str
    content: str
    offset: int
    next_offset: int | None
    total_characters: int
    cache_path: str
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class _ContentResource:
    reference: str
    content: str
    sha256: str


class DisclosureStore:
    """所有运行内容共用的渐进披露与有界缓存。"""

    def __init__(
        self,
        cache_root: str | Path | None = None,
        *,
        max_entries: int = 128,
        max_content_characters: int = 2_000_000,
        record_event: RecordEvent | None = None,
    ) -> None:
        if max_entries < 1 or max_content_characters < 1:
            raise ValueError("disclosure cache limits must be positive")
        self.cache_root = (
            None if cache_root is None else Path(cache_root).expanduser().resolve()
        )
        self.max_entries = max_entries
        self.max_content_characters = max_content_characters
        self.record_event = record_event
        self._memory: dict[str, _ContentResource] = {}
        self._history: list[dict[str, object]] = []

    def preview(
        self,
        reference: str,
        content: str,
        *,
        offset: int = 0,
        max_characters: int = 4000,
    ) -> DisclosedContent:
        resource = self._resource(reference, content)
        return _page(resource, "", offset, max_characters)

    def disclose(
        self,
        reference: str,
        content: str,
        *,
        offset: int = 0,
        max_characters: int = 4000,
        max_serialized_characters: int | None = None,
    ) -> DisclosedContent:
        resource = self._resource(reference, content)
        cache_path = self._cache_path(resource)
        value = _page_for_serialized_limit(
            resource,
            cache_path,
            offset,
            max_characters,
            max_serialized_characters,
        )
        self._store(resource, cache_path)
        self._remember(value, "content.disclosed")
        return value

    def read(
        self,
        cache_path: str,
        *,
        offset: int = 0,
        max_characters: int = 4000,
    ) -> DisclosedContent:
        resource = self._load(_text(cache_path, "disclosure cache path"))
        value = _page(resource, cache_path, offset, max_characters)
        self._remember(value, "content.cache_read")
        return value

    def history(self) -> tuple[Mapping[str, object], ...]:
        return tuple(MappingProxyType(dict(item)) for item in self._history)

    def tool(self) -> Tool:
        return Tool(
            "read_disclosed_content",
            "Read one bounded page from a disclosed cache path",
            self._read_tool,
            _read_schema(),
        )

    def _read_tool(
        self,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> dict[str, object]:
        value = self.read(
            _text(arguments.get("cache_path"), "disclosure cache path"),
            offset=_integer(arguments.get("offset", 0), "offset", 0, 10_000_000),
            max_characters=_integer(
                arguments.get("max_characters", 4000),
                "max_characters",
                1,
                MAX_PAGE_CHARACTERS,
            ),
        )
        context.emit(
            "content.cache_read",
            {
                "reference": value.reference,
                "cache_path": value.cache_path,
                "offset": value.offset,
                "next_offset": value.next_offset,
                "sha256": value.sha256,
            },
        )
        return value.to_dict()

    def _resource(self, reference: str, content: str) -> _ContentResource:
        selected_reference = _text(reference, "disclosure reference")
        if not isinstance(content, str):
            raise TypeError("disclosed content must be text")
        if len(content) > self.max_content_characters:
            raise ValueError(
                f"disclosed content has {len(content)} characters; "
                f"limit is {self.max_content_characters}"
            )
        return _ContentResource(
            selected_reference,
            content,
            hashlib.sha256(content.encode()).hexdigest(),
        )

    def _cache_path(self, resource: _ContentResource) -> str:
        digest = hashlib.sha256(
            f"{resource.reference}\0{resource.sha256}".encode()
        ).hexdigest()
        if self.cache_root is None:
            return f"memory://{digest}"
        return f"{digest[:2]}/{digest}.json"

    def _store(self, resource: _ContentResource, cache_path: str) -> None:
        if self.cache_root is None:
            self._memory.pop(cache_path, None)
            self._memory[cache_path] = resource
            while len(self._memory) > self.max_entries:
                self._memory.pop(next(iter(self._memory)))
            return
        target = _inside(self.cache_root, self.cache_root / cache_path)
        if not target.is_file():
            _atomic_write(
                target,
                json.dumps(
                    {
                        "reference": resource.reference,
                        "content": resource.content,
                        "sha256": resource.sha256,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode(),
            )
            _prune(self.cache_root, self.max_entries)

    def _load(self, cache_path: str) -> _ContentResource:
        if cache_path.startswith("memory://"):
            try:
                resource = self._memory.pop(cache_path)
            except KeyError as error:
                raise KeyError(f"disclosure cache path not found: {cache_path}") from error
            self._memory[cache_path] = resource
            return resource
        if self.cache_root is None:
            raise RuntimeError("persistent disclosure cache is not configured")
        selected = _inside(self.cache_root, self.cache_root / cache_path)
        if not selected.is_file():
            raise KeyError(f"disclosure cache path not found: {cache_path}")
        value = json.loads(selected.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("cached disclosure must be an object")
        resource = _ContentResource(
            _text(value.get("reference"), "cached disclosure reference"),
            _text_value(value.get("content"), "cached disclosure content"),
            _text(value.get("sha256"), "cached disclosure SHA-256"),
        )
        if hashlib.sha256(resource.content.encode()).hexdigest() != resource.sha256:
            raise ValueError(f"cached disclosure hash does not match: {cache_path}")
        return resource

    def _remember(self, value: DisclosedContent, event_type: str) -> None:
        event = {
            "reference": value.reference,
            "cache_path": value.cache_path,
            "offset": value.offset,
            "next_offset": value.next_offset,
            "sha256": value.sha256,
        }
        self._history.append(event)
        del self._history[:-self.max_entries]
        if self.record_event is not None:
            self.record_event(event_type, event)


def _page(
    resource: _ContentResource,
    cache_path: str,
    offset: int,
    maximum: int,
) -> DisclosedContent:
    if offset < 0 or not 1 <= maximum <= MAX_PAGE_CHARACTERS:
        raise ValueError("disclosure range is outside its limits")
    content = resource.content[offset : offset + maximum]
    if not content and offset != len(resource.content):
        raise ValueError("disclosure offset is outside the content")
    end = offset + len(content)
    return DisclosedContent(
        resource.reference,
        content,
        offset,
        end if end < len(resource.content) else None,
        len(resource.content),
        cache_path,
        resource.sha256,
    )


def _page_for_serialized_limit(
    resource: _ContentResource,
    cache_path: str,
    offset: int,
    maximum: int,
    serialized_limit: int | None,
) -> DisclosedContent:
    if serialized_limit is None:
        return _page(resource, cache_path, offset, maximum)
    if serialized_limit < 1:
        raise RuntimeError("remaining context cannot hold a disclosure reference")
    smallest = _page(resource, cache_path, offset, 1)
    required = _serialized_characters(smallest)
    if required > serialized_limit:
        raise RuntimeError(
            f"disclosure reference needs {required} characters; "
            f"remaining budget is {serialized_limit}"
        )
    low = 1
    high = min(maximum, max(1, len(resource.content) - offset))
    while low < high:
        middle = (low + high + 1) // 2
        if _serialized_characters(_page(resource, cache_path, offset, middle)) <= serialized_limit:
            low = middle
        else:
            high = middle - 1
    return _page(resource, cache_path, offset, low)


def _serialized_characters(value: DisclosedContent) -> int:
    return len(json.dumps(value.to_dict(), ensure_ascii=False, sort_keys=True))


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _prune(root: Path, limit: int) -> None:
    files = sorted(root.rglob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files[limit:]:
        path.unlink(missing_ok=True)


def _inside(root: Path, path: Path) -> Path:
    selected = path.expanduser().resolve()
    if not selected.is_relative_to(root.expanduser().resolve()):
        raise PermissionError(f"path is outside disclosure cache: {path}")
    return selected


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _text_value(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    return value


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _read_schema() -> dict[str, object]:
    return {
        "type": "object",
        "required": ["cache_path"],
        "properties": {
            "cache_path": {"type": "string"},
            "offset": {"type": "integer", "minimum": 0},
            "max_characters": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_PAGE_CHARACTERS,
            },
        },
    }

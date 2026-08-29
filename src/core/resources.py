"""Provide the one progressive disclosure service used by every run."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from core.disclosure import (
    MAX_PAGE_CHARACTERS,
    DisclosedContent,
    DisclosurePage,
    DisclosureStore,
)
from core.model import Tool


RecordEvent = Callable[[str, Mapping[str, object]], object]


class ResourceCenter:
    """Own progressive disclosure, cache paths, and disclosure history."""

    def __init__(
        self,
        store: DisclosureStore | None = None,
        *,
        cache_root: str | Path | None = None,
        max_entries: int = 128,
        record_event: RecordEvent | None = None,
    ) -> None:
        if store is not None and any(
            value is not None for value in (cache_root, record_event)
        ):
            raise ValueError("a supplied disclosure store cannot be reconfigured")
        self.store = store or DisclosureStore(
            cache_root,
            max_entries=max_entries,
            record_event=record_event,
        )

    def preview_resource(
        self,
        reference: str,
        content: str,
        *,
        offset: int = 0,
        max_characters: int = 4000,
    ) -> DisclosedContent:
        """Return a bounded page without creating a persistent cache entry."""
        return self.store.preview(
            reference, content, offset=offset, max_characters=max_characters
        )

    def disclose_resource(
        self,
        reference: str,
        content: str,
        *,
        offset: int = 0,
        max_characters: int = 4000,
        max_serialized_characters: int | None = None,
    ) -> DisclosedContent:
        """Cache a resource and return its stable path and bounded page."""
        return self.store.disclose(
            reference,
            content,
            offset=offset,
            max_characters=max_characters,
            max_serialized_characters=max_serialized_characters,
        )

    def read_cached_resource(
        self,
        cache_path: str,
        *,
        offset: int = 0,
        max_characters: int = 4000,
    ) -> DisclosedContent:
        """Read a page from a previously returned cache path."""
        return self.store.read(
            cache_path, offset=offset, max_characters=max_characters
        )

    def read_history(self) -> tuple[Mapping[str, object], ...]:
        """Return disclosure history metadata."""
        return self.store.history()

    def create_read_tool(self) -> Tool:
        """Create a reader for this center's cache paths."""
        return self.store.tool()


__all__ = [
    "MAX_PAGE_CHARACTERS",
    "DisclosedContent",
    "DisclosurePage",
    "ResourceCenter",
]

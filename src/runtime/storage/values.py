"""Shared validation and JSON encoding for storage backend values."""

from __future__ import annotations

import json
from datetime import UTC, datetime


def clean_storage_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"storage event {name} cannot be empty")
    return value.strip()


def positive_storage_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"storage event {name} must be a positive integer")
    return value


def encode_storage_data(data: dict[str, object]) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def decode_storage_data(text: str, location: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid storage event data at {location}") from error
    if not isinstance(value, dict):
        raise ValueError(f"storage event data must be an object at {location}")
    return dict(value)


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

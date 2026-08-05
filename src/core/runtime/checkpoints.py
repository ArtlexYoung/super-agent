"""Small, content-free checkpoint records for explicit task resumption."""

from __future__ import annotations

import hashlib
import json
from typing import Iterable
from uuid import uuid4

from core.state.models import RunEvent


CHECKPOINT_STATE_BYTES = 16_384


def create_checkpoint_data(
    run_id: str,
    label: str,
    facts: dict[str, object],
) -> dict[str, object]:
    clean_label = label.strip()
    if not clean_label:
        raise ValueError("checkpoint label cannot be empty")
    if not isinstance(facts, dict):
        raise TypeError("checkpoint facts must be a dictionary")
    try:
        encoded = json.dumps(
            facts,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("checkpoint facts must be JSON-compatible") from error
    if len(encoded) > CHECKPOINT_STATE_BYTES:
        raise ValueError("checkpoint facts exceed 16384 bytes")
    return {
        "checkpoint_id": f"checkpoint-{uuid4().hex}",
        "run_id": run_id,
        "label": clean_label,
        "state_sha256": hashlib.sha256(encoded).hexdigest(),
        "state_keys": sorted(str(key) for key in facts),
    }


def list_checkpoint_data(events: Iterable[RunEvent]) -> list[dict[str, object]]:
    return [
        dict(event.data)
        for event in events
        if event.event_type == "run.checkpoint.created"
    ]


def find_checkpoint_data(
    events: Iterable[RunEvent],
    checkpoint_id: str | None = None,
) -> dict[str, object]:
    checkpoints = list_checkpoint_data(events)
    if not checkpoints:
        raise KeyError("run has no checkpoints")
    if checkpoint_id is None:
        return checkpoints[-1]
    selected = next(
        (
            item
            for item in checkpoints
            if item.get("checkpoint_id") == checkpoint_id.strip()
        ),
        None,
    )
    if selected is None:
        raise KeyError(f"checkpoint not found: {checkpoint_id}")
    return selected


def hash_checkpoint_value(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

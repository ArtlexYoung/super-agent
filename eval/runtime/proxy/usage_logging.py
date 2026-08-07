"""Authenticated evaluation context and provider-usage logging."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException


@dataclass(frozen=True)
class RequestContext:
    run_id: str
    dataset: str
    agent: str
    task_key: str


def decode_request_context(supplied: str, configured: str) -> RequestContext | None:
    if hmac.compare_digest(supplied, configured):
        return None
    prefix = f"{configured}."
    if not supplied.startswith(prefix):
        raise HTTPException(status_code=401, detail={"error": {"message": "Unauthorized"}})
    try:
        _, encoded, signature = supplied.rsplit(".", 2)
        expected = hmac.new(
            configured.encode("utf-8"), encoded.encode("ascii"), "sha256"
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        values = {
            key: payload[key]
            for key in ("run_id", "dataset", "agent", "task_key")
        }
        if not all(isinstance(value, str) and value for value in values.values()):
            raise ValueError("invalid context")
    except (
        KeyError,
        ValueError,
        TypeError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ):
        raise HTTPException(status_code=401, detail={"error": {"message": "Unauthorized"}})
    return RequestContext(**values)


def record_usage(
    context: RequestContext | None,
    endpoint: str,
    usage: dict[str, Any],
    path: Path | None,
    model: str,
) -> None:
    if context is None or path is None:
        return
    entry = {
        "created_at": datetime.now(UTC).isoformat(),
        "run_id": context.run_id,
        "dataset": context.dataset,
        "agent": context.agent,
        "task_key": context.task_key,
        "endpoint": endpoint,
        "model": model,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as destination:
        destination.write(json.dumps(entry, ensure_ascii=False) + "\n")
    path.chmod(0o600)

"""Small local-file helpers for rebuildable Runtime artifacts."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import uuid4

def create_scope_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]

def write_bytes_atomically(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

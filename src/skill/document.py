"""定义被动 Skill 文档及其唯一 Markdown/TOML 格式。"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType


NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


@dataclass(frozen=True)
class Skill:
    """一个开放类型、可分类且只承载内容的 Skill。"""

    name: str
    skill_type: str
    description: str
    body: str
    path: Path
    categories: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    optional_tools: tuple[str, ...] = ()
    includes: tuple[str, ...] = ()
    version: str = "0.1.0"
    created_by: str = "user"
    agent_can_update: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)
    sha256: str = ""

    def __post_init__(self) -> None:
        _validate_name(self.name, "skill name")
        _validate_name(self.skill_type, "skill type")
        if not self.description.strip() or not self.body.strip():
            raise ValueError("skill description and body cannot be empty")
        if self.created_by not in {"builtin", "user", "agent"}:
            raise ValueError("skill created_by must be builtin, user, or agent")

    @property
    def key(self) -> str:
        return f"{self.skill_type}:{self.name}"

    def index_entry(self) -> dict[str, object]:
        return {
            "key": self.key,
            "description": self.description,
            "categories": list(self.categories),
            "requires": list(self.requires),
            "optional_tools": list(self.optional_tools),
            "version": self.version,
        }


def parse_skill_text(text: str, path: Path) -> Skill:
    metadata, body = _split_front_matter(text)
    digest = hashlib.sha256(text.encode()).hexdigest()
    return Skill(
        name=_required_text(metadata.get("name"), "skill name"),
        skill_type=_required_text(metadata.get("type", "prompt"), "skill type"),
        description=_required_text(metadata.get("description"), "skill description"),
        body=body.strip(),
        path=path.resolve(),
        categories=_text_array(metadata.get("categories", []), "skill categories"),
        requires=_text_array(metadata.get("requires", []), "skill requires"),
        optional_tools=_text_array(metadata.get("optional_tools", []), "skill optional_tools"),
        includes=_text_array(metadata.get("includes", []), "skill includes"),
        version=_required_text(metadata.get("version", "0.1.0"), "skill version"),
        created_by=_required_text(metadata.get("created_by", "user"), "skill created_by"),
        agent_can_update=_boolean(metadata.get("agent_can_update", False), "skill agent_can_update"),
        metadata=MappingProxyType(dict(metadata)),
        sha256=digest,
    )


def format_skill(metadata: Mapping[str, object], body: str) -> str:
    """生成简单且可重复读取的 TOML front matter。"""
    lines = ["+++"]
    preferred = ("name", "type", "description", "version", "created_by", "agent_can_update")
    for key in preferred:
        if key in metadata:
            lines.append(f"{key} = {_toml_value(metadata[key])}")
    arrays = {"categories", "requires", "optional_tools", "includes"}
    for key in ("categories", "requires", "optional_tools", "includes"):
        if key in metadata and metadata[key]:
            lines.append(f"{key} = {_toml_value(metadata[key])}")
    for key in sorted(set(metadata) - set(preferred) - arrays):
        lines.append(f"{key} = {_toml_value(metadata[key])}")
    return "\n".join((*lines, "+++", body.strip(), ""))


def _split_front_matter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("+++\n"):
        raise ValueError("skill must start with TOML front matter")
    end = text.find("\n+++\n", 4)
    if end < 0:
        raise ValueError("skill TOML front matter is not closed")
    metadata = tomllib.loads(text[4:end])
    if not isinstance(metadata, dict):
        raise ValueError("skill front matter must be an object")
    return metadata, text[end + 5 :]


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, Mapping):
        return "{" + ", ".join(f"{key} = {_toml_value(item)}" for key, item in value.items()) + "}"
    raise TypeError(f"unsupported Skill metadata value: {type(value).__name__}")


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _text_array(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{name} must be an array of non-empty text")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _validate_name(value: str, name: str) -> None:
    if not NAME_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must use lowercase letters, numbers, '-' or '_'")

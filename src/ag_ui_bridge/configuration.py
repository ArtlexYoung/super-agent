"""Validated, canonical agent.toml updates for the web interface."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

from runtime.config import AgentConfig, AgentSettings
from runtime.storage.files import write_bytes_atomically


@dataclass(frozen=True)
class AgentConfigurationInput:
    name: str
    system: str
    workflow: str
    memory: str
    skills: list[str]
    max_agent_chain_depth: int | None
    use_features: list[str]
    disable_names: list[str]
    safety: str

    @classmethod
    def from_dict(cls, value: object) -> "AgentConfigurationInput":
        if not isinstance(value, dict):
            raise ValueError("agent configuration must be a JSON object")
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError("unknown agent configuration fields: " + ", ".join(unknown))
        return cls(
            name=_required_text(value.get("name"), "name"),
            system=_required_text(value.get("system"), "system"),
            workflow=_skill_name(value.get("workflow"), "workflow"),
            memory=_skill_name(value.get("memory"), "memory"),
            skills=_text_list(value.get("skills", []), "skills"),
            max_agent_chain_depth=_optional_depth(value.get("max_agent_chain_depth")),
            use_features=_text_list(value.get("use_features", []), "use_features"),
            disable_names=_text_list(value.get("disable_names", []), "disable_names"),
            safety=_safety_preset(value.get("safety")),
        )


def update_agent_configuration(
    config: AgentConfig,
    request: AgentConfigurationInput,
) -> AgentConfig:
    updated = replace(
        config,
        agent=AgentSettings(
            name=request.name,
            system=request.system,
            workflow=request.workflow,
            memory=request.memory,
            skills=request.skills,
            max_agent_chain_depth=request.max_agent_chain_depth,
            use_features=request.use_features,
            disable_names=request.disable_names,
            safety=request.safety,
        ),
    )
    content = _agent_config_to_toml(updated)
    write_bytes_atomically(config.source, content.encode("utf-8"))
    return AgentConfig.load_from_file(config.source)


def agent_configuration_to_dict(config: AgentConfig) -> dict[str, object]:
    settings = config.agent
    return {
        "name": settings.name,
        "system": settings.system,
        "workflow": settings.workflow,
        "memory": settings.memory,
        "skills": list(settings.skills),
        "max_agent_chain_depth": settings.max_agent_chain_depth,
        "use_features": list(settings.use_features),
        "disable_names": list(settings.disable_names),
        "safety": settings.safety,
    }


def _agent_config_to_toml(config: AgentConfig) -> str:
    agent = config.agent
    base = config.source.parent
    lines = [
        "[agent]",
        f"name = {_toml_string(agent.name)}",
        f"system = {_toml_string(agent.system)}",
        f"workflow = {_toml_string(agent.workflow)}",
        f"memory = {_toml_string(agent.memory)}",
        f"skills = {_toml_array(agent.skills)}",
    ]
    if agent.max_agent_chain_depth is not None:
        lines.append(f"max_agent_chain_depth = {agent.max_agent_chain_depth}")
    lines.extend(
        [
            f"use_features = {_toml_array(agent.use_features)}",
            f"disable_names = {_toml_array(agent.disable_names)}",
            f"safety = {_toml_string(agent.safety)}",
            "",
            "[paths]",
            f"skills = {_toml_array([_portable_path(path, base) for path in config.paths.skills])}",
            "",
            "[storage]",
            f"backend = {_toml_string(config.storage.backend)}",
            f"path = {_toml_string(_portable_path(config.storage.path, base))}",
        ]
    )
    if config.storage.url_env is not None:
        lines.append(f"url_env = {_toml_string(config.storage.url_env)}")
    return "\n".join(lines) + "\n"


def _portable_path(path: Path, base: Path) -> str:
    try:
        relative = path.resolve().relative_to(base.resolve())
    except ValueError:
        return str(path.resolve())
    return str(relative) or "."


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"agent configuration {name} must be a non-empty string")
    return value.strip()


def _skill_name(value: object, name: str) -> str:
    text = _required_text(value, name).lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", text):
        raise ValueError(f"agent configuration {name} must be a Skill name")
    return text


def _text_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"agent configuration {name} must be a string array")
    items = [item.strip().lower() for item in value]
    if any(not item for item in items) or len(items) != len(set(items)):
        raise ValueError(f"agent configuration {name} must contain unique values")
    return items


def _optional_depth(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("max_agent_chain_depth must be a positive integer or null")
    return value


def _safety_preset(value: object) -> str:
    text = _required_text(value, "safety").lower()
    if text not in {"standard", "read_only", "autonomous"}:
        raise ValueError("web configuration safety must be standard, read_only, or autonomous")
    return text

"""Validated, canonical common.toml updates for the web interface."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from core.config import CommonConfig, AgentSettings
from core.files import write_bytes_atomically

@dataclass(frozen=True)
class CommonConfigurationInput:
    name: str
    system: str
    skills: list[str]
    max_agent_chain_depth: int | None
    disabled_skills: list[str]

    @classmethod
    def from_dict(cls, value: object) -> "CommonConfigurationInput":
        if not isinstance(value, dict):
            raise ValueError("agent configuration must be a JSON object")
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError("unknown agent configuration fields: " + ", ".join(unknown))
        return cls(
            name=_required_text(value.get("name"), "name"),
            system=_required_text(value.get("system"), "system"),
            skills=_text_list(value.get("skills", []), "skills"),
            max_agent_chain_depth=_optional_depth(value.get("max_agent_chain_depth")),
            disabled_skills=_text_list(
                value.get("disabled_skills", []),
                "disabled_skills",
            ),
        )

def update_common_configuration(
    config: CommonConfig,
    request: CommonConfigurationInput,
) -> CommonConfig:
    updated = replace(
        config,
        agent=AgentSettings(
            name=request.name,
            system=request.system,
            skills=request.skills,
            max_agent_chain_depth=request.max_agent_chain_depth,
            disabled_skills=request.disabled_skills,
        ),
    )
    content = _common_config_to_toml(updated)
    write_bytes_atomically(config.source, content.encode("utf-8"))
    return CommonConfig.load_from_file(config.source)

def common_configuration_to_dict(config: CommonConfig) -> dict[str, object]:
    settings = config.agent
    return {
        "name": settings.name,
        "system": settings.system,
        "skills": list(settings.skills),
        "max_agent_chain_depth": settings.max_agent_chain_depth,
        "disabled_skills": list(settings.disabled_skills),
    }

def _common_config_to_toml(config: CommonConfig) -> str:
    agent = config.agent
    base = config.source.parent
    lines = [
        "schema_version = 1",
        'kind = "common"',
        "",
        "[agent]",
        f"name = {_toml_string(agent.name)}",
        f"system = {_toml_string(agent.system)}",
        f"skills = {_toml_array(agent.skills)}",
    ]
    if agent.max_agent_chain_depth is not None:
        lines.append(f"max_agent_chain_depth = {agent.max_agent_chain_depth}")
    lines.extend(
        [
            f"disabled_skills = {_toml_array(agent.disabled_skills)}",
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
    lines.extend(
        [
            "",
            "[storage.audit]",
            f"detailed_days = {config.storage.audit.detailed_days}",
            f"critical_days = {config.storage.audit.critical_days}",
        ]
    )
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

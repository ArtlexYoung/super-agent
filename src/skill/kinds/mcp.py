"""Passive MCP Skill settings read through progressive disclosure."""

from __future__ import annotations

from dataclasses import dataclass

from skill.disclosure import SkillDisclosure


MCP_CONFIGURATION_FIELDS = {"server"}


@dataclass(frozen=True)
class McpSkillSettings:
    server_name: str


def read_mcp_skill_settings(disclosure: SkillDisclosure) -> McpSkillSettings:
    manifest = disclosure.read_manifest()
    if manifest.skill_type != "mcp":
        raise ValueError(f"Skill is not an MCP Skill: {manifest.name}")
    configuration = disclosure.read_configuration().content
    unknown = set(configuration) - MCP_CONFIGURATION_FIELDS
    if unknown:
        raise ValueError(
            "unknown MCP Skill settings: " + ", ".join(sorted(unknown))
        )
    value = configuration.get("server", manifest.name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("MCP Skill configuration.server must be a non-empty string")
    return McpSkillSettings(value.strip().lower())

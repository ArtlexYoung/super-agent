from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class McpServer:
    name: str
    description: str
    version: str
    triggers: list[str]
    transport: str
    command: str
    args: list[str]
    env: dict[str, str]
    path: Path

    @classmethod
    def load_from_file(cls, path: Path) -> "McpServer":
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        mcp_data = _read_mcp_section(data, path)
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError(f"mcp manifest missing name: {path}")
        return cls(
            name=name,
            description=str(data.get("description", "")),
            version=str(data.get("version", "0.1.0")),
            triggers=[str(item).lower() for item in data.get("triggers", [])],
            transport=str(mcp_data.get("transport", "stdio")),
            command=str(mcp_data.get("command", "")),
            args=[str(item) for item in mcp_data.get("args", [])],
            env=_read_env_names(mcp_data.get("env", {})),
            path=path.parent,
        )

    def build_skill_instructions(self) -> str:
        lines = [
            "MCP server skill:",
            f"Name: {self.name}",
            f"Description: {self.description}",
            "Protocol: mcp",
            f"Transport: {self.transport}",
        ]
        command = " ".join([self.command, *self.args]).strip()
        if command:
            lines.append(f"Command: {command}")
        if self.env:
            lines.append("Environment variables: " + ", ".join(sorted(self.env)))
        return "\n".join(line for line in lines if line)


def _read_mcp_section(data: dict[str, Any], path: Path) -> dict[str, Any]:
    value = data.get("mcp")
    if not isinstance(value, dict):
        raise ValueError(f"mcp skill manifest missing [mcp]: {path}")
    return value


def _read_env_names(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): "" for key in value}

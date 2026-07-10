from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from core.provider import ChatProvider, Message
from skill.freshness import DEFAULT_FRESHNESS
from skill.loader import SkillLoader
from skill.manifest import SKILL_SCHEMA_VERSION, SkillManifest


SKILL_INSTRUCTION_FILE = "SKILL.md"


@dataclass(frozen=True)
class SkillWriteRequest:
    name: str
    instructions: str
    description: str = ""
    kind: str = "prompt"
    triggers: list[str] | None = None
    version: str = "0.1.0"
    agent_created: bool = True
    agent_can_update: bool = True
    freshness: float = DEFAULT_FRESHNESS
    function_group: str = ""
    freshness_updated_at: str = ""


@dataclass(frozen=True)
class SkillUpdateRequest:
    name: str
    instructions: str | None = None
    description: str | None = None
    triggers: list[str] | None = None
    version: str | None = None
    agent_can_update: bool | None = None
    freshness: float | None = None
    function_group: str | None = None
    freshness_updated_at: str | None = None


def create_agent_skill(skill_root: Path, request: SkillWriteRequest) -> SkillManifest:
    skill_name = _clean_skill_name(request.name)
    instructions = _clean_instructions(request.instructions)
    skill_dir = skill_root.expanduser() / skill_name
    if skill_dir.exists():
        raise FileExistsError(f"skill already exists: {skill_name}")
    skill_dir.mkdir(parents=True, exist_ok=False)
    _write_skill_files(
        skill_dir,
        SkillWriteRequest(
            name=skill_name,
            instructions=instructions,
            description=request.description,
            kind=request.kind,
            triggers=request.triggers,
            version=request.version,
            agent_created=request.agent_created,
            agent_can_update=request.agent_can_update,
            freshness=request.freshness,
            function_group=request.function_group or skill_name,
            freshness_updated_at=request.freshness_updated_at,
        ),
    )
    return SkillManifest.load_from_file(skill_dir / "skill.toml")


def update_agent_skill(loader: SkillLoader, request: SkillUpdateRequest) -> SkillManifest:
    manifest = loader.find_skill_manifest(request.name)
    if manifest is None:
        raise KeyError(f"skill not found: {request.name}")
    if manifest.kind != "prompt":
        raise PermissionError(f"only prompt skill files can be updated by agent: {request.name}")
    if not manifest.agent_can_update:
        raise PermissionError(f"skill does not allow agent update: {request.name}")

    current_skill = loader.load_skill(manifest.name)
    instructions = current_skill.instructions
    if request.instructions is not None:
        instructions = _clean_instructions(request.instructions)
    write_request = SkillWriteRequest(
        name=manifest.name,
        instructions=instructions,
        description=manifest.description if request.description is None else request.description,
        kind=manifest.kind,
        triggers=manifest.triggers if request.triggers is None else request.triggers,
        version=manifest.version if request.version is None else request.version,
        agent_created=manifest.agent_created,
        agent_can_update=manifest.agent_can_update
        if request.agent_can_update is None
        else request.agent_can_update,
        freshness=manifest.freshness if request.freshness is None else request.freshness,
        function_group=manifest.function_group if request.function_group is None else request.function_group,
        freshness_updated_at=manifest.freshness_updated_at
        if request.freshness_updated_at is None
        else request.freshness_updated_at,
    )
    _write_skill_files(manifest.path, write_request)
    return SkillManifest.load_from_file(manifest.path / "skill.toml")


def optimize_agent_skill(
    loader: SkillLoader,
    provider: ChatProvider,
    *,
    model: str,
    name: str,
    goal: str,
) -> SkillManifest:
    skill = loader.load_skill(name)
    messages = _build_skill_optimization_messages(
        name=skill.manifest.name,
        description=skill.manifest.description,
        instructions=skill.instructions,
        goal=goal,
    )
    improved = provider.send_chat_messages(messages, model).strip()
    return update_agent_skill(loader, SkillUpdateRequest(name=name, instructions=improved))


def _write_skill_files(skill_dir: Path, request: SkillWriteRequest) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.toml").write_text(_build_skill_manifest_text(request), encoding="utf-8")
    (skill_dir / SKILL_INSTRUCTION_FILE).write_text(request.instructions.rstrip() + "\n", encoding="utf-8")


def _build_skill_manifest_text(request: SkillWriteRequest) -> str:
    return "\n".join(
        [
            f"schema_version = {SKILL_SCHEMA_VERSION}",
            f"name = {_quote_toml_string(request.name)}",
            f"kind = {_quote_toml_string(request.kind)}",
            f"description = {_quote_toml_string(request.description)}",
            f"version = {_quote_toml_string(request.version)}",
            f"agent_created = {_quote_toml_bool(request.agent_created)}",
            f"agent_can_update = {_quote_toml_bool(request.agent_can_update)}",
            f"freshness = {_quote_toml_number(request.freshness)}",
            f"function_group = {_quote_toml_string(request.function_group or request.name)}",
            *_optional_freshness_updated_at_line(request.freshness_updated_at),
            f"triggers = {_quote_toml_string_array(request.triggers or [])}",
            "",
            "[entry]",
            f"instructions = {_quote_toml_string(SKILL_INSTRUCTION_FILE)}",
            "",
        ]
    )


def _build_skill_optimization_messages(
    *,
    name: str,
    description: str,
    instructions: str,
    goal: str,
) -> list[Message]:
    return [
        {
            "role": "system",
            "content": "You improve one agent skill. Return only the complete new SKILL.md content.",
        },
        {
            "role": "user",
            "content": (
                f"Skill name: {name}\n"
                f"Description: {description}\n"
                f"Optimization goal: {goal}\n\n"
                f"Current SKILL.md:\n{instructions}"
            ),
        },
    ]


def _clean_skill_name(name: str) -> str:
    value = name.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
        raise ValueError("skill name must use lowercase letters, numbers, '-' or '_'")
    return value


def _clean_instructions(instructions: str) -> str:
    value = instructions.strip()
    if not value:
        raise ValueError("skill instructions cannot be empty")
    return value


def _quote_toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _quote_toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _quote_toml_number(value: float) -> str:
    return str(round(float(value), 2)).rstrip("0").rstrip(".")


def _quote_toml_string_array(values: list[str]) -> str:
    return "[" + ", ".join(_quote_toml_string(value.lower()) for value in values) + "]"


def _optional_freshness_updated_at_line(value: str) -> list[str]:
    return [f"freshness_updated_at = {_quote_toml_string(value)}"] if value else []

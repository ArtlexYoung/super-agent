from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from super_agent.core.provider import ChatProvider, Message
from super_agent.skill import SkillLoader, SkillManifest


SKILL_INSTRUCTION_FILE = "SKILL.md"


@dataclass(frozen=True)
class SkillWriteRequest:
    name: str
    instructions: str
    description: str = ""
    triggers: list[str] | None = None
    version: str = "0.1.0"
    agent_created: bool = True
    agent_can_update: bool = True


@dataclass(frozen=True)
class SkillUpdateRequest:
    name: str
    instructions: str | None = None
    description: str | None = None
    triggers: list[str] | None = None
    version: str | None = None
    agent_can_update: bool | None = None


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
            triggers=request.triggers,
            version=request.version,
            agent_created=request.agent_created,
            agent_can_update=request.agent_can_update,
        ),
    )
    return SkillManifest.load_from_file(skill_dir / "skill.toml")


def update_agent_skill(loader: SkillLoader, request: SkillUpdateRequest) -> SkillManifest:
    manifest = loader.find_skill_manifest(request.name)
    if manifest is None:
        raise KeyError(f"skill not found: {request.name}")
    if manifest.kind != "skill":
        raise PermissionError(f"only normal skill files can be updated by agent: {request.name}")
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
        triggers=manifest.triggers if request.triggers is None else request.triggers,
        version=manifest.version if request.version is None else request.version,
        agent_created=manifest.agent_created,
        agent_can_update=manifest.agent_can_update
        if request.agent_can_update is None
        else request.agent_can_update,
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
            f"name = {_quote_toml_string(request.name)}",
            f"description = {_quote_toml_string(request.description)}",
            f"version = {_quote_toml_string(request.version)}",
            f"agent_created = {_quote_toml_bool(request.agent_created)}",
            f"agent_can_update = {_quote_toml_bool(request.agent_can_update)}",
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


def _quote_toml_string_array(values: list[str]) -> str:
    return "[" + ", ".join(_quote_toml_string(value.lower()) for value in values) + "]"

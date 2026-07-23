from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from provider.chat import ChatProvider, Message
from skill.disclosure import ProgressiveDisclosureCore, SkillDisclosure
from skill.manifest import SKILL_SCHEMA_VERSION, SkillManifest, calculate_skill_directory_sha256


@dataclass(frozen=True)
class SkillCandidate:
    candidate_id: str
    name: str
    goal: str
    parent_version: str
    proposed_version: str
    parent_sha256: str
    candidate_sha256: str
    created_at: str
    skill_path: Path
    metadata_path: Path


def create_candidate(
    *,
    skill_disclosure: ProgressiveDisclosureCore,
    candidate_root: Path,
    provider: ChatProvider,
    model: str,
    name: str,
    goal: str,
) -> SkillCandidate:
    skill_name = clean_skill_name(name)
    evolution_goal = goal.strip()
    if not evolution_goal:
        raise ValueError("skill evolution goal cannot be empty")
    index = skill_disclosure.prepare_skill_index()
    current_entry = index.find_skill(skill_name)
    current_disclosure = None
    current = None
    if current_entry is not None:
        current_disclosure = skill_disclosure.open_skill(
            current_entry.reference.name,
            current_entry.reference.capability,
        )
        current = current_disclosure.read_manifest()
    if current is not None and not current.agent_can_update:
        raise PermissionError(f"skill does not allow agent evolution: {skill_name}")
    current_instructions = _read_current_instructions(current_disclosure)
    proposed_instructions = provider.send_chat_messages(
        _build_candidate_messages(skill_name, evolution_goal, current, current_instructions),
        model,
    ).strip()
    if not proposed_instructions:
        raise ValueError("model returned empty skill candidate instructions")

    candidate_id = f"{skill_name}-{uuid4().hex[:12]}"
    candidate_dir = candidate_root / candidate_id
    skill_path = candidate_dir / "skill"
    parent_version = "" if current is None else current.version
    proposed_version = "0.1.0" if current is None else increment_patch_version(current.version)
    parent_sha256 = "" if current is None else calculate_skill_directory_sha256(current.path)
    _write_candidate_skill(
        skill_path,
        current=current,
        name=skill_name,
        goal=evolution_goal,
        instructions=proposed_instructions,
        version=proposed_version,
    )
    _validate_candidate_skill(skill_path)
    candidate = SkillCandidate(
        candidate_id=candidate_id,
        name=skill_name,
        goal=evolution_goal,
        parent_version=parent_version,
        proposed_version=proposed_version,
        parent_sha256=parent_sha256,
        candidate_sha256=calculate_skill_directory_sha256(skill_path),
        created_at=_utc_now_text(),
        skill_path=skill_path,
        metadata_path=candidate_dir / "candidate.json",
    )
    _write_candidate_metadata(candidate)
    return candidate


def load_candidate(candidate_root: Path, candidate_id: str) -> SkillCandidate:
    clean_record_id(candidate_id)
    metadata_path = candidate_root / candidate_id / "candidate.json"
    if not metadata_path.is_file():
        raise KeyError(f"skill candidate not found: {candidate_id}")
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError(f"invalid skill candidate metadata: {candidate_id}")
    stored_id = str(data.get("candidate_id", ""))
    if stored_id != candidate_id:
        raise ValueError(f"skill candidate metadata id does not match directory: {candidate_id}")
    name = clean_skill_name(str(data.get("name", "")))
    return SkillCandidate(
        candidate_id=stored_id,
        name=name,
        goal=str(data["goal"]),
        parent_version=str(data["parent_version"]),
        proposed_version=str(data["proposed_version"]),
        parent_sha256=_read_sha256(data, "parent_sha256", allow_empty=True),
        candidate_sha256=_read_sha256(data, "candidate_sha256"),
        created_at=str(data["created_at"]),
        skill_path=metadata_path.parent / "skill",
        metadata_path=metadata_path,
    )


def verify_candidate_files(candidate: SkillCandidate) -> None:
    actual = calculate_skill_directory_sha256(candidate.skill_path)
    if actual != candidate.candidate_sha256:
        raise ValueError(f"skill candidate files changed after proposal: {candidate.candidate_id}")


def resolve_skill_file(skill_path: Path, relative_path: str) -> Path:
    root = skill_path.resolve()
    path = (skill_path / relative_path).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"skill file must stay inside its directory: {relative_path}")
    return path


def clean_skill_name(name: str) -> str:
    value = name.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
        raise ValueError("skill name must use lowercase letters, numbers, '-' or '_'")
    return value


def clean_record_id(record_id: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", record_id):
        raise ValueError("invalid evolution record id")
    return record_id


def _read_sha256(data: dict[str, object], name: str, *, allow_empty: bool = False) -> str:
    value = str(data.get(name, ""))
    if allow_empty and not value:
        return ""
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"skill candidate {name} must be a SHA-256 value")
    return value


def increment_patch_version(version: str) -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version.strip())
    if match is None:
        raise ValueError(f"skill version must use major.minor.patch: {version}")
    major, minor, patch = (int(value) for value in match.groups())
    return f"{major}.{minor}.{patch + 1}"


def _write_candidate_skill(
    skill_path: Path,
    *,
    current: SkillManifest | None,
    name: str,
    goal: str,
    instructions: str,
    version: str,
) -> None:
    if skill_path.parent.exists():
        raise FileExistsError(f"candidate directory already exists: {skill_path.parent}")
    if current is None:
        skill_path.mkdir(parents=True)
        (skill_path / "skill.toml").write_text(
            _new_skill_manifest_text(name, goal, version),
            encoding="utf-8",
        )
        instruction_path = resolve_skill_file(skill_path, "SKILL.md")
    else:
        shutil.copytree(current.path, skill_path)
        _set_manifest_version(skill_path / "skill.toml", version)
        instruction_path = resolve_skill_file(skill_path, current.entry.instructions)
        instruction_path.parent.mkdir(parents=True, exist_ok=True)
    instruction_path.write_text(instructions.rstrip() + "\n", encoding="utf-8")


def _read_current_instructions(disclosure: SkillDisclosure | None) -> str:
    if disclosure is None:
        return ""
    return disclosure.read_instructions().content


def _build_candidate_messages(
    name: str,
    goal: str,
    manifest: SkillManifest | None,
    instructions: str,
) -> list[Message]:
    action = "Create" if manifest is None else "Improve"
    current = "No active version exists." if manifest is None else f"Current SKILL.md:\n{instructions}"
    return [
        {
            "role": "system",
            "content": (
                f"{action} one reusable agent Skill. Return only the complete new SKILL.md content. "
                "Do not include Markdown fences or commentary."
            ),
        },
        {
            "role": "user",
            "content": f"Skill: {name}\nEvolution goal: {goal}\n\n{current}",
        },
    ]


def _new_skill_manifest_text(name: str, goal: str, version: str) -> str:
    return "\n".join(
        [
            f"schema_version = {SKILL_SCHEMA_VERSION}",
            f"name = {json.dumps(name)}",
            'capability = "prompt"',
            f"description = {json.dumps(goal, ensure_ascii=False)}",
            f"version = {json.dumps(version)}",
            "agent_created = true",
            "agent_can_update = true",
            f"triggers = [{json.dumps(name)}]",
            "",
            "[entry]",
            'instructions = "SKILL.md"',
            "",
        ]
    )


def _set_manifest_version(path: Path, version: str) -> None:
    # The standard library writes text; the disclosure core validates the complete candidate schema.
    lines = path.read_text(encoding="utf-8").splitlines()
    replacement = f"version = {json.dumps(version)}"
    table_index = next((index for index, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
    version_index = next(
        (index for index, line in enumerate(lines[:table_index]) if re.match(r"^\s*version\s*=", line)),
        None,
    )
    if version_index is None:
        lines.insert(table_index, replacement)
    else:
        lines[version_index] = replacement
    text = "\n".join(lines).rstrip() + "\n"
    path.write_text(text, encoding="utf-8")


def _validate_candidate_skill(skill_path: Path) -> None:
    disclosure = ProgressiveDisclosureCore(
        [skill_path],
        skill_path.parent / ".candidate-validation-cache",
    )
    index = disclosure.prepare_skill_index()
    if len(index.entries) != 1:
        raise ValueError("candidate must contain exactly one valid skill")


def _write_candidate_metadata(candidate: SkillCandidate) -> None:
    data = {
        "schema_version": 1,
        "candidate_id": candidate.candidate_id,
        "name": candidate.name,
        "goal": candidate.goal,
        "parent_version": candidate.parent_version,
        "proposed_version": candidate.proposed_version,
        "parent_sha256": candidate.parent_sha256,
        "candidate_sha256": candidate.candidate_sha256,
        "created_at": candidate.created_at,
    }
    candidate.metadata_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

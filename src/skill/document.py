"""定义标准 Agent Skill 文档及其无依赖解析。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

SKILL_FILE = "SKILL.md"
NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
STANDARD_FIELDS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}
EXTENSION_KEYS = {
    "type": "super-agent-type",
    "version": "super-agent-version",
    "created_by": "super-agent-created-by",
    "agent_can_update": "super-agent-agent-can-update",
    "categories": "super-agent-categories",
    "requires": "super-agent-requires",
    "optional_tools": "super-agent-optional-tools",
    "includes": "super-agent-includes",
}
LIST_FIELDS = ("categories", "requires", "optional_tools", "includes")
MAX_RESOURCE_BYTES = 20_000_000
MAX_PACKAGE_FILES = 1_024
MAX_PACKAGE_FILE_BYTES = 50_000_000
MAX_PACKAGE_BYTES = 100_000_000


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
        if len(self.description) > 1024:
            raise ValueError("skill description cannot exceed 1024 characters")
        if self.created_by not in {"builtin", "user", "agent"}:
            raise ValueError("skill created_by must be builtin, user, or agent")

    @property
    def key(self) -> str:
        return f"{self.skill_type}:{self.name}"

    @property
    def root(self) -> Path:
        return self.path.parent

    def index_entry(self) -> dict[str, object]:
        return {
            "key": self.key,
            "description": self.description,
            "categories": list(self.categories),
            "requires": list(self.requires),
            "optional_tools": list(self.optional_tools),
            "version": self.version,
        }


@dataclass(frozen=True)
class SkillPackage:
    """一个标准 Skill 目录的安全、确定性快照。"""

    files: tuple[tuple[Path, bytes], ...]
    skill_path: Path
    sha256: str

    @property
    def skill_content(self) -> bytes:
        for relative, content in self.files:
            if relative == Path(SKILL_FILE):
                return content
        raise ValueError("Skill package does not contain SKILL.md")


def read_skill_package(source: str | Path) -> SkillPackage:
    """读取目录、SKILL.md、ZIP 或显式 git 来源，不执行其中资源。"""
    text = str(source)
    if text.startswith("git+"):
        repository, _, relative = text[4:].partition("#")
        with tempfile.TemporaryDirectory() as temporary:
            subprocess.run(
                ["git", "clone", "--quiet", "--depth", "1", repository, temporary],
                check=True,
            )
            return read_skill_package(Path(temporary) / relative)
    path = Path(source).expanduser().resolve()
    if path.is_file() and path.name == SKILL_FILE:
        return _package_from_file(path)
    if path.is_file() and path.suffix.lower() == ".zip":
        return _package_from_zip(path)
    if path.is_dir():
        return _package_from_directory(path)
    raise ValueError(f"unsupported Skill package source: {source}")


def pack_skill(skill: Skill, destination: str | Path) -> Path:
    """将 Skill 目录及其被动资源打成确定性的 ZIP。"""
    package = _package_from_directory(skill.root)
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as archive:
        for relative, content in package.files:
            info = zipfile.ZipInfo(
                f"{skill.name}/{relative.as_posix()}", (1980, 1, 1, 0, 0, 0)
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return target


def install_skill_package(package: SkillPackage, target: Path) -> None:
    """原子安装 Skill 目录，避免失败时留下半个包。"""
    if target.exists():
        raise ValueError(f"Skill target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    try:
        for relative, content in package.files:
            destination = _safe_package_destination(temporary, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        os.replace(temporary, target)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def read_skill_resource_text(skill: Skill, resource_path: str) -> tuple[str, str]:
    """安全读取 Skill 内的 UTF-8 文本资源。"""
    relative = Path(_required_text(resource_path, "resource path"))
    if relative.is_absolute() or "." in relative.parts or ".." in relative.parts:
        raise PermissionError(f"Skill resource path must be relative: {resource_path}")
    candidate = skill.root.joinpath(*relative.parts)
    current = skill.root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise PermissionError(
                f"Skill resources cannot be symbolic links: {resource_path}"
            )
    target = candidate.resolve()
    if not target.is_relative_to(skill.root.resolve()):
        raise PermissionError(f"Skill resource path escapes its root: {resource_path}")
    if not target.is_file():
        raise FileNotFoundError(f"Skill resource not found: {resource_path}")
    if target.stat().st_size > MAX_RESOURCE_BYTES:
        raise ValueError(f"Skill resource is too large: {resource_path}")
    try:
        return relative.as_posix(), target.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(
            f"Skill resource is not UTF-8 text: {resource_path}"
        ) from error


def _package_from_file(path: Path) -> SkillPackage:
    return _package_from_directory(path.parent)


def _package_from_directory(root: Path) -> SkillPackage:
    skill_file = root / SKILL_FILE
    if not skill_file.is_file():
        raise ValueError(f"Skill directory must contain {SKILL_FILE}: {root}")
    paths = sorted(root.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError(f"Skill package cannot contain symbolic links: {root}")
    selected = tuple(
        (path.relative_to(root), path.stat().st_size)
        for path in paths
        if path.is_file() and ".git" not in path.relative_to(root).parts
    )
    _validate_package_manifest(selected)
    files = tuple((relative, (root / relative).read_bytes()) for relative, _ in selected)
    return _make_package(files, skill_file)


def _package_from_zip(path: Path) -> SkillPackage:
    with zipfile.ZipFile(path) as archive:
        entries: list[tuple[Path, zipfile.ZipInfo]] = []
        declared_size = 0
        for info in archive.infolist():
            relative = _zip_relative_path(info.filename)
            if relative is None:
                continue
            if _zip_is_symlink(info):
                raise ValueError(
                    f"Skill package cannot contain symbolic links: {info.filename}"
                )
            if len(entries) >= MAX_PACKAGE_FILES:
                raise ValueError(
                    f"Skill package cannot contain more than {MAX_PACKAGE_FILES} files"
                )
            if info.file_size > MAX_PACKAGE_FILE_BYTES:
                raise ValueError(f"Skill package file is too large: {info.filename}")
            declared_size += info.file_size
            if declared_size > MAX_PACKAGE_BYTES:
                raise ValueError(
                    f"Skill package cannot exceed {MAX_PACKAGE_BYTES} unpacked bytes"
                )
            entries.append((relative, info))
        skill_paths = [
            relative for relative, _ in entries if relative.name == SKILL_FILE
        ]
        if len(skill_paths) != 1:
            raise ValueError("Skill package must contain exactly one SKILL.md")
        root = skill_paths[0].parent
        selected = tuple(
            (relative.relative_to(root), info)
            for relative, info in entries
            if relative.is_relative_to(root)
        )
        if len(selected) != len(entries):
            raise ValueError("Skill package files must stay under the SKILL.md directory")
        _validate_package_manifest(
            tuple((relative, info.file_size) for relative, info in selected)
        )
        files = _read_zip_files(archive, selected)
    return _make_package(files, Path(root) / SKILL_FILE)


def _read_zip_files(
    archive: zipfile.ZipFile,
    entries: tuple[tuple[Path, zipfile.ZipInfo], ...],
) -> tuple[tuple[Path, bytes], ...]:
    """有界读取 ZIP 内容，不信任归档声明的解压大小。"""
    files: list[tuple[Path, bytes]] = []
    total = 0
    for relative, info in entries:
        remaining = min(MAX_PACKAGE_FILE_BYTES, MAX_PACKAGE_BYTES - total)
        with archive.open(info) as source:
            content = source.read(remaining + 1)
        if len(content) > MAX_PACKAGE_FILE_BYTES:
            raise ValueError(f"Skill package file is too large: {info.filename}")
        total += len(content)
        if total > MAX_PACKAGE_BYTES:
            raise ValueError(
                f"Skill package cannot exceed {MAX_PACKAGE_BYTES} unpacked bytes"
            )
        files.append((relative, content))
    return tuple(files)


def _zip_relative_path(name: str) -> Path | None:
    if name.startswith("__MACOSX/") or name.endswith("/"):
        return None
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"unsafe Skill package path: {name}")
    return path


def _zip_is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _make_package(
    files: tuple[tuple[Path, bytes], ...], skill_path: Path
) -> SkillPackage:
    normalized = tuple(
        sorted((_safe_package_path(path), content) for path, content in files)
    )
    _validate_package_manifest(tuple((path, len(content)) for path, content in normalized))
    digest = hashlib.sha256()
    for relative, content in normalized:
        digest.update(relative.as_posix().encode())
        digest.update(b"\0")
        digest.update(content)
    return SkillPackage(normalized, skill_path, digest.hexdigest())


def _validate_package_manifest(entries: tuple[tuple[Path, int], ...]) -> None:
    """统一验证目录和 ZIP 的资源边界及文件树结构。"""
    normalized = tuple((_safe_package_path(path), size) for path, size in entries)
    if len(normalized) > MAX_PACKAGE_FILES:
        raise ValueError(
            f"Skill package cannot contain more than {MAX_PACKAGE_FILES} files"
        )
    paths = tuple(path for path, _ in normalized)
    if len(set(paths)) != len(paths):
        raise ValueError("Skill package cannot contain duplicate paths")
    skill_paths = tuple(path for path in paths if path.name == SKILL_FILE)
    if skill_paths != (Path(SKILL_FILE),):
        raise ValueError(
            "Skill package must contain one root SKILL.md and no nested SKILL.md"
        )
    occupied = set(paths)
    if any(parent in occupied for path in paths for parent in path.parents[:-1]):
        raise ValueError("Skill package path cannot be both a file and a directory")
    if any(size < 0 or size > MAX_PACKAGE_FILE_BYTES for _, size in normalized):
        raise ValueError(
            f"Skill package files cannot exceed {MAX_PACKAGE_FILE_BYTES} bytes"
        )
    if sum(size for _, size in normalized) > MAX_PACKAGE_BYTES:
        raise ValueError(
            f"Skill package cannot exceed {MAX_PACKAGE_BYTES} unpacked bytes"
        )


def _safe_package_path(path: Path) -> Path:
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe Skill package path: {path}")
    return Path(*path.parts)


def _safe_package_destination(root: Path, relative: Path) -> Path:
    path = _safe_package_path(relative)
    destination = (root / path).resolve()
    if not destination.is_relative_to(root.resolve()):
        raise ValueError(f"Skill package path escapes target: {relative}")
    return destination


def parse_skill_text(text: str, path: Path) -> Skill:
    """读取标准 YAML front matter 和 Markdown 正文。"""
    frontmatter, body = _split_front_matter(text)
    metadata = _normalize_metadata(frontmatter)
    name = _required_text(metadata.get("name"), "skill name")
    _validate_skill_path(name, path)
    digest = hashlib.sha256(text.encode()).hexdigest()
    return Skill(
        name=name,
        skill_type=_required_text(metadata.get("type"), "skill type"),
        description=_required_text(metadata.get("description"), "skill description"),
        body=body.strip(),
        path=path.resolve(),
        categories=_text_array(metadata.get("categories", []), "skill categories"),
        requires=_text_array(metadata.get("requires", []), "skill requires"),
        optional_tools=_text_array(
            metadata.get("optional_tools", []), "skill optional_tools"
        ),
        includes=_text_array(metadata.get("includes", []), "skill includes"),
        version=_required_text(metadata.get("version"), "skill version"),
        created_by=_required_text(metadata.get("created_by"), "skill created_by"),
        agent_can_update=_boolean(
            metadata.get("agent_can_update"), "skill agent_can_update"
        ),
        metadata=MappingProxyType(metadata),
        sha256=digest,
    )


def format_skill(metadata: Mapping[str, object], body: str) -> str:
    """生成 Agent Skills 规范兼容的 SKILL.md。"""
    name = _required_text(metadata.get("name"), "skill name")
    description = _required_text(metadata.get("description"), "skill description")
    extension = _extension_metadata(metadata)
    lines = [
        "---",
        f"name: {_yaml_string(name)}",
        f"description: {_yaml_string(description)}",
    ]
    for key in ("license", "compatibility", "allowed-tools"):
        if key in metadata:
            lines.append(f"{key}: {_yaml_string(_required_text(metadata[key], key))}")
    if extension:
        lines.append("metadata:")
        lines.extend(
            f"  {key}: {_yaml_string(value)}" for key, value in extension.items()
        )
    return "\n".join((*lines, "---", "", body.strip(), ""))


def _normalize_metadata(frontmatter: Mapping[str, object]) -> dict[str, object]:
    unexpected = sorted(set(frontmatter) - STANDARD_FIELDS)
    if unexpected:
        raise ValueError(
            f"unsupported Skill front matter fields: {', '.join(unexpected)}"
        )
    standard_metadata = frontmatter.get("metadata", {})
    if not isinstance(standard_metadata, Mapping):
        raise TypeError("skill metadata must be a string mapping")
    extra = {
        str(key): _required_text(value, f"skill metadata {key}")
        for key, value in standard_metadata.items()
    }
    normalized: dict[str, object] = {
        "name": _required_text(frontmatter.get("name"), "skill name"),
        "description": _required_text(
            frontmatter.get("description"), "skill description"
        ),
        "type": extra.get(EXTENSION_KEYS["type"], "prompt"),
        "version": extra.get(EXTENSION_KEYS["version"], "0.1.0"),
        "created_by": extra.get(EXTENSION_KEYS["created_by"], "user"),
        "agent_can_update": _extension_boolean(
            extra.get(EXTENSION_KEYS["agent_can_update"], "false")
        ),
        "standard_metadata": extra,
    }
    for key in ("license", "compatibility", "allowed-tools"):
        if key in frontmatter:
            value = _required_text(frontmatter[key], key)
            if key == "compatibility" and len(value) > 500:
                raise ValueError("compatibility cannot exceed 500 characters")
            normalized[key] = value
    for key in LIST_FIELDS:
        normalized[key] = _extension_list(extra.get(EXTENSION_KEYS[key], "[]"), key)
    return normalized


def _extension_metadata(metadata: Mapping[str, object]) -> dict[str, str]:
    existing = metadata.get("standard_metadata", {})
    if not isinstance(existing, Mapping):
        raise TypeError("standard_metadata must be a string mapping")
    values = {
        str(key): _required_text(value, f"skill metadata {key}")
        for key, value in existing.items()
        if key not in EXTENSION_KEYS.values()
    }
    values[EXTENSION_KEYS["type"]] = _required_text(
        metadata.get("type", "prompt"), "skill type"
    )
    values[EXTENSION_KEYS["version"]] = _required_text(
        metadata.get("version", "0.1.0"), "skill version"
    )
    values[EXTENSION_KEYS["created_by"]] = _required_text(
        metadata.get("created_by", "user"), "skill created_by"
    )
    update = _boolean(metadata.get("agent_can_update", False), "skill agent_can_update")
    values[EXTENSION_KEYS["agent_can_update"]] = "true" if update else "false"
    for key in LIST_FIELDS:
        items = _text_array(metadata.get(key, []), f"skill {key}")
        if items:
            values[EXTENSION_KEYS[key]] = json.dumps(
                items, ensure_ascii=False, separators=(",", ":")
            )
    return dict(sorted(values.items()))


def _split_front_matter(text: str) -> tuple[dict[str, object], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError("skill must start with YAML front matter")
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == "---":
            source = "".join(lines[1:index])
            return _parse_yaml_frontmatter(source), "".join(lines[index + 1 :])
    raise ValueError("skill YAML front matter is not closed")


def _parse_yaml_frontmatter(source: str) -> dict[str, object]:
    """解析 Agent Skills front matter 所需的受限 YAML 字符串结构。"""
    lines = source.splitlines()
    result: dict[str, object] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if "\t" in line:
            raise ValueError("tabs are not allowed in Skill YAML front matter")
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line.startswith(" "):
            raise ValueError(f"unexpected YAML indentation on line {index + 1}")
        key, raw = _yaml_pair(line, index + 1)
        if key in result:
            raise ValueError(f"duplicate Skill front matter field: {key}")
        if key == "metadata" and (not raw or raw == "{}"):
            if raw:
                value, index = {}, index + 1
            else:
                value, index = _parse_yaml_metadata(lines, index + 1)
        elif _is_block_scalar(raw):
            value, index = _parse_block_scalar(lines, index + 1, 0, raw)
        else:
            value, index = _yaml_string_value(raw, index + 1), index + 1
        result[key] = value
    return result


def _parse_yaml_metadata(lines: list[str], index: int) -> tuple[dict[str, str], int]:
    result: dict[str, str] = {}
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            break
        if indent != 2 or "\t" in line:
            raise ValueError(
                f"Skill metadata must use two-space indentation on line {index + 1}"
            )
        key, raw = _yaml_pair(line[2:], index + 1)
        if key in result:
            raise ValueError(f"duplicate Skill metadata field: {key}")
        if _is_block_scalar(raw):
            value, index = _parse_block_scalar(lines, index + 1, 2, raw)
        else:
            value, index = _yaml_string_value(raw, index + 1), index + 1
        result[key] = value
    return result, index


def _parse_block_scalar(
    lines: list[str], index: int, parent_indent: int, marker: str
) -> tuple[str, int]:
    collected: list[str] = []
    while index < len(lines):
        line = lines[index]
        indent = len(line) - len(line.lstrip(" "))
        if line.strip() and indent <= parent_indent:
            break
        collected.append(line)
        index += 1
    content_indent = min(
        (len(line) - len(line.lstrip(" ")) for line in collected if line.strip()),
        default=parent_indent + 2,
    )
    values = [line[content_indent:] if line.strip() else "" for line in collected]
    value = "\n".join(values) if marker.startswith("|") else _fold_yaml_lines(values)
    if marker.endswith("-"):
        value = value.rstrip("\n")
    elif not marker.endswith("+"):
        value = value.rstrip("\n") + "\n"
    return value, index


def _fold_yaml_lines(lines: list[str]) -> str:
    result: list[str] = []
    for index, line in enumerate(lines):
        if index and line and lines[index - 1]:
            result.append(" ")
        elif index:
            result.append("\n")
        result.append(line)
    return "".join(result)


def _yaml_pair(line: str, line_number: int) -> tuple[str, str]:
    if ":" not in line:
        raise ValueError(f"invalid Skill YAML mapping on line {line_number}")
    key, raw = line.split(":", 1)
    key = key.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", key):
        raise ValueError(f"invalid Skill YAML key on line {line_number}: {key}")
    return key, raw.strip()


def _yaml_string_value(raw: str, line_number: int) -> str:
    if raw.startswith('"'):
        return _double_quoted_yaml_string(raw, line_number)
    if raw.startswith("'"):
        return _single_quoted_yaml_string(raw, line_number)
    value = _strip_yaml_comment(raw)
    if not value:
        raise ValueError(f"Skill YAML value must be a string on line {line_number}")
    if value[0] in "[{&*!" or re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value):
        raise ValueError(f"Skill YAML value must be a string on line {line_number}")
    if value.lower() in {"null", "~", "true", "false", ".nan", ".inf", "-.inf"}:
        raise ValueError(f"Skill YAML value must be a string on line {line_number}")
    if re.search(r":\s", value):
        raise ValueError(
            f"plain Skill YAML strings containing ': ' must be quoted on line {line_number}"
        )
    return value


def _single_quoted_yaml_string(value: str, line_number: int) -> str:
    result: list[str] = []
    index = 1
    while index < len(value):
        if value[index] != "'":
            result.append(value[index])
            index += 1
            continue
        if index + 1 < len(value) and value[index + 1] == "'":
            result.append("'")
            index += 2
            continue
        remainder = value[index + 1 :].strip()
        if remainder and not remainder.startswith("#"):
            raise ValueError(f"unexpected YAML content on line {line_number}")
        return "".join(result)
    raise ValueError(f"invalid single-quoted YAML string on line {line_number}")


def _double_quoted_yaml_string(value: str, line_number: int) -> str:
    try:
        parsed, end = json.JSONDecoder().raw_decode(value)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"invalid double-quoted YAML string on line {line_number}"
        ) from error
    remainder = value[end:].strip()
    if remainder and not remainder.startswith("#"):
        raise ValueError(f"unexpected YAML content on line {line_number}")
    if not isinstance(parsed, str):
        raise TypeError(f"Skill YAML value must be a string on line {line_number}")
    return parsed


def _strip_yaml_comment(value: str) -> str:
    match = re.search(r"\s+#", value)
    return value[: match.start()].rstrip() if match else value.rstrip()


def _is_block_scalar(value: str) -> bool:
    return value in {"|", "|-", "|+", ">", ">-", ">+"}


def _extension_boolean(value: str) -> bool:
    if value not in {"true", "false"}:
        raise ValueError("super-agent-agent-can-update must be 'true' or 'false'")
    return value == "true"


def _extension_list(value: str, name: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"super-agent-{name.replace('_', '-')} must be a JSON string array"
        ) from error
    return list(_text_array(parsed, f"skill {name}"))


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _text_array(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{name} must be an array of non-empty text")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


def _validate_name(value: str, name: str) -> None:
    if not NAME_PATTERN.fullmatch(value) or "--" in value:
        raise ValueError(
            f"{name} must use 1-64 lowercase letters, numbers, or single hyphens"
        )


def _validate_skill_path(name: str, path: Path) -> None:
    if path.name == SKILL_FILE and path.parent.name != name:
        raise ValueError(
            f"Skill name must match its parent directory: {name} != {path.parent.name}"
        )

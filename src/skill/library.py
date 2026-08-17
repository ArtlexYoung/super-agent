"""统一读取、披露、缓存、激活和管理 Markdown Skill。"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from core.disclosure import DisclosedContent, DisclosureStore
from core.model import Tool
from core.run import RunSession, ToolContext
from skill.document import Skill, format_skill, parse_skill_text

RecordEvent = Callable[[str, Mapping[str, object]], object]


@dataclass(frozen=True)
class SkillPage:
    items: tuple[Mapping[str, object], ...]
    page: int
    page_size: int
    total: int

    @property
    def has_more(self) -> bool:
        return self.page * self.page_size < self.total

    def to_dict(self) -> dict[str, object]:
        return {
            "items": [dict(item) for item in self.items],
            "page": self.page,
            "page_size": self.page_size,
            "total": self.total,
            "has_more": self.has_more,
        }


class SkillLibrary:
    """所有 Skill 使用的中央渐进披露入口。"""

    def __init__(
        self,
        roots: Iterable[str | Path] = (),
        *,
        writable_root: str | Path | None = None,
        cache_root: str | Path | None = None,
        record_event: RecordEvent | None = None,
        cache_entries: int = 128,
        disabled_references: Iterable[str] = (),
    ) -> None:
        self.roots = tuple(Path(root).expanduser().resolve() for root in roots)
        self.writable_root = (
            None
            if writable_root is None
            else Path(writable_root).expanduser().resolve()
        )
        self.cache_root = (
            None if cache_root is None else Path(cache_root).expanduser().resolve()
        )
        self.record_event = record_event
        self.cache_entries = cache_entries
        self.disclosures = DisclosureStore(
            self.cache_root,
            max_entries=cache_entries,
            record_event=self._record,
        )
        self.disabled_references = frozenset(
            _required_text(item, "disabled Skill reference").lower()
            for item in disabled_references
        )
        self._skills: dict[str, Skill] | None = None

    def refresh(self) -> None:
        self._skills = None

    def use_disclosure_store(self, store: DisclosureStore) -> None:
        """让当前作用域与运行中的其他机制共用披露缓存。"""
        if not isinstance(store, DisclosureStore):
            raise TypeError("Skill disclosure store must be a DisclosureStore")
        self.disclosures = store

    def for_scope(
        self,
        user_id: str,
        agent_name: str,
        *,
        disabled_references: Iterable[str] | None = None,
    ) -> SkillLibrary:
        """创建共享只读根、隔离可写内容和缓存的用户-Agent 视图。"""
        scope = f"{_required_text(user_id, 'user ID')}\0{_required_text(agent_name, 'Agent name')}"
        safe = hashlib.sha256(scope.encode()).hexdigest()[:24]
        writable = (
            None
            if self.writable_root is None
            else self.writable_root / "users" / safe / "skills"
        )
        cache = None if self.cache_root is None else self.cache_root / "users" / safe
        return SkillLibrary(
            self.roots,
            writable_root=writable,
            cache_root=cache,
            record_event=self.record_event,
            cache_entries=self.cache_entries,
            disabled_references=(
                self.disabled_references
                if disabled_references is None
                else disabled_references
            ),
        )

    def list_skills(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        skill_type: str | None = None,
        category: str | None = None,
    ) -> SkillPage:
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError("skill page and page_size are outside their limits")
        selected = sorted(
            (item for item in self._load().values() if not self._is_disabled(item)),
            key=lambda item: item.key,
        )
        if skill_type:
            selected = [item for item in selected if item.skill_type == skill_type]
        if category:
            selected = [item for item in selected if category in item.categories]
        start = (page - 1) * page_size
        values = tuple(
            MappingProxyType(item.index_entry())
            for item in selected[start : start + page_size]
        )
        return SkillPage(values, page, page_size, len(selected))

    def find(self, reference: str) -> Skill:
        skills = self._load()
        key = reference.strip().lower()
        if key in skills and not self._is_disabled(skills[key]):
            return skills[key]
        matches = [
            skill
            for skill in skills.values()
            if skill.name == key and not self._is_disabled(skill)
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KeyError(f"skill not found: {reference}")
        raise ValueError(f"skill name is ambiguous; use type:name: {reference}")

    def _is_disabled(self, skill: Skill) -> bool:
        return bool(
            {skill.key, skill.name, skill.skill_type} & self.disabled_references
        )

    def preview(
        self, reference: str, *, offset: int = 0, max_characters: int = 4000
    ) -> DisclosedContent:
        skill = self.find(reference)
        return self.disclosures.preview(
            skill.key,
            skill.body,
            offset=offset,
            max_characters=max_characters,
        )

    def disclose(
        self, reference: str, *, offset: int = 0, max_characters: int = 4000
    ) -> DisclosedContent:
        skill = self.find(reference)
        value = self.disclosures.disclose(
            skill.key,
            skill.body,
            offset=offset,
            max_characters=max_characters,
        )
        event = {
            "key": skill.key,
            "cache_path": value.cache_path,
            "offset": value.offset,
            "next_offset": value.next_offset,
            "sha256": value.sha256,
        }
        self._record("skill.disclosed", event)
        return value

    def read_disclosed(
        self,
        cache_path: str,
        *,
        offset: int = 0,
        max_characters: int = 4000,
    ) -> DisclosedContent:
        return self.disclosures.read(
            cache_path,
            offset=offset,
            max_characters=max_characters,
        )

    def history(self) -> tuple[Mapping[str, object], ...]:
        return self.disclosures.history()

    def activate(self, reference: str, session: RunSession) -> tuple[str, ...]:
        instructions = list(session.instructions)
        tools = dict(session.tools)
        active_skills = list(session.active_skills)
        context_characters = session.context_characters
        activated: list[str] = []
        try:
            self._activate(self.find(reference), session, activated, [])
            for key in activated:
                self._record(
                    "skill.activated", {"key": key, "run_id": session.identity.run_id}
                )
        except BaseException:
            session.instructions[:] = instructions
            session.tools.clear()
            session.tools.update(tools)
            session.active_skills[:] = active_skills
            session.context_characters = context_characters
            raise
        return tuple(activated)

    def tools(self) -> tuple[Tool, ...]:
        """向模型公开中央索引、披露、缓存和激活操作。"""
        return (
            Tool(
                "list_skills",
                "List a page from the Skill index",
                self._list_tool,
                _list_schema(),
            ),
            Tool(
                "read_skill",
                "Read one bounded page of a Skill",
                self._read_tool,
                _read_schema(),
            ),
            self.disclosures.tool(),
            Tool(
                "activate_skill",
                "Activate one disclosed Skill for this run",
                self._activate_tool,
                _activate_schema(),
            ),
        )

    def create(
        self,
        name: str,
        body: str,
        *,
        description: str,
        skill_type: str = "prompt",
        categories: Iterable[str] = (),
        requires: Iterable[str] = (),
        optional_tools: Iterable[str] = (),
        includes: Iterable[str] = (),
        actor: str = "user",
        agent_can_update: bool | None = None,
    ) -> Skill:
        root = self._require_writable_root()
        key = f"{skill_type}:{name}"
        if key in self._load():
            raise ValueError(f"skill already exists: {key}")
        metadata = {
            "name": name,
            "type": skill_type,
            "description": description,
            "categories": list(categories),
            "requires": list(requires),
            "optional_tools": list(optional_tools),
            "includes": list(includes),
            "version": "0.1.0",
            "created_by": actor,
            "agent_can_update": actor == "agent"
            if agent_can_update is None
            else agent_can_update,
        }
        path = root / skill_type / f"{name}.md"
        self._write_skill(path, metadata, body, expected_sha256=None)
        self._record(
            "skill.created", {"key": key, "actor": actor, "sha256": _file_sha256(path)}
        )
        return self.find(key)

    def update(
        self,
        reference: str,
        body: str,
        *,
        expected_sha256: str,
        actor: str = "user",
        description: str | None = None,
    ) -> Skill:
        current = self.find(reference)
        if actor == "agent" and not (
            current.created_by == "agent" and current.agent_can_update
        ):
            raise PermissionError(f"agent cannot update skill: {current.key}")
        self._require_writable_path(current.path)
        if current.sha256 != expected_sha256:
            raise RuntimeError(f"skill changed since it was read: {current.key}")
        metadata = dict(current.metadata)
        metadata["version"] = _next_patch(current.version)
        if description is not None:
            metadata["description"] = description
        self._write_skill(current.path, metadata, body, expected_sha256=expected_sha256)
        updated = self.find(current.key)
        self._record(
            "skill.updated",
            {
                "key": current.key,
                "actor": actor,
                "before_sha256": current.sha256,
                "after_sha256": updated.sha256,
                "version": updated.version,
            },
        )
        return updated

    def derive(
        self, reference: str, name: str, body: str, *, actor: str = "agent"
    ) -> Skill:
        source = self.find(reference)
        categories = (*source.categories, f"derived/{source.key}")
        return self.create(
            name,
            body,
            description=f"Derived from {source.key}: {source.description}",
            skill_type=source.skill_type,
            categories=categories,
            requires=source.requires,
            optional_tools=source.optional_tools,
            includes=source.includes,
            actor=actor,
        )

    def remove(
        self, reference: str, *, expected_sha256: str, actor: str = "user"
    ) -> None:
        skill = self.find(reference)
        if actor == "agent" and not (
            skill.created_by == "agent" and skill.agent_can_update
        ):
            raise PermissionError(f"agent cannot remove skill: {skill.key}")
        self._require_writable_path(skill.path)
        if skill.sha256 != expected_sha256:
            raise RuntimeError(f"skill changed since it was read: {skill.key}")
        skill.path.unlink()
        self.refresh()
        self._record(
            "skill.removed", {"key": skill.key, "actor": actor, "sha256": skill.sha256}
        )

    def pack(self, reference: str, destination: str | Path) -> Path:
        skill = self.find(reference)
        target = Path(destination).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        info = zipfile.ZipInfo(skill.path.name, (1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr(info, skill.path.read_bytes())
        return target

    def install(
        self, source: str | Path, *, expected_sha256: str | None = None
    ) -> Skill:
        content, name = _read_package(source)
        digest = hashlib.sha256(content).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError("skill package SHA-256 mismatch")
        parsed = parse_skill_text(content.decode("utf-8"), Path(name))
        target = self._require_writable_root() / parsed.skill_type / f"{parsed.name}.md"
        if target.exists():
            raise ValueError(f"skill already installed: {parsed.key}")
        _atomic_write(target, content)
        self.refresh()
        installed = self.find(parsed.key)
        self._record("skill.installed", {"key": installed.key, "sha256": digest})
        return installed

    def _load(self) -> dict[str, Skill]:
        if self._skills is not None:
            return self._skills
        roots = (*self.roots, *((self.writable_root,) if self.writable_root else ()))
        loaded: dict[str, Skill] = {}
        for root in roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.md")):
                if path.is_symlink():
                    raise ValueError(f"skill files cannot be symbolic links: {path}")
                skill = parse_skill_text(path.read_text(encoding="utf-8"), path)
                if skill.key in loaded:
                    raise ValueError(f"duplicate skill key: {skill.key}")
                loaded[skill.key] = skill
        self._skills = loaded
        return loaded

    def _activate(
        self, skill: Skill, session: RunSession, activated: list[str], stack: list[str]
    ) -> None:
        if skill.key in session.active_skills:
            return
        if skill.key in stack:
            raise ValueError(f"skill include cycle: {' -> '.join((*stack, skill.key))}")
        available = session.values.get("available_tools", {})
        if not isinstance(available, Mapping):
            raise TypeError("run available_tools must be an object")
        for name in (*skill.requires, *skill.optional_tools):
            if name in session.tools or name not in available:
                continue
            tool = available[name]
            if not isinstance(tool, Tool):
                raise TypeError(f"registered Skill tool is not a Tool: {name}")
            session.add_tool(tool)
        missing = sorted(set(skill.requires) - set(session.tools))
        if missing:
            raise RuntimeError(
                f"skill requires tools that are not registered: {skill.key}: {', '.join(missing)}"
            )
        for included in skill.includes:
            self._activate(self.find(included), session, activated, [*stack, skill.key])
        if len(skill.body) > 20_000:
            raise RuntimeError(
                f"Skill is too large for one activation: {skill.key}; read its pages explicitly"
            )
        disclosure = self.disclose(skill.key, max_characters=max(4000, len(skill.body)))
        session.add_instruction(f"[Skill {skill.key}]\n{disclosure.content}")
        session.activate_skill(skill.key)
        if skill.key not in activated:
            activated.append(skill.key)

    def _list_tool(
        self, arguments: dict[str, object], _context: ToolContext
    ) -> dict[str, object]:
        return self.list_skills(
            page=_integer(arguments.get("page", 1), "page", 1, 1_000_000),
            page_size=_integer(arguments.get("page_size", 20), "page_size", 1, 100),
            skill_type=_optional_text(arguments.get("type")),
            category=_optional_text(arguments.get("category")),
        ).to_dict()

    def _read_tool(
        self, arguments: dict[str, object], context: ToolContext
    ) -> dict[str, object]:
        reference = _required_text(arguments.get("skill"), "skill")
        disclosed = self.disclose(
            reference,
            offset=_integer(arguments.get("offset", 0), "offset", 0, 10_000_000),
            max_characters=_integer(
                arguments.get("max_characters", 4000), "max_characters", 1, 20_000
            ),
        )
        context.emit(
            "skill.disclosed",
            {
                "key": disclosed.reference,
                "cache_path": disclosed.cache_path,
                "offset": disclosed.offset,
                "next_offset": disclosed.next_offset,
            },
        )
        return disclosed.to_dict()

    def _activate_tool(
        self, arguments: dict[str, object], context: ToolContext
    ) -> dict[str, object]:
        reference = _required_text(arguments.get("skill"), "skill")
        activated = self.activate(reference, context.session)
        for key in activated:
            context.emit("skill.activated", {"key": key})
        return {"activated": list(activated)}

    def _write_skill(
        self,
        path: Path,
        metadata: Mapping[str, object],
        body: str,
        *,
        expected_sha256: str | None,
    ) -> None:
        content = format_skill(metadata, body).encode()
        parse_skill_text(content.decode("utf-8"), path)
        if expected_sha256 is not None and _file_sha256(path) != expected_sha256:
            raise RuntimeError(f"skill changed before write: {path}")
        _atomic_write(path, content)
        self.refresh()

    def _require_writable_root(self) -> Path:
        if self.writable_root is None:
            raise RuntimeError("writable Skill root is not configured")
        return self.writable_root

    def _require_writable_path(self, path: Path) -> None:
        _inside(self._require_writable_root(), path)

    def _record(self, event_type: str, data: Mapping[str, object]) -> None:
        if self.record_event is not None:
            self.record_event(event_type, data)


def _read_package(source: str | Path) -> tuple[bytes, str]:
    text = str(source)
    if text.startswith("git+"):
        repository, _, relative = text[4:].partition("#")
        with tempfile.TemporaryDirectory() as temporary:
            subprocess.run(
                ["git", "clone", "--quiet", "--depth", "1", repository, temporary],
                check=True,
            )
            return _read_package(Path(temporary) / relative)
    path = Path(source).expanduser().resolve()
    if path.is_file() and path.suffix.lower() == ".md":
        return path.read_bytes(), path.name
    if path.is_file() and path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.endswith(".md") and not name.startswith("__MACOSX/")
            ]
            if (
                len(names) != 1
                or Path(names[0]).is_absolute()
                or ".." in Path(names[0]).parts
            ):
                raise ValueError("skill package must contain one safe Markdown file")
            return archive.read(names[0]), Path(names[0]).name
    if path.is_dir():
        files = list(path.glob("*.md"))
        if len(files) == 1:
            return files[0].read_bytes(), files[0].name
    raise ValueError(f"unsupported skill package source: {source}")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _inside(root: Path, path: Path) -> Path:
    selected = path.expanduser().resolve()
    if not selected.is_relative_to(root.expanduser().resolve()):
        raise PermissionError(f"path is outside configured root: {path}")
    return selected


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _next_patch(version: str) -> str:
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"skill version must use x.y.z: {version}")
    return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _required_text(value, "optional text")


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _list_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "page": {"type": "integer", "minimum": 1},
            "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
            "type": {"type": "string"},
            "category": {"type": "string"},
        },
    }


def _read_schema() -> dict[str, object]:
    return {
        "type": "object",
        "required": ["skill"],
        "properties": {
            "skill": {"type": "string"},
            "offset": {"type": "integer", "minimum": 0},
            "max_characters": {"type": "integer", "minimum": 1, "maximum": 20000},
        },
    }


def _activate_schema() -> dict[str, object]:
    return {
        "type": "object",
        "required": ["skill"],
        "properties": {"skill": {"type": "string"}},
    }

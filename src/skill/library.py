"""集中发现、去重、披露、激活和更新插件及其 Skill。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

from core import require_integer as _integer, require_text as _text
from core.disclosure import DisclosedContent, DisclosurePage, DisclosureStore
from core.model import Tool
from core.run import RunContext, ToolContext
from skill.document import (
    SKILL_FILE,
    Skill,
    format_skill,
    install_skill_package,
    pack_skill,
    parse_skill_text,
    read_skill_package,
    read_skill_resource_text,
)

PLUGIN_FILE = "plugin.toml"
PLUGIN_ID_PATTERN = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?"
    r"(?:/[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)*"
)
RecordEvent = Callable[[str, Mapping[str, object]], object]


@dataclass(frozen=True)
class Plugin:
    """一个安装、版本、权限和进化边界的被动内容包。"""

    plugin_id: str
    version: str
    root: Path
    requires: tuple[str, ...]
    skills: Mapping[str, Skill]
    sha256: str
    base_hash: str | None = None
    sources: tuple[Path, ...] = ()
    writable: bool = False

    @property
    def reference(self) -> str:
        return f"plugin:{self.plugin_id}"

    @property
    def entry(self) -> Skill:
        return self.skills["main"]

    def index_entry(self) -> dict[str, object]:
        return {
            "key": self.reference,
            "version": self.version,
            "description": self.entry.description,
            "requires": [f"plugin:{item}" for item in self.requires],
            "skills": len(self.skills),
            "sha256": self.sha256,
            "sources": len(self.sources),
        }


@dataclass(frozen=True)
class PluginSnapshot:
    """一次运行固定使用的插件依赖图和内容修订。"""

    snapshot_id: str
    plugins: Mapping[str, Plugin]
    skills: Mapping[str, Skill]

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "plugins": {
                key: {"version": value.version, "sha256": value.sha256}
                for key, value in self.plugins.items()
            },
        }


class PluginCatalog:
    """插件和 Skill 共用的唯一索引、快照与渐进披露入口。"""

    def __init__(
        self,
        roots: Iterable[str | Path] = (),
        *,
        writable_root: str | Path | None = None,
        cache_root: str | Path | None = None,
        record_event: RecordEvent | None = None,
        cache_entries: int = 128,
        disabled_plugins: Iterable[str] = (),
        disabled_skills: Iterable[str] = (),
    ) -> None:
        self.roots = tuple(Path(root).expanduser().resolve() for root in roots)
        self.writable_root = _path_or_none(writable_root)
        self.cache_root = _path_or_none(cache_root)
        self.record_event = record_event
        self.cache_entries = cache_entries
        self.disclosures = DisclosureStore(self.cache_root, max_entries=cache_entries, record_event=self._record)
        self.disabled_plugins = frozenset(_plugin_reference(item) for item in disabled_plugins)
        self.disabled_skills = frozenset(_skill_reference(item) for item in disabled_skills)
        self._snapshot: PluginSnapshot | None = None

    def refresh(self) -> None:
        """让下一次顶层运行读取新内容，现有快照保持不变。"""
        self._snapshot = None

    def snapshot(self) -> PluginSnapshot:
        if self._snapshot is None:
            self._snapshot = self._build_snapshot()
        return self._snapshot

    def use_disclosure_store(self, store: DisclosureStore) -> None:
        if not isinstance(store, DisclosureStore):
            raise TypeError("plugin disclosure store must be a DisclosureStore")
        self.disclosures = store

    def for_scope(
        self,
        user_id: str,
        agent_name: str,
        *,
        disabled_plugins: Iterable[str] | None = None,
        disabled_skills: Iterable[str] | None = None,
    ) -> PluginCatalog:
        """共享只读内容，同时隔离用户和 Agent 的覆盖层与缓存。"""
        scope = f"{_text(user_id, 'user ID')}\0{_text(agent_name, 'Agent name')}"
        safe = hashlib.sha256(scope.encode()).hexdigest()[:24]
        writable = None if self.writable_root is None else self.writable_root / "users" / safe / "plugins"
        cache = None if self.cache_root is None else self.cache_root / "users" / safe
        return PluginCatalog(
            self.roots,
            writable_root=writable,
            cache_root=cache,
            record_event=self.record_event,
            cache_entries=self.cache_entries,
            disabled_plugins=self.disabled_plugins if disabled_plugins is None else disabled_plugins,
            disabled_skills=self.disabled_skills if disabled_skills is None else disabled_skills,
        )

    def list_plugins(self, *, page: int = 1, page_size: int = 20) -> DisclosurePage:
        selected = sorted(
            (item for item in self.snapshot().plugins.values() if item.reference not in self.disabled_plugins),
            key=lambda item: item.reference,
        )
        return _page(selected, page, page_size, lambda item: item.index_entry())

    def list_skills(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        plugin: str | None = None,
        skill_type: str | None = None,
        category: str | None = None,
    ) -> DisclosurePage:
        selected = sorted(
            (item for item in self.snapshot().skills.values() if not self._skill_is_disabled(item)),
            key=lambda item: item.reference,
        )
        if plugin:
            plugin_id = self.find_plugin(plugin).plugin_id
            selected = [item for item in selected if item.plugin_id == plugin_id]
        if skill_type:
            selected = [item for item in selected if item.skill_type == skill_type]
        if category:
            selected = [item for item in selected if category in item.categories]
        return _page(selected, page, page_size, lambda item: item.index_entry())

    def find_plugin(self, reference: str) -> Plugin:
        key = _plugin_reference(reference)
        try:
            plugin = self.snapshot().plugins[key.removeprefix("plugin:")]
        except KeyError as error:
            raise KeyError(f"plugin not found: {key}") from error
        if key in self.disabled_plugins:
            raise PermissionError(f"plugin is disabled: {key}")
        return plugin

    def find_skill(self, reference: str) -> Skill:
        key = _skill_reference(reference)
        try:
            skill = self.snapshot().skills[key]
        except KeyError as error:
            raise KeyError(f"skill not found: {key}") from error
        if self._skill_is_disabled(skill):
            raise PermissionError(f"skill is disabled: {key}")
        return skill

    def preview_plugin(self, reference: str, **page: int) -> DisclosedContent:
        return self._read(self.find_plugin(reference).entry, None, page)

    def preview_skill(self, reference: str, **page: int) -> DisclosedContent:
        return self._read(self.find_skill(reference), None, page)

    def disclose_plugin(self, reference: str, **page: int) -> DisclosedContent:
        return self._read(self.find_plugin(reference).entry, "plugin", page)

    def disclose_skill(self, reference: str, **page: int) -> DisclosedContent:
        return self._read(self.find_skill(reference), "skill", page)

    def read_disclosed(self, cache_path: str, **page: int) -> DisclosedContent:
        return self.disclosures.read(cache_path, **page)

    def history(self) -> tuple[Mapping[str, object], ...]:
        return self.disclosures.history()

    def activate_plugin(self, reference: str, session: RunContext) -> tuple[str, ...]:
        plugin = self.find_plugin(reference)
        activated = self._activate(session, self._activate_plugin, plugin)
        self._record(
            "plugin.activated",
            {"key": plugin.reference, "skills": activated, "run_id": session.identity.run_id},
        )
        return tuple(activated)

    def activate_skill(self, reference: str, session: RunContext) -> tuple[str, ...]:
        activated = self._activate(session, self._activate_skill, self.find_skill(reference))
        for key in activated:
            self._record("skill.activated", {"key": key, "run_id": session.identity.run_id})
        return activated

    def tools(self) -> tuple[Tool, ...]:
        """向模型公开插件和 Skill 的索引、披露与显式激活。"""
        return (
            Tool("list_plugins", "List a page from the plugin index", partial(self._list_tool, "plugin"), _list_schema()),
            Tool("read_plugin", "Read one bounded page of a plugin entry", partial(self._read_tool, "plugin"), _read_schema("plugin")),
            Tool("activate_plugin", "Activate a disclosed plugin for this run", partial(self._activate_tool, "plugin"), _activate_schema("plugin")),
            Tool("list_skills", "List a page from the Skill index", partial(self._list_tool, "skill"), _list_schema(skills=True)),
            Tool("read_skill", "Read one bounded page of a Skill", partial(self._read_tool, "skill"), _read_schema("skill")),
            Tool("read_skill_resource", "Read one bounded text resource inside a Skill", self._resource_tool, _read_schema("skill", resource=True)),
            self.disclosures.tool(),
            Tool("activate_skill", "Activate one disclosed Skill for this run", partial(self._activate_tool, "skill"), _activate_schema("skill")),
        )

    def create_plugin(
        self,
        plugin_id: str,
        body: str,
        *,
        description: str,
        version: str = "0.1.0",
        requires: Iterable[str] = (),
    ) -> Plugin:
        selected = _plugin_id(plugin_id)
        if selected in self.snapshot().plugins:
            raise ValueError(f"plugin already exists: plugin:{selected}")
        writable = self._writable()
        target = writable / _directory_name(selected)
        temporary = _temporary(writable, target.name)
        try:
            _write_manifest(temporary, selected, version, requires)
            metadata = {
                "name": selected.rsplit("/", 1)[-1],
                "description": description,
                "type": "prompt",
                "version": version,
            }
            _atomic_write(temporary / SKILL_FILE, format_skill(metadata, body).encode())
            _read_plugin(temporary, writable=True)
            _install_tree(temporary, target)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        created = _read_plugin(target, writable=True)
        self._record("plugin.created", _plugin_event(created))
        return created

    def create_skill(
        self,
        plugin: str,
        name: str,
        body: str,
        *,
        description: str,
        skill_type: str = "prompt",
        categories: Iterable[str] = (),
        requires: Iterable[str] = (),
        optional_tools: Iterable[str] = (),
        includes: Iterable[str] = (),
    ) -> Skill:
        owner, member = self.find_plugin(plugin), _member(name)
        reference = f"skill:{owner.plugin_id}/{member}"
        if reference in self.snapshot().skills:
            raise ValueError(f"skill already exists: {reference}")

        def edit(root: Path) -> None:
            metadata = {
                "name": member,
                "description": description,
                "type": skill_type,
                "version": "0.1.0",
                "categories": list(categories),
                "requires": list(requires),
                "optional_tools": list(optional_tools),
                "includes": list(includes),
            }
            _atomic_write(
                root / "skills" / member / SKILL_FILE,
                format_skill(metadata, body).encode(),
            )

        created = self._edit(owner, edit).skills[member]
        self._record("skill.created", _skill_event(created))
        return created

    def update_skill(
        self,
        reference: str,
        body: str,
        *,
        expected_sha256: str,
        description: str | None = None,
    ) -> Skill:
        skill = self.find_skill(reference)
        if skill.sha256 != expected_sha256:
            latest = self._build_snapshot()
            skill = latest.skills.get(_skill_reference(reference), skill)
            if skill.sha256 != expected_sha256:
                raise RuntimeError(f"skill changed since it was read: {skill.reference}")
            owner = latest.plugins[skill.plugin_id]
        else:
            owner = self.find_plugin(skill.plugin_id)
        relative = skill.path.relative_to(owner.root)

        def edit(root: Path) -> None:
            path = root / relative
            if _file_hash(path) != expected_sha256:
                raise RuntimeError(f"skill changed before write: {skill.reference}")
            metadata = dict(skill.metadata)
            metadata["version"] = _next_patch(skill.version)
            if description is not None:
                metadata["description"] = description
            _atomic_write(path, format_skill(metadata, body).encode())

        updated_owner = self._edit(owner, edit)
        updated = updated_owner.skills[skill.member_name]
        self._record(
            "skill.updated",
            {
                **_skill_event(updated),
                "before_sha256": skill.sha256,
                "plugin_sha256": updated_owner.sha256,
            },
        )
        return updated

    def derive_skill(self, source: str, plugin: str, name: str, body: str) -> Skill:
        original = self.find_skill(source)
        return self.create_skill(
            plugin,
            name,
            body,
            description=f"Derived from {original.reference}: {original.description}",
            skill_type=original.skill_type,
            categories=(*original.categories, f"derived/{original.reference}"),
            requires=original.requires,
            optional_tools=original.optional_tools,
            includes=original.includes,
        )

    def remove_skill(self, reference: str, *, expected_sha256: str) -> None:
        skill = self.find_skill(reference)
        if skill.member_name == "main":
            raise ValueError("remove the plugin instead of its main Skill")
        if skill.sha256 != expected_sha256:
            raise RuntimeError(f"skill changed since it was read: {skill.reference}")
        owner = self.find_plugin(skill.plugin_id)
        relative = skill.path.parent.relative_to(owner.root)

        def edit(root: Path) -> None:
            path = root / relative / SKILL_FILE
            if _file_hash(path) != expected_sha256:
                raise RuntimeError(f"skill changed before removal: {skill.reference}")
            shutil.rmtree(path.parent)

        self._edit(owner, edit)
        self._record("skill.removed", _skill_event(skill))

    def remove_plugin(self, reference: str, *, expected_sha256: str) -> None:
        plugin = self.find_plugin(reference)
        if not plugin.writable or self.writable_root is None:
            raise PermissionError(f"plugin is not in the writable scope: {plugin.reference}")
        _inside(self.writable_root, plugin.root)
        if _read_plugin(plugin.root, writable=True).sha256 != expected_sha256:
            raise RuntimeError(f"plugin changed since it was read: {plugin.reference}")
        removed = plugin.root.with_name(f".{plugin.root.name}.removed-{uuid4().hex}")
        os.replace(plugin.root, removed)
        try:
            shutil.rmtree(removed)
        except BaseException:
            os.replace(removed, plugin.root)
            raise
        self._record("plugin.removed", _plugin_event(plugin))

    def pack_plugin(self, reference: str, destination: str | Path) -> Path:
        return pack_skill(self.find_plugin(reference).entry, destination)

    def install_plugin(
        self, source: str | Path, *, expected_sha256: str | None = None
    ) -> Plugin:
        package = read_skill_package(source)
        if expected_sha256 is not None and package.sha256 != expected_sha256:
            raise ValueError("plugin package SHA-256 mismatch")
        writable = self._writable()
        staging = _temporary(writable, "install")
        extracted = staging / "plugin"
        try:
            install_skill_package(package, extracted)
            plugin = _read_plugin(extracted, writable=True)
            existing = self.snapshot().plugins.get(plugin.plugin_id)
            if existing is not None:
                if existing.version == plugin.version and existing.sha256 == plugin.sha256:
                    self._record("plugin.reused", _plugin_event(existing))
                    return existing
                raise ValueError(f"plugin identity conflicts: {plugin.reference}")
            target = writable / _directory_name(plugin.plugin_id)
            _install_tree(extracted, target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        installed = _read_plugin(target, writable=True)
        self._record("plugin.installed", _plugin_event(installed))
        return installed

    def reverse_dependencies(self, reference: str) -> tuple[str, ...]:
        selected = self.find_skill(reference).reference
        return tuple(
            sorted(
                skill.reference
                for skill in self.snapshot().skills.values()
                if selected in skill.includes
            )
        )

    def _build_snapshot(self) -> PluginSnapshot:
        plugins: dict[str, Plugin] = {}
        configured = [(root, False) for root in self.roots]
        if self.writable_root is not None:
            configured.append((self.writable_root, True))
        for root, writable in _discover(configured):
            plugin = _read_plugin(root, writable=writable)
            current = plugins.get(plugin.plugin_id)
            if current is None:
                plugins[plugin.plugin_id] = plugin
            elif current.version == plugin.version and current.sha256 == plugin.sha256:
                plugins[plugin.plugin_id] = replace(
                    current,
                    sources=tuple(dict.fromkeys((*current.sources, *plugin.sources))),
                )
                self._record("plugin.reused", _plugin_event(plugins[plugin.plugin_id]))
            elif plugin.writable and plugin.base_hash == current.sha256:
                plugins[plugin.plugin_id] = plugin
            else:
                raise ValueError(
                    f"plugin identity conflicts: {plugin.reference}; "
                    "version and content hash must both match"
                )
        _validate_graph(plugins)
        skills = {
            skill.reference: skill
            for plugin in plugins.values()
            for skill in plugin.skills.values()
        }
        _validate_includes(plugins, skills)
        digest = hashlib.sha256()
        for key, plugin in sorted(plugins.items()):
            digest.update(f"{key}\0{plugin.version}\0{plugin.sha256}\n".encode())
        return PluginSnapshot(
            digest.hexdigest(),
            MappingProxyType(dict(sorted(plugins.items()))),
            MappingProxyType(dict(sorted(skills.items()))),
        )

    def _edit(self, plugin: Plugin, action: Callable[[Path], None]) -> Plugin:
        writable = self._writable()
        target = plugin.root if plugin.writable else writable / _directory_name(plugin.plugin_id)
        if plugin.writable and _read_plugin(target, writable=True).sha256 != plugin.sha256:
            raise RuntimeError(f"plugin changed before write: {plugin.reference}")
        if not plugin.writable and target.exists():
            raise RuntimeError(f"plugin override already exists: {plugin.reference}")
        temporary = _temporary(writable, target.name)
        try:
            shutil.copytree(plugin.root, temporary, dirs_exist_ok=True)
            _write_manifest(
                temporary,
                plugin.plugin_id,
                _next_patch(plugin.version),
                plugin.requires,
                base_hash=plugin.base_hash if plugin.writable else plugin.sha256,
            )
            action(temporary)
            _read_plugin(temporary, writable=True)
            _replace_tree(temporary, target)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return _read_plugin(target, writable=True)

    def _activate_plugin(
        self,
        plugin: Plugin,
        session: RunContext,
        activated: list[str],
        stack: tuple[str, ...],
    ) -> None:
        if plugin.plugin_id in stack:
            raise ValueError(f"plugin dependency cycle: {' -> '.join((*stack, plugin.plugin_id))}")
        for required in plugin.requires:
            self._activate_plugin(self.find_plugin(required), session, activated, (*stack, plugin.plugin_id))
        self._activate_skill(plugin.entry, session, activated, ())

    def _activate_skill(
        self,
        skill: Skill,
        session: RunContext,
        activated: list[str],
        stack: tuple[str, ...],
    ) -> None:
        if skill.reference in session.active_skills:
            return
        if skill.reference in stack:
            raise ValueError(f"skill include cycle: {' -> '.join((*stack, skill.reference))}")
        available = session.values.get("available_tools", {})
        if not isinstance(available, Mapping):
            raise TypeError("run available_tools must be an object")
        for name in (*skill.requires, *skill.optional_tools):
            if name not in session.tools and isinstance(available.get(name), Tool):
                session.add_tool(available[name])
        missing = sorted(set(skill.requires) - set(session.tools))
        if missing:
            raise RuntimeError(
                f"skill requires unregistered tools: {skill.reference}: {', '.join(missing)}"
            )
        for included in skill.includes:
            self._activate_skill(self.find_skill(included), session, activated, (*stack, skill.reference))
        if len(skill.body) > 20_000:
            raise RuntimeError(f"Skill is too large for one activation: {skill.reference}")
        value = self.disclose_skill(skill.reference, max_characters=max(4_000, len(skill.body)))
        session.add_instruction(f"[Skill {skill.reference}]\n{value.content}")
        session.activate_skill(skill.reference)
        activated.append(skill.reference)

    def _activate(
        self,
        session: RunContext,
        action: Callable[[object, RunContext, list[str], tuple[str, ...]], None],
        content: object,
    ) -> tuple[str, ...]:
        activated: list[str] = []
        state = _activation_state(session)
        try:
            action(content, session, activated, ())
        except BaseException:
            _restore_activation(session, state)
            raise
        return tuple(activated)

    def _read(self, skill: Skill, kind: str | None, page: Mapping[str, int]) -> DisclosedContent:
        method = self.disclosures.disclose if kind else self.disclosures.preview
        value = method(skill.reference, skill.body, **page)
        if kind:
            key = f"plugin:{skill.plugin_id}" if kind == "plugin" else skill.reference
            self._record(f"{kind}.disclosed", {**_disclosure_event(value), "key": key})
        return value

    def _skill_is_disabled(self, skill: Skill) -> bool:
        return (
            skill.reference in self.disabled_skills
            or f"plugin:{skill.plugin_id}" in self.disabled_plugins
        )

    def _list_tool(self, kind: str, arguments: dict[str, object], _context: ToolContext) -> dict[str, object]:
        page = _integer(arguments.get("page", 1), "page", 1, 1_000_000)
        size = _integer(arguments.get("page_size", 20), "page_size", 1, 100)
        if kind == "plugin":
            return self.list_plugins(page=page, page_size=size).to_dict()
        return self.list_skills(page=page, page_size=size, plugin=_optional_text(arguments.get("plugin")), skill_type=_optional_text(arguments.get("type")), category=_optional_text(arguments.get("category"))).to_dict()

    def _read_tool(self, kind: str, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        reference = _text(arguments.get(kind), kind)
        method = self.disclose_plugin if kind == "plugin" else self.disclose_skill
        value = method(reference, **_tool_page(arguments))
        context.emit(f"{kind}.disclosed", _disclosure_event(value))
        return value.to_dict()

    def _resource_tool(self, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        skill = self.find_skill(_text(arguments.get("skill"), "skill"))
        relative, content = read_skill_resource_text(skill, _text(arguments.get("path"), "path"))
        value = self.disclosures.disclose(f"{skill.reference}:resource:{relative}", content, **_tool_page(arguments))
        event = {"key": skill.reference, "resource": relative, **_disclosure_event(value)}
        self._record("skill.resource_disclosed", event)
        context.emit("skill.resource_disclosed", event)
        return value.to_dict()

    def _activate_tool(self, kind: str, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        reference = _text(arguments.get(kind), kind)
        if kind == "plugin":
            plugin = self.find_plugin(reference)
            activated = self.activate_plugin(plugin.reference, context.session)
            context.emit("plugin.activated", {"key": plugin.reference, "skills": list(activated)})
            return {"plugin": plugin.reference, "activated": list(activated)}
        activated = self.activate_skill(reference, context.session)
        for key in activated:
            context.emit("skill.activated", {"key": key})
        return {"activated": list(activated)}

    def _writable(self) -> Path:
        if self.writable_root is None:
            raise RuntimeError("writable plugin root is not configured")
        self.writable_root.mkdir(parents=True, exist_ok=True)
        return self.writable_root

    def _record(self, event_type: str, data: Mapping[str, object]) -> None:
        if self.record_event is not None:
            self.record_event(event_type, data)


def _read_plugin(root: Path, *, writable: bool) -> Plugin:
    package = read_skill_package(root)
    path = root / SKILL_FILE
    provisional = parse_skill_text(
        path.read_text(encoding="utf-8"), path, validate_path=False
    )
    manifest_path = root / PLUGIN_FILE
    if manifest_path.is_file():
        with manifest_path.open("rb") as stream:
            manifest = tomllib.load(stream)
        unknown = sorted(set(manifest) - {"schema", "id", "version", "requires", "base_hash"})
        if unknown:
            raise ValueError(f"unknown plugin manifest fields: {', '.join(unknown)}")
        if manifest.get("schema") != 1:
            raise ValueError(f"unsupported plugin schema: {manifest.get('schema')}")
        plugin_id = _plugin_id(manifest.get("id"))
        version = _version(manifest.get("version"), "plugin version")
        requires = tuple(dict.fromkeys(_plugin_id(item) for item in _array(manifest.get("requires", []), "plugin requires")))
        base_hash = _hash_or_none(manifest.get("base_hash"))
    else:
        plugin_id, version, requires, base_hash = _plugin_id(provisional.name), _version(provisional.version, "plugin version"), (), None
    skills = {
        "main": parse_skill_text(
            path.read_text(encoding="utf-8"),
            path,
            plugin_id=plugin_id,
            validate_path=False,
        )
    }
    members = root / "skills"
    if members.exists() and not members.is_dir():
        raise ValueError(f"plugin skills path must be a directory: {members}")
    if members.is_dir():
        for child in sorted(members.iterdir()):
            if not child.is_dir() or not (child / SKILL_FILE).is_file():
                raise ValueError(f"invalid plugin Skill directory: {child}")
            member = _member(child.name)
            skill = parse_skill_text((child / SKILL_FILE).read_text(encoding="utf-8"), child / SKILL_FILE, plugin_id=plugin_id, member_name=member)
            if skill.name != member:
                raise ValueError(f"plugin Skill directory must match its name: {child}")
            skills[member] = skill
    expected = {skill.path for skill in skills.values()}
    if set(root.rglob(SKILL_FILE)) != expected:
        raise ValueError("plugin contains a Skill outside skills/<name>/SKILL.md")
    return Plugin(plugin_id, version, root.resolve(), requires, MappingProxyType(skills), package.sha256, base_hash, (root.resolve(),), writable)


def _discover(configured: Iterable[tuple[Path, bool]]) -> tuple[tuple[Path, bool], ...]:
    values: list[tuple[Path, bool]] = []
    for root, writable in configured:
        if not root.exists():
            continue
        if (root / SKILL_FILE).is_file():
            values.append((root, writable))
        else:
            values.extend((child, writable) for child in sorted(root.iterdir()) if child.is_dir() and (child / SKILL_FILE).is_file())
    return tuple(values)


def _validate_graph(plugins: Mapping[str, Plugin]) -> None:
    for plugin in plugins.values():
        missing = sorted(set(plugin.requires) - set(plugins))
        if missing:
            raise ValueError(f"plugin dependencies are missing: {plugin.reference}: {', '.join(missing)}")

    def visit(plugin_id: str, path: tuple[str, ...], done: set[str]) -> None:
        if plugin_id in path:
            raise ValueError(f"plugin dependency cycle: {' -> '.join((*path, plugin_id))}")
        if plugin_id not in done:
            for required in plugins[plugin_id].requires:
                visit(required, (*path, plugin_id), done)
            done.add(plugin_id)

    done: set[str] = set()
    for plugin_id in sorted(plugins):
        visit(plugin_id, (), done)


def _validate_includes(plugins: Mapping[str, Plugin], skills: Mapping[str, Skill]) -> None:
    for plugin in plugins.values():
        for skill in plugin.skills.values():
            for raw in skill.includes:
                reference = _skill_reference(raw)
                if reference not in skills:
                    raise ValueError(f"Skill include is missing: {skill.reference}: {reference}")
                owner = skills[reference].plugin_id
                if owner != plugin.plugin_id and owner not in plugin.requires:
                    raise ValueError(f"cross-plugin include requires plugin:{owner}: {skill.reference}")


def _write_manifest(root: Path, plugin_id: str, version: str, requires: Iterable[str], *, base_hash: str | None = None) -> None:
    values = tuple(dict.fromkeys(_plugin_id(item) for item in requires))
    lines = ["schema = 1", f"id = {json.dumps(_plugin_id(plugin_id))}", f"version = {json.dumps(_version(version, 'plugin version'))}", "requires = [" + ", ".join(json.dumps(item) for item in values) + "]"]
    if base_hash is not None:
        lines.append(f"base_hash = {json.dumps(_hash_or_none(base_hash))}")
    _atomic_write(root / PLUGIN_FILE, ("\n".join(lines) + "\n").encode())


def _activation_state(session: RunContext) -> tuple[list[str], dict[str, Tool], list[str], int, int]:
    ledger_size = 0 if session.ledger is None else len(session.ledger.entries())
    return list(session.instructions), dict(session.tools), list(session.active_skills), session.context_characters, ledger_size


def _restore_activation(session: RunContext, state: tuple[list[str], dict[str, Tool], list[str], int, int]) -> None:
    instructions, tools, skills, characters, ledger_size = state
    session.instructions[:], session.active_skills[:] = instructions, skills
    session.tools.clear()
    session.tools.update(tools)
    if session.ledger is not None:
        session.ledger.restore(ledger_size)
    session.context_characters = characters


def _install_tree(source: Path, target: Path) -> None:
    if target.exists():
        raise ValueError(f"plugin target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)


def _replace_tree(source: Path, target: Path) -> None:
    if not target.exists():
        _install_tree(source, target)
        return
    backup = target.with_name(f".{target.name}.backup-{uuid4().hex}")
    os.replace(target, backup)
    try:
        os.replace(source, target)
    except BaseException:
        os.replace(backup, target)
        raise
    shutil.rmtree(backup)


def _temporary(root: Path, name: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f".{name}.", dir=root))


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


def _plugin_id(value: object) -> str:
    selected = _text(value, "plugin ID").lower()
    if not PLUGIN_ID_PATTERN.fullmatch(selected):
        raise ValueError(f"invalid plugin ID: {selected}")
    return selected


def _member(value: object) -> str:
    selected = _text(value, "Skill member name").lower()
    if "/" in selected or not PLUGIN_ID_PATTERN.fullmatch(selected):
        raise ValueError(f"invalid Skill member name: {selected}")
    return selected


def _plugin_reference(value: object) -> str:
    selected = _text(value, "plugin reference").lower().removeprefix("plugin:")
    return f"plugin:{_plugin_id(selected)}"


def _skill_reference(value: object) -> str:
    selected = _text(value, "Skill reference").lower()
    if not selected.startswith("skill:"):
        raise ValueError(f"Skill reference must start with skill: {selected}")
    owner, separator, member = selected.removeprefix("skill:").rpartition("/")
    if not separator:
        raise ValueError(f"Skill reference must include a plugin and member: {selected}")
    return f"skill:{_plugin_id(owner)}/{_member(member)}"


def _version(value: object, name: str) -> str:
    selected = _text(value, name)
    if len(selected.split(".")) != 3 or not all(part.isdigit() for part in selected.split(".")):
        raise ValueError(f"{name} must use x.y.z: {selected}")
    return selected


def _next_patch(version: str) -> str:
    major, minor, patch = _version(version, "version").split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


def _hash_or_none(value: object) -> str | None:
    if value is None:
        return None
    selected = _text(value, "plugin base hash").lower()
    if len(selected) != 64 or any(item not in "0123456789abcdef" for item in selected):
        raise ValueError("plugin base hash must be a SHA-256 value")
    return selected


def _optional_text(value: object) -> str | None:
    return None if value is None else _text(value, "optional text")


def _array(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise TypeError(f"{name} must be an array of non-empty text")
    return tuple(item.strip() for item in value)


def _path_or_none(value: str | Path | None) -> Path | None:
    return None if value is None else Path(value).expanduser().resolve()


def _directory_name(plugin_id: str) -> str:
    return plugin_id.replace("/", "__")


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _page(values: list[object], page: int, size: int, render: Callable[[object], Mapping[str, object]]) -> DisclosurePage:
    if page < 1 or not 1 <= size <= 100:
        raise ValueError("page and page_size are outside their limits")
    start = (page - 1) * size
    items = tuple(MappingProxyType(dict(render(item))) for item in values[start : start + size])
    return DisclosurePage(items, page, size, len(values))


def _tool_page(arguments: Mapping[str, object]) -> dict[str, int]:
    return {"offset": _integer(arguments.get("offset", 0), "offset", 0, 10_000_000), "max_characters": _integer(arguments.get("max_characters", 4_000), "max_characters", 1, 20_000)}


def _plugin_event(plugin: Plugin) -> dict[str, object]:
    return {"key": plugin.reference, "version": plugin.version, "sha256": plugin.sha256, "sources": len(plugin.sources)}


def _skill_event(skill: Skill) -> dict[str, object]:
    return {"key": skill.reference, "version": skill.version, "sha256": skill.sha256}


def _disclosure_event(value: DisclosedContent) -> dict[str, object]:
    return {"key": value.reference, "cache_path": value.cache_path, "offset": value.offset, "next_offset": value.next_offset, "sha256": value.sha256}


def _list_schema(*, skills: bool = False) -> dict[str, object]:
    properties = {"page": {"type": "integer", "minimum": 1}, "page_size": {"type": "integer", "minimum": 1, "maximum": 100}}
    if skills:
        properties.update({"plugin": {"type": "string"}, "type": {"type": "string"}, "category": {"type": "string"}})
    return {"type": "object", "properties": properties}


def _read_schema(name: str, *, resource: bool = False) -> dict[str, object]:
    properties = {name: {"type": "string"}, "offset": {"type": "integer", "minimum": 0}, "max_characters": {"type": "integer", "minimum": 1, "maximum": 20_000}}
    required = [name]
    if resource:
        properties["path"], required = {"type": "string"}, [name, "path"]
    return {"type": "object", "required": required, "properties": properties}


def _activate_schema(name: str) -> dict[str, object]:
    return {"type": "object", "required": [name], "properties": {name: {"type": "string"}}}

"""中央资源库：统一发现、去重、披露和激活 Skill、MCP 与插件引用。"""

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
from core.model import Tool
from core.resources import DisclosedContent, DisclosurePage, ResourceCenter
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
MCP_FILE = "mcp.toml"
SKILL_META_FILE = "skill.toml"
ID_PATTERN = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?"
    r"(?:/[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)*"
)
RecordEvent = Callable[[str, Mapping[str, object]], object]


@dataclass(frozen=True)
class McpDefinition:
    """MCP 的被动说明，不包含命令、地址、密钥或连接动作。"""

    mcp_id: str
    version: str
    description: str
    tools: tuple[str, ...]
    root: Path
    sha256: str
    sources: tuple[Path, ...] = ()

    @property
    def reference(self) -> str:
        return f"mcp:{self.mcp_id}"

    def index_entry(self) -> dict[str, object]:
        return {
            "key": self.reference,
            "version": self.version,
            "description": self.description,
            "tools": list(self.tools),
            "sha256": self.sha256,
            "sources": len(self.sources),
        }


@dataclass(frozen=True)
class Plugin:
    """只保存 Skill、MCP 和其他插件的引用。"""

    plugin_id: str
    version: str
    description: str
    root: Path
    entry_skill: str
    skills: tuple[str, ...]
    included_plugins: tuple[str, ...]
    required_mcp_servers: tuple[str, ...]
    optional_mcp_servers: tuple[str, ...]
    sha256: str
    sources: tuple[Path, ...] = ()
    writable: bool = False

    @property
    def reference(self) -> str:
        return f"plugin:{self.plugin_id}"

    @property
    def skill_references(self) -> tuple[str, ...]:
        """返回入口和成员 Skill 的有序去重引用。"""
        return tuple(dict.fromkeys((self.entry_skill, *self.skills)))

    def index_entry(self) -> dict[str, object]:
        return {
            "key": self.reference,
            "version": self.version,
            "description": self.description,
            "entry_skill": self.entry_skill,
            "skills": list(self.skills),
            "skill_references": list(self.skill_references),
            "included_plugins": [f"plugin:{item}" for item in self.included_plugins],
            "required_mcp_servers": [f"mcp:{item}" for item in self.required_mcp_servers],
            "optional_mcp_servers": [f"mcp:{item}" for item in self.optional_mcp_servers],
            "sha256": self.sha256,
            "sources": len(self.sources),
        }


@dataclass(frozen=True)
class LibrarySnapshot:
    """一次运行使用的不可变中央资源快照。"""

    snapshot_id: str
    skills: Mapping[str, Skill]
    mcps: Mapping[str, McpDefinition]
    plugins: Mapping[str, Plugin]

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "skills": {
                key: {"version": value.version, "sha256": value.sha256}
                for key, value in self.skills.items()
            },
            "mcps": {
                key: {"version": value.version, "sha256": value.sha256}
                for key, value in self.mcps.items()
            },
            "plugins": {
                key: {"version": value.version, "sha256": value.sha256}
                for key, value in self.plugins.items()
            },
        }


class AgentLibrary:
    """所有可发现资源共用一个索引、快照、披露缓存和写入边界。"""

    def __init__(
        self,
        paths: Iterable[str | Path] = (),
        *,
        writable_root: str | Path | None = None,
        cache_root: str | Path | None = None,
        record_event: RecordEvent | None = None,
        cache_entries: int = 128,
        disabled_plugins: Iterable[str] = (),
        disabled_skills: Iterable[str] = (),
        disabled_mcp_servers: Iterable[str] = (),
    ) -> None:
        self.paths = tuple(_path(item) for item in paths)
        self.writable_root = _path_or_none(writable_root)
        self.cache_root = _path_or_none(cache_root)
        self.record_event = record_event
        self.cache_entries = _integer(cache_entries, "library cache entries", 1, 100_000)
        self.resources = ResourceCenter(
            cache_root=self.cache_root,
            max_entries=self.cache_entries,
            record_event=self._record,
        )
        self.disabled_plugins = frozenset(_plugin_reference(item) for item in disabled_plugins)
        self.disabled_skills = frozenset(_skill_reference(item) for item in disabled_skills)
        self.disabled_mcp_servers = frozenset(_mcp_reference(item) for item in disabled_mcp_servers)
        self._snapshot: LibrarySnapshot | None = None

    def refresh(self) -> None:
        """让下一次顶层运行重新发现资源。"""
        self._snapshot = None

    def snapshot(self) -> LibrarySnapshot:
        if self._snapshot is None:
            self._snapshot = self._build_snapshot()
        return self._snapshot

    def use_resource_center(self, resource_center: ResourceCenter) -> None:
        """Use the explicit central resource service for this library scope."""
        if not isinstance(resource_center, ResourceCenter):
            raise TypeError("library resource center must be a ResourceCenter")
        self.resources = resource_center

    def for_scope(
        self,
        user_id: str,
        agent_name: str,
        *,
        disabled_plugins: Iterable[str] | None = None,
        disabled_skills: Iterable[str] | None = None,
        disabled_mcp_servers: Iterable[str] | None = None,
    ) -> AgentLibrary:
        """共享只读资源，同时隔离用户和 Agent 的覆盖层与缓存。"""
        scope = f"{_text(user_id, 'user ID')}\0{_text(agent_name, 'Agent name')}"
        safe = hashlib.sha256(scope.encode()).hexdigest()[:24]
        writable = None
        cache = None
        if self.writable_root is not None:
            writable = self.writable_root / "users" / safe
        if self.cache_root is not None:
            cache = self.cache_root / "users" / safe
        return AgentLibrary(
            self.paths,
            writable_root=writable,
            cache_root=cache,
            record_event=self.record_event,
            cache_entries=self.cache_entries,
            disabled_plugins=self.disabled_plugins if disabled_plugins is None else disabled_plugins,
            disabled_skills=self.disabled_skills if disabled_skills is None else disabled_skills,
            disabled_mcp_servers=(
                self.disabled_mcp_servers
                if disabled_mcp_servers is None
                else disabled_mcp_servers
            ),
        )

    def list_plugins(self, *, page: int = 1, page_size: int = 20) -> DisclosurePage:
        values = sorted(
            (item for item in self.snapshot().plugins.values() if item.reference not in self.disabled_plugins),
            key=lambda item: item.reference,
        )
        return _page(values, page, page_size, lambda item: item.index_entry())

    def resource_index(self, *, page_size: int = 20) -> dict[str, object]:
        """Return one bounded index for every discoverable resource kind."""
        if not 1 <= page_size <= 100:
            raise ValueError("resource index page size must be between 1 and 100")
        return {
            "plugins": self.list_plugins(page=1, page_size=page_size).to_dict(),
            "skills": self.list_skills(page=1, page_size=page_size).to_dict(),
            "mcp_servers": self.list_mcp_servers(
                page=1, page_size=page_size
            ).to_dict(),
        }

    def list_skills(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        plugin: str | None = None,
        skill_type: str | None = None,
        category: str | None = None,
    ) -> DisclosurePage:
        values = sorted(
            (item for item in self.snapshot().skills.values() if not self._skill_is_disabled(item)),
            key=lambda item: item.reference,
        )
        if plugin:
            selected = self.find_plugin(plugin)
            allowed = {selected.entry_skill, *selected.skills}
            values = [item for item in values if item.reference in allowed]
        if skill_type:
            values = [item for item in values if item.skill_type == skill_type]
        if category:
            values = [item for item in values if category in item.categories]
        return _page(
            values,
            page,
            page_size,
            lambda item: {
                **item.index_entry(),
                "plugins": list(self.plugins_using_skill(item.reference)),
            },
        )

    def list_mcp_servers(self, *, page: int = 1, page_size: int = 20) -> DisclosurePage:
        values = sorted(
            (item for item in self.snapshot().mcps.values() if item.reference not in self.disabled_mcp_servers),
            key=lambda item: item.reference,
        )
        return _page(values, page, page_size, lambda item: item.index_entry())

    def find_plugin(self, reference: str) -> Plugin:
        key = _plugin_reference(reference)
        try:
            value = self.snapshot().plugins[key.removeprefix("plugin:")]
        except KeyError as error:
            raise KeyError(f"plugin not found: {key}") from error
        if key in self.disabled_plugins:
            raise PermissionError(f"plugin is disabled: {key}")
        return value

    def find_skill(self, reference: str) -> Skill:
        key = _skill_reference(reference)
        try:
            value = self.snapshot().skills[key]
        except KeyError as error:
            raise KeyError(f"Skill not found: {key}") from error
        if self._skill_is_disabled(value):
            raise PermissionError(f"Skill is disabled: {key}")
        return value

    def find_mcp_server(self, reference: str) -> McpDefinition:
        key = _mcp_reference(reference)
        try:
            value = self.snapshot().mcps[key.removeprefix("mcp:")]
        except KeyError as error:
            raise KeyError(f"MCP server not found: {key}") from error
        if key in self.disabled_mcp_servers:
            raise PermissionError(f"MCP server is disabled: {key}")
        return value

    def plugin_skill_references(self, reference: str) -> tuple[str, ...]:
        """解析插件及其嵌套插件的全部 Skill，并按引用去重。"""
        result: list[str] = []
        visited: set[str] = set()

        def visit(plugin: Plugin) -> None:
            if plugin.plugin_id in visited:
                return
            visited.add(plugin.plugin_id)
            for skill in plugin.skill_references:
                if skill not in result:
                    result.append(skill)
            for included in plugin.included_plugins:
                visit(self.find_plugin(included))

        visit(self.find_plugin(reference))
        return tuple(result)

    def plugins_using_skill(self, reference: str) -> tuple[str, ...]:
        """查找直接或间接引用指定 Skill 的插件。"""
        selected = self.find_skill(reference).reference
        return tuple(
            sorted(
                plugin.reference
                for plugin in self.snapshot().plugins.values()
                if selected in self.plugin_skill_references(plugin.reference)
            )
        )

    def preview_plugin(self, reference: str, **page: int) -> DisclosedContent:
        return self._read(self.find_plugin(reference).entry_skill, None, page)

    def preview_skill(self, reference: str, **page: int) -> DisclosedContent:
        return self._read(self.find_skill(reference), None, page)

    def disclose_plugin(self, reference: str, **page: int) -> DisclosedContent:
        return self._read(self.find_plugin(reference).entry_skill, "plugin", page)

    def disclose_skill(self, reference: str, **page: int) -> DisclosedContent:
        return self._read(self.find_skill(reference), "skill", page)

    def read_disclosed(self, cache_path: str, **page: int) -> DisclosedContent:
        return self.resources.read_cached_resource(cache_path, **page)

    def history(self) -> tuple[Mapping[str, object], ...]:
        return self.resources.read_history()

    def activate_plugin(self, reference: str, session: RunContext) -> tuple[str, ...]:
        plugin = self.find_plugin(reference)
        unavailable: list[tuple[str, str]] = []

        def activate(
            content: object,
            run: RunContext,
            activated_skills: list[str],
            stack: tuple[str, ...],
        ) -> None:
            if not isinstance(content, Plugin):
                raise TypeError("plugin activation requires a Plugin")
            self._activate_plugin(
                content, run, activated_skills, stack, unavailable
            )

        activated = self._activate(session, activate, plugin)
        for plugin_reference, mcp_reference in unavailable:
            self._record(
                "mcp.unavailable",
                {
                    "key": mcp_reference,
                    "plugin": plugin_reference,
                    "run_id": session.identity.run_id,
                },
            )
        self._record(
            "plugin.activated",
            {"key": plugin.reference, "skills": activated, "run_id": session.identity.run_id},
        )
        return activated

    def activate_skill(self, reference: str, session: RunContext) -> tuple[str, ...]:
        activated = self._activate(session, self._activate_skill, self.find_skill(reference))
        for key in activated:
            self._record("skill.activated", {"key": key, "run_id": session.identity.run_id})
        return activated

    def tools(self) -> tuple[Tool, ...]:
        """只公开发现、读取和显式激活；不会自动启动 MCP。"""
        return (
            Tool("list_plugins", "List a page from the plugin index", partial(self._list_tool, "plugin"), _list_schema()),
            Tool("read_plugin", "Read one bounded page of a plugin", partial(self._read_tool, "plugin"), _read_schema("plugin")),
            Tool("activate_plugin", "Activate one disclosed plugin", partial(self._activate_tool, "plugin"), _activate_schema("plugin")),
            Tool("list_skills", "List a page from the Skill index", partial(self._list_tool, "skill"), _list_schema(skills=True)),
            Tool("read_skill", "Read one bounded page of a Skill", partial(self._read_tool, "skill"), _read_schema("skill")),
            Tool("read_skill_resource", "Read one bounded text resource inside a Skill", self._resource_tool, _read_schema("skill", resource=True)),
            Tool("list_mcp_servers", "List passive MCP server definitions", partial(self._list_tool, "mcp"), _list_schema()),
            Tool("read_mcp_server", "Read one passive MCP server definition", partial(self._read_tool, "mcp"), _read_schema("mcp")),
            self.resources.create_read_tool(),
            Tool("activate_skill", "Activate one disclosed Skill", partial(self._activate_tool, "skill"), _activate_schema("skill")),
        )

    def create_skill(
        self,
        skill_id: str,
        body: str,
        *,
        description: str,
        skill_type: str = "prompt",
        version: str = "0.1.0",
        categories: Iterable[str] = (),
        requires: Iterable[str] = (),
        optional_tools: Iterable[str] = (),
        includes: Iterable[str] = (),
    ) -> Skill:
        """在中央可写库创建一个独立 Skill。"""
        selected = _resource_id(skill_id, "Skill ID")
        if selected in self.snapshot().skills:
            raise ValueError(f"Skill already exists: skill:{selected}")
        selected_includes = _references(includes, "skill", "Skill includes")
        if f"skill:{selected}" in selected_includes:
            raise ValueError(f"Skill include cycle: skill:{selected}")
        for reference in selected_includes:
            self.find_skill(reference)
        root = self._writable() / "skills" / selected
        if root.exists():
            raise ValueError(f"Skill target already exists: {root}")
        metadata = {
            "name": selected.rsplit("/", 1)[-1],
            "description": description,
            "type": skill_type,
            "version": version,
            "categories": list(categories),
            "requires": list(requires),
            "optional_tools": list(optional_tools),
            "includes": list(selected_includes),
        }
        _atomic_write(root / SKILL_FILE, format_skill(metadata, body).encode())
        self.refresh()
        created = self.find_skill(f"skill:{selected}")
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
        """只更新中央可写覆盖层，并拒绝过期哈希。"""
        skill = self.find_skill(reference)
        if skill.sha256 != expected_sha256 or _file_hash(skill.path) != expected_sha256:
            raise RuntimeError(f"Skill changed before write: {skill.reference}")
        if _skill_content_hash(skill.path) != skill.package_sha256:
            raise RuntimeError(f"Skill resources changed before write: {skill.reference}")
        metadata = dict(skill.metadata)
        metadata["version"] = _next_patch(skill.version)
        if description is not None:
            metadata["description"] = description
        content = format_skill(metadata, body).encode()
        if skill.writable:
            _atomic_write(skill.path, content)
        else:
            self._write_skill_override(skill, content)
        self.refresh()
        updated = self.find_skill(skill.reference)
        self._record("skill.updated", {**_skill_event(updated), "before_sha256": expected_sha256})
        return updated

    def _write_skill_override(self, skill: Skill, content: bytes) -> None:
        target = self._writable() / "skills" / skill.skill_id
        if target.exists():
            raise RuntimeError(f"Skill override already exists: {skill.reference}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent)
        )
        staged = temporary / "skill"
        try:
            _copy_skill_content(skill.root, staged)
            if _skill_content_hash(staged / SKILL_FILE) != skill.package_sha256:
                raise RuntimeError(f"Skill changed while copying: {skill.reference}")
            _write_skill_metadata(staged, skill.skill_id, skill.package_sha256)
            _atomic_write(staged / SKILL_FILE, content)
            os.replace(staged, target)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def remove_skill(self, reference: str, *, expected_sha256: str) -> None:
        skill = self.find_skill(reference)
        if not skill.writable:
            raise PermissionError(f"Skill is not in the writable scope: {skill.reference}")
        if skill.sha256 != expected_sha256:
            raise RuntimeError(f"Skill changed since it was read: {skill.reference}")
        dependents = self.reverse_dependencies(skill.reference)
        descendants = tuple(
            item.reference
            for item in self.snapshot().skills.values()
            if item.reference != skill.reference and item.root.is_relative_to(skill.root)
        )
        plugins = self.plugins_using_skill(skill.reference)
        if dependents or plugins or descendants:
            references = ", ".join((*plugins, *dependents, *descendants))
            raise RuntimeError(f"Skill is still referenced: {skill.reference}: {references}")
        _inside(self._writable(), skill.root)
        shutil.rmtree(skill.root)
        self.refresh()
        self._record("skill.removed", _skill_event(skill))

    def derive_skill(self, source: str, skill_id: str, body: str) -> Skill:
        original = self.find_skill(source)
        return self.create_skill(
            skill_id,
            body,
            description=f"Derived from {original.reference}: {original.description}",
            skill_type=original.skill_type,
            categories=(*original.categories, f"derived/{original.reference}"),
            requires=original.requires,
            optional_tools=original.optional_tools,
            includes=original.includes,
        )

    def pack_skill(self, reference: str, destination: str | Path) -> Path:
        return pack_skill(self.find_skill(reference), destination)

    def install_skill(self, source: str | Path, *, expected_sha256: str | None = None, skill_id: str | None = None) -> Skill:
        package = read_skill_package(source)
        if expected_sha256 is not None and package.sha256 != expected_sha256:
            raise ValueError("Skill package SHA-256 mismatch")
        provisional = parse_skill_text(package.skill_content.decode(), package.skill_path, validate_path=False)
        selected = _resource_id(skill_id or provisional.name, "Skill ID")
        if selected.rsplit("/", 1)[-1] != provisional.name:
            raise ValueError("installed Skill name must match the target directory")
        if selected in self.snapshot().skills:
            current = self.snapshot().skills[selected]
            if current.package_sha256 == package.sha256:
                return current
            raise ValueError(f"Skill identity conflicts: skill:{selected}")
        if f"skill:{selected}" in provisional.includes:
            raise ValueError(f"Skill include cycle: skill:{selected}")
        for reference in provisional.includes:
            self.find_skill(reference)
        target = self._writable() / "skills" / selected
        install_skill_package(package, target)
        self.refresh()
        installed = self.find_skill(f"skill:{selected}")
        self._record("skill.installed", _skill_event(installed))
        return installed

    def create_plugin(
        self,
        plugin_id: str,
        *,
        description: str,
        entry_skill: str,
        skills: Iterable[str] = (),
        included_plugins: Iterable[str] = (),
        required_mcp_servers: Iterable[str] = (),
        optional_mcp_servers: Iterable[str] = (),
        version: str = "0.1.0",
    ) -> Plugin:
        selected = _resource_id(plugin_id, "plugin ID")
        if selected in self.snapshot().plugins:
            raise ValueError(f"plugin already exists: plugin:{selected}")
        selected_entry = self.find_skill(entry_skill).reference
        selected_skills = tuple(self.find_skill(item).reference for item in skills)
        selected_plugins = tuple(
            self.find_plugin(item).reference for item in included_plugins
        )
        selected_required_mcp = tuple(
            self.find_mcp_server(item).reference for item in required_mcp_servers
        )
        selected_optional_mcp = tuple(
            self.find_mcp_server(item).reference for item in optional_mcp_servers
        )
        if selected_entry in selected_skills:
            raise ValueError("plugin entry_skill must not be repeated in skills")
        overlap = sorted(set(selected_required_mcp) & set(selected_optional_mcp))
        if overlap:
            raise ValueError(
                f"plugin MCP references cannot be both required and optional: {', '.join(overlap)}"
            )
        target = self._writable() / "plugins" / selected
        _write_plugin_manifest(
            target,
            selected,
            version,
            description,
            selected_entry,
            selected_skills,
            selected_plugins,
            selected_required_mcp,
            selected_optional_mcp,
        )
        self.refresh()
        created = self.find_plugin(f"plugin:{selected}")
        self._record("plugin.created", _plugin_event(created))
        return created

    def remove_plugin(self, reference: str, *, expected_sha256: str) -> None:
        plugin = self.find_plugin(reference)
        if not plugin.writable:
            raise PermissionError(f"plugin is not in the writable scope: {plugin.reference}")
        if plugin.sha256 != expected_sha256:
            raise RuntimeError(f"plugin changed since it was read: {plugin.reference}")
        users = tuple(
            item.reference
            for item in self.snapshot().plugins.values()
            if plugin.plugin_id in item.included_plugins
        )
        if users:
            raise RuntimeError(f"plugin is still included: {plugin.reference}: {', '.join(users)}")
        _inside(self._writable(), plugin.root)
        shutil.rmtree(plugin.root)
        self.refresh()
        self._record("plugin.removed", _plugin_event(plugin))

    def reverse_dependencies(self, reference: str) -> tuple[str, ...]:
        selected = self.find_skill(reference).reference
        return tuple(sorted(
            skill.reference for skill in self.snapshot().skills.values() if selected in skill.includes
        ))

    def _build_snapshot(self) -> LibrarySnapshot:
        skills: dict[str, Skill] = {}
        mcps: dict[str, McpDefinition] = {}
        plugins: dict[str, Plugin] = {}
        configured = [(root, False) for root in self.paths]
        if self.writable_root is not None:
            configured.append((self.writable_root, True))
        for root, writable in configured:
            for path in _discover_skill_files(root):
                item = _read_skill(path, root, writable=writable)
                _merge_skill(skills, item, self._record)
            for path in _discover_mcp_files(root):
                item = _read_mcp(path, root)
                _merge_resource(mcps, item.mcp_id, item, "MCP", self._record)
            for path in _discover_plugin_files(root):
                item = _read_plugin_manifest(path, root, writable=writable)
                _merge_resource(plugins, item.plugin_id, item, "plugin", self._record)
        _validate_plugins(plugins, skills, mcps)
        digest = hashlib.sha256()
        for kind, values in (("skill", skills), ("mcp", mcps), ("plugin", plugins)):
            for key, value in sorted(values.items()):
                digest.update(f"{kind}\0{key}\0{value.version}\0{value.sha256}\n".encode())
        return LibrarySnapshot(
            digest.hexdigest(),
            MappingProxyType(dict(sorted(skills.items()))),
            MappingProxyType(dict(sorted(mcps.items()))),
            MappingProxyType(dict(sorted(plugins.items()))),
        )

    def _activate_plugin(
        self,
        plugin: Plugin,
        session: RunContext,
        activated: list[str],
        stack: tuple[str, ...],
        unavailable: list[tuple[str, str]],
    ) -> None:
        if plugin.plugin_id in stack:
            raise ValueError(f"plugin include cycle: {' -> '.join((*stack, plugin.plugin_id))}")
        self._mount_plugin_mcp(plugin, session, unavailable)
        for included in plugin.included_plugins:
            self._activate_plugin(
                self.find_plugin(included),
                session,
                activated,
                (*stack, plugin.plugin_id),
                unavailable,
            )
        for reference in plugin.skill_references:
            self._activate_skill(self.find_skill(reference), session, activated, ())

    def _activate_skill(self, skill: Skill, session: RunContext, activated: list[str], stack: tuple[str, ...]) -> None:
        if skill.reference in session.active_skills:
            return
        if skill.reference in stack:
            raise ValueError(f"Skill include cycle: {' -> '.join((*stack, skill.reference))}")
        registry = session.resources.tool_registry
        if registry is None:  # pragma: no cover - RunContext always creates it
            raise RuntimeError("run tool registry is unavailable")
        for name in (*skill.requires, *skill.optional_tools):
            if name not in session.tools and isinstance(registry.available.get(name), Tool):
                registry.activate(name)
        missing = sorted(set(skill.requires) - set(session.tools))
        if missing:
            raise RuntimeError(f"Skill requires unregistered tools: {skill.reference}: {', '.join(missing)}")
        for included in skill.includes:
            self._activate_skill(self.find_skill(included), session, activated, (*stack, skill.reference))
        if len(skill.body) > 20_000:
            raise RuntimeError(f"Skill is too large for one activation: {skill.reference}")
        value = self.disclose_skill(skill.reference, max_characters=max(4_000, len(skill.body)))
        session.add_instruction(f"[Skill {skill.reference}]\n{value.content}")
        session.activate_skill(skill.reference)
        activated.append(skill.reference)

    def _activate(self, session: RunContext, action: Callable[[object, RunContext, list[str], tuple[str, ...]], None], content: object) -> tuple[str, ...]:
        activated: list[str] = []
        state = _activation_state(session)
        try:
            action(content, session, activated, ())
        except BaseException:
            _restore_activation(session, state)
            raise
        return tuple(activated)

    def _read(self, skill: Skill | str, kind: str | None, page: Mapping[str, int]) -> DisclosedContent:
        if isinstance(skill, str):
            skill = self.find_skill(skill)
        method = (
            self.resources.disclose_resource
            if kind
            else self.resources.preview_resource
        )
        value = method(skill.reference, skill.body, **page)
        if kind:
            self._record(f"{kind}.disclosed", {**_disclosure_event(value), "key": skill.reference})
        return value

    def _mount_plugin_mcp(
        self,
        plugin: Plugin,
        session: RunContext,
        unavailable: list[tuple[str, str]],
    ) -> None:
        bindings = session.resources.mcp_tools_by_server or {}
        if not isinstance(bindings, Mapping):
            raise TypeError("run MCP tools by server must be an object")
        required = tuple(f"mcp:{item}" for item in plugin.required_mcp_servers)
        optional = tuple(f"mcp:{item}" for item in plugin.optional_mcp_servers)
        missing = [reference for reference in required if reference not in bindings]
        if missing:
            raise RuntimeError(f"required MCP servers are not explicitly connected: {plugin.reference}: {', '.join(missing)}")
        for reference in (*required, *optional):
            tools = bindings.get(reference)
            if tools is None:
                unavailable.append((plugin.reference, reference))
                continue
            if not isinstance(tools, (list, tuple)) or any(
                not isinstance(tool, Tool) for tool in tools
            ):
                raise TypeError(f"run MCP tools must contain Tool values: {reference}")
            for tool in tools:
                session.add_tool(tool)

    def _skill_is_disabled(self, skill: Skill) -> bool:
        return skill.reference in self.disabled_skills

    def _list_tool(self, kind: str, arguments: dict[str, object], _context: ToolContext) -> dict[str, object]:
        page = _integer(arguments.get("page", 1), "page", 1, 1_000_000)
        size = _integer(arguments.get("page_size", 20), "page_size", 1, 100)
        if kind == "plugin":
            value = self.list_plugins(page=page, page_size=size)
        elif kind == "skill":
            value = self.list_skills(page=page, page_size=size, plugin=_optional_text(arguments.get("plugin")), skill_type=_optional_text(arguments.get("type")), category=_optional_text(arguments.get("category")))
        else:
            value = self.list_mcp_servers(page=page, page_size=size)
        return value.to_dict()

    def _read_tool(self, kind: str, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        reference = _text(arguments.get(kind), kind)
        if kind == "plugin":
            value = self.disclose_plugin(reference, **_tool_page(arguments))
        elif kind == "skill":
            value = self.disclose_skill(reference, **_tool_page(arguments))
        else:
            definition = self.find_mcp_server(reference)
            value = definition.index_entry()
        if isinstance(value, DisclosedContent):
            context.emit(f"{kind}.disclosed", _disclosure_event(value))
            return value.to_dict()
        return value

    def _resource_tool(self, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        skill = self.find_skill(_text(arguments.get("skill"), "skill"))
        relative, content = read_skill_resource_text(skill, _text(arguments.get("path"), "path"))
        value = self.resources.disclose_resource(
            f"{skill.reference}:resource:{relative}",
            content,
            **_tool_page(arguments),
        )
        event = {"key": skill.reference, "resource": relative, **_disclosure_event(value)}
        self._record("skill.resource_disclosed", event)
        context.emit("skill.resource_disclosed", event)
        return value.to_dict()

    def _activate_tool(self, kind: str, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        reference = _text(arguments.get(kind), kind)
        if kind == "plugin":
            activated = self.activate_plugin(reference, context.session)
            return {"plugin": self.find_plugin(reference).reference, "activated": list(activated)}
        activated = self.activate_skill(reference, context.session)
        return {"activated": list(activated)}

    def _writable(self) -> Path:
        if self.writable_root is None:
            raise RuntimeError("writable library root is not configured")
        self.writable_root.mkdir(parents=True, exist_ok=True)
        return self.writable_root

    def _record(self, event_type: str, data: Mapping[str, object]) -> None:
        if self.record_event is not None:
            self.record_event(event_type, data)


def _read_skill(path: Path, library_root: Path, *, writable: bool) -> Skill:
    relative = path.parent.relative_to(library_root / "skills")
    skill_id = _resource_id("/".join(relative.parts), "Skill ID")
    base_hash = None
    metadata_path = path.parent / SKILL_META_FILE
    if metadata_path.is_file():
        with metadata_path.open("rb") as stream:
            metadata = tomllib.load(stream)
        _unknown(metadata, {"schema", "id", "base_hash"}, "Skill metadata")
        if metadata.get("schema") != 1:
            raise ValueError(f"unsupported Skill metadata schema: {metadata.get('schema')}")
        if _resource_id(metadata.get("id"), "Skill ID") != skill_id:
            raise ValueError(f"Skill metadata ID must match its directory: {skill_id}")
        base_hash = _hash(metadata.get("base_hash"), "Skill base hash")
    return parse_skill_text(
        path.read_text(encoding="utf-8"),
        path,
        skill_id=skill_id,
        package_sha256=_skill_content_hash(path),
        base_hash=base_hash,
        sources=(path.parent.resolve(),),
        writable=writable,
    )


def _read_mcp(path: Path, library_root: Path) -> McpDefinition:
    relative = path.parent.relative_to(library_root / "mcps")
    mcp_id = _resource_id("/".join(relative.parts), "MCP ID")
    with path.open("rb") as stream:
        value = tomllib.load(stream)
    _unknown(value, {"schema", "id", "version", "description", "tools"}, "MCP manifest")
    if value.get("schema") != 1:
        raise ValueError(f"unsupported MCP schema: {value.get('schema')}")
    if _resource_id(value.get("id"), "MCP ID") != mcp_id:
        raise ValueError(f"MCP ID must match its directory: {mcp_id}")
    raw_tools = value.get("tools", [])
    tools = _text_array(raw_tools, "MCP tools")
    if len(tools) != len(raw_tools):
        raise ValueError(f"MCP tools must not contain duplicates: {mcp_id}")
    return McpDefinition(
        mcp_id,
        _version(value.get("version"), "MCP version"),
        _text(value.get("description"), "MCP description"),
        tools,
        path.parent.resolve(),
        _directory_hash(path.parent),
        (path.parent.resolve(),),
    )


def _read_plugin_manifest(path: Path, library_root: Path, *, writable: bool) -> Plugin:
    relative = path.parent.relative_to(library_root / "plugins")
    plugin_id = _resource_id("/".join(relative.parts), "plugin ID")
    with path.open("rb") as stream:
        value = tomllib.load(stream)
    _unknown(value, {"schema", "id", "version", "description", "entry_skill", "skills", "included_plugins", "required_mcp_servers", "optional_mcp_servers"}, "plugin manifest")
    if value.get("schema") != 1:
        raise ValueError(f"unsupported plugin schema: {value.get('schema')}")
    if _resource_id(value.get("id"), "plugin ID") != plugin_id:
        raise ValueError(f"plugin ID must match its directory: {plugin_id}")
    extra_files = tuple(
        item for item in path.parent.rglob("*") if item.is_file() and item.name != PLUGIN_FILE
    )
    if extra_files:
        raise ValueError(
            f"plugin directory may contain only {PLUGIN_FILE}: {plugin_id}"
        )
    skills = _references(value.get("skills", []), "skill", "plugin skills")
    entry = _skill_reference(value.get("entry_skill"))
    if entry in skills:
        raise ValueError(f"plugin entry_skill must not be repeated in skills: {plugin_id}")
    for label, raw, normalized in (
        ("plugin skills", value.get("skills", []), skills),
        ("included plugins", value.get("included_plugins", []), _references(value.get("included_plugins", []), "plugin", "included plugins")),
        ("required MCP servers", value.get("required_mcp_servers", []), _references(value.get("required_mcp_servers", []), "mcp", "required MCP servers")),
        ("optional MCP servers", value.get("optional_mcp_servers", []), _references(value.get("optional_mcp_servers", []), "mcp", "optional MCP servers")),
    ):
        if isinstance(raw, list) and len(normalized) != len(raw):
            raise ValueError(f"{label} must not contain duplicates: {plugin_id}")
    return Plugin(
        plugin_id,
        _version(value.get("version"), "plugin version"),
        _text(value.get("description"), "plugin description"),
        path.parent.resolve(),
        entry,
        tuple(dict.fromkeys(skills)),
        tuple(item.removeprefix("plugin:") for item in _references(value.get("included_plugins", []), "plugin", "included plugins")),
        tuple(item.removeprefix("mcp:") for item in _references(value.get("required_mcp_servers", []), "mcp", "required MCP servers")),
        tuple(item.removeprefix("mcp:") for item in _references(value.get("optional_mcp_servers", []), "mcp", "optional MCP servers")),
        _directory_hash(path.parent),
        (path.parent.resolve(),),
        writable,
    )


def _discover_skill_files(root: Path) -> tuple[Path, ...]:
    directory = root / "skills"
    return tuple(sorted(path for path in directory.rglob(SKILL_FILE) if path.is_file())) if directory.is_dir() else ()


def _discover_mcp_files(root: Path) -> tuple[Path, ...]:
    directory = root / "mcps"
    return tuple(sorted(path for path in directory.rglob(MCP_FILE) if path.is_file())) if directory.is_dir() else ()


def _discover_plugin_files(root: Path) -> tuple[Path, ...]:
    directory = root / "plugins"
    return tuple(sorted(path for path in directory.rglob(PLUGIN_FILE) if path.is_file())) if directory.is_dir() else ()


def _merge_resource(values: dict[str, object], key: str, item: object, label: str, record: RecordEvent | None) -> None:
    current = values.get(key)
    if current is None:
        values[key] = item
        return
    if getattr(current, "version") == getattr(item, "version") and getattr(current, "sha256") == getattr(item, "sha256"):
        values[key] = replace(current, sources=tuple(dict.fromkeys((*current.sources, *item.sources))))
        if record is not None:
            record(f"{label.lower()}.reused", {"key": getattr(current, "reference"), "version": getattr(current, "version"), "sha256": getattr(current, "sha256"), "sources": len(values[key].sources)})
        return
    raise ValueError(f"{label} identity conflicts: {getattr(item, 'reference')}; version and content hash must both match")


def _merge_skill(values: dict[str, Skill], item: Skill, record: RecordEvent | None) -> None:
    current = values.get(item.reference)
    if current is None:
        values[item.reference] = item
        return
    if current.version == item.version and current.package_sha256 == item.package_sha256:
        values[item.reference] = replace(
            current,
            sources=tuple(dict.fromkeys((*current.sources, *item.sources))),
        )
        if record is not None:
            record("skill.reused", _skill_event(values[item.reference]))
        return
    if item.writable and item.base_hash == current.package_sha256:
        values[item.reference] = item
        return
    raise ValueError(
        f"Skill identity conflicts: {item.reference}; version and content hash must both match"
    )


def _validate_plugins(plugins: Mapping[str, Plugin], skills: Mapping[str, Skill], mcps: Mapping[str, McpDefinition]) -> None:
    for plugin in plugins.values():
        missing_skills = sorted(set((plugin.entry_skill, *plugin.skills)) - set(skills))
        if missing_skills:
            raise ValueError(f"plugin Skills are missing: {plugin.reference}: {', '.join(missing_skills)}")
        missing_plugins = sorted(set(plugin.included_plugins) - set(plugins))
        if missing_plugins:
            raise ValueError(f"included plugins are missing: {plugin.reference}: {', '.join(missing_plugins)}")
        missing_mcp = sorted(
            set((*plugin.required_mcp_servers, *plugin.optional_mcp_servers))
            - set(mcps)
        )
        if missing_mcp:
            raise ValueError(f"MCP definitions are missing: {plugin.reference}: {', '.join(missing_mcp)}")
    _validate_cycles(plugins)
    _validate_skill_includes(skills)


def _validate_cycles(plugins: Mapping[str, Plugin]) -> None:
    def visit(key: str, path: tuple[str, ...], done: set[str]) -> None:
        if key in path:
            raise ValueError(f"plugin include cycle: {' -> '.join((*path, key))}")
        if key in done:
            return
        for child in plugins[key].included_plugins:
            visit(child, (*path, key), done)
        done.add(key)
    done: set[str] = set()
    for key in sorted(plugins):
        visit(key, (), done)


def _validate_skill_includes(skills: Mapping[str, Skill]) -> None:
    def visit(key: str, path: tuple[str, ...], done: set[str]) -> None:
        if key in path:
            raise ValueError(f"Skill include cycle: {' -> '.join((*path, key))}")
        if key in done:
            return
        for child in skills[key].includes:
            selected = _skill_reference(child)
            if selected not in skills:
                raise ValueError(f"Skill include is missing: {key}: {selected}")
            visit(selected, (*path, key), done)
        done.add(key)
    done: set[str] = set()
    for key in sorted(skills):
        visit(key, (), done)


def _write_plugin_manifest(root: Path, plugin_id: str, version: str, description: str, entry_skill: str, skills: Iterable[str], included_plugins: Iterable[str], required_mcp_servers: Iterable[str], optional_mcp_servers: Iterable[str]) -> None:
    values = {
        "schema": 1,
        "id": plugin_id,
        "version": _version(version, "plugin version"),
        "description": _text(description, "plugin description"),
        "entry_skill": _skill_reference(entry_skill),
        "skills": list(_references(skills, "skill", "plugin skills")),
        "included_plugins": list(_references(included_plugins, "plugin", "included plugins")),
        "required_mcp_servers": list(_references(required_mcp_servers, "mcp", "required MCP servers")),
        "optional_mcp_servers": list(_references(optional_mcp_servers, "mcp", "optional MCP servers")),
    }
    lines = [
        "schema = 1",
        f"id = {json.dumps(values['id'])}",
        f"version = {json.dumps(values['version'])}",
        f"description = {json.dumps(values['description'])}",
        f"entry_skill = {json.dumps(values['entry_skill'])}",
    ]
    for key in ("skills", "included_plugins", "required_mcp_servers", "optional_mcp_servers"):
        lines.append(f"{key} = {json.dumps(values[key], ensure_ascii=False)}")
    _atomic_write(root / PLUGIN_FILE, ("\n".join(lines) + "\n").encode())


def _write_skill_metadata(root: Path, skill_id: str, base_hash: str) -> None:
    content = (
        "schema = 1\n"
        f"id = {json.dumps(_resource_id(skill_id, 'Skill ID'))}\n"
        f"base_hash = {json.dumps(_hash(base_hash, 'Skill base hash'))}\n"
    )
    _atomic_write(root / SKILL_META_FILE, content.encode())


def _copy_skill_content(source: Path, target: Path) -> None:
    """复制一个 Skill 的资源，同时排除中央目录中的子 Skill。"""
    target.mkdir(parents=True, exist_ok=False)
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(
            parent != Path(".") and (source / parent / SKILL_FILE).is_file()
            for parent in relative.parents
        ):
            continue
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def _references(value: object, kind: str, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise TypeError(f"{name} must be an array")
    parser = {"skill": _skill_reference, "plugin": _plugin_reference, "mcp": _mcp_reference}[kind]
    return tuple(dict.fromkeys(parser(item) for item in value))


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


def _path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _path_or_none(value: str | Path | None) -> Path | None:
    return None if value is None else _path(value)


def _resource_id(value: object, name: str) -> str:
    selected = _text(value, name).lower().removeprefix("skill:").removeprefix("plugin:").removeprefix("mcp:")
    if not ID_PATTERN.fullmatch(selected):
        raise ValueError(f"invalid {name}: {selected}")
    return selected


def _plugin_reference(value: object) -> str:
    return f"plugin:{_resource_id(value, 'plugin ID')}"


def _skill_reference(value: object) -> str:
    return f"skill:{_resource_id(value, 'Skill ID')}"


def _mcp_reference(value: object) -> str:
    return f"mcp:{_resource_id(value, 'MCP ID')}"


def _version(value: object, name: str) -> str:
    selected = _text(value, name)
    if len(selected.split(".")) != 3 or not all(part.isdigit() for part in selected.split(".")):
        raise ValueError(f"{name} must use x.y.z: {selected}")
    return selected


def _hash(value: object, name: str) -> str:
    selected = _text(value, name).lower()
    if len(selected) != 64 or any(item not in "0123456789abcdef" for item in selected):
        raise ValueError(f"{name} must be a SHA-256 value")
    return selected


def _next_patch(version: str) -> str:
    major, minor, patch = _version(version, "version").split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


def _unknown(value: Mapping[str, object], allowed: set[str], name: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"unknown {name} fields: {', '.join(unknown)}")


def _text_array(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise TypeError(f"{name} must be an array of non-empty text")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _directory_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _skill_content_hash(path: Path) -> str:
    """只计算当前 Skill，避免中央目录中的兄弟 Skill 互相污染哈希。"""
    root = path.parent
    digest = hashlib.sha256()
    for item in sorted(root.rglob("*")):
        if not item.is_file():
            continue
        relative = item.relative_to(root)
        if item != path and any((root / parent / SKILL_FILE).is_file() for parent in relative.parents):
            continue
        digest.update(relative.as_posix().encode())
        digest.update(b"\0")
        digest.update(item.read_bytes())
    return digest.hexdigest()


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


def _optional_text(value: object) -> str | None:
    return None if value is None else _text(value, "optional text")


def _plugin_event(value: Plugin) -> dict[str, object]:
    return {"key": value.reference, "version": value.version, "sha256": value.sha256, "sources": len(value.sources)}


def _skill_event(value: Skill) -> dict[str, object]:
    return {"key": value.reference, "version": value.version, "sha256": value.sha256}


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

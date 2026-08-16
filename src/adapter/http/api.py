"""提供独立于 AG-UI 协议的轻量 Web 管理 API。"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from http import HTTPStatus
from pathlib import Path
from typing import Mapping
from urllib.parse import unquote

from core.config import Config, ModelConfig
from core.event import RunIdentity
from core.provider import ModelPricing
from core.records import EventStore
from super_agent import Agent, AgentSettings


class RuntimeWebAPI:
    """只读取和修改当前用户显式开启的 Runtime 状态。"""

    def __init__(self, agent: Agent, user_id: str) -> None:
        self.agent = agent
        self.user_id = _identifier(user_id, "user_id")

    def handle(
        self,
        method: str,
        path: str,
        body: object | None = None,
    ) -> tuple[HTTPStatus, object]:
        parts = [unquote(part) for part in path.split("/") if part]
        if method == "GET" and path == "/api/bootstrap":
            return HTTPStatus.OK, self.bootstrap()
        if parts[:2] == ["api", "conversations"]:
            return self._conversations(method, parts[2:], body)
        if method == "GET" and parts[:2] == ["api", "runs"] and len(parts) == 3:
            return HTTPStatus.OK, self._user().runs.explain(parts[2])
        if method == "DELETE" and parts[:2] == ["api", "memory"] and len(parts) == 3:
            self._user().memory.forget(parts[2], "forgotten from Web UI")
            return HTTPStatus.OK, self.bootstrap()
        if method == "PUT" and path == "/api/config":
            self._update_config(body)
            return HTTPStatus.OK, self.bootstrap()
        if method == "POST" and path == "/api/models":
            self._add_model(body)
            return HTTPStatus.CREATED, self.bootstrap()
        if method == "DELETE" and parts[:2] == ["api", "models"] and len(parts) == 3:
            self._remove_model(parts[2])
            return HTTPStatus.OK, self.bootstrap()
        return HTTPStatus.NOT_FOUND, {"error": "route not found"}

    def bootstrap(self) -> dict[str, object]:
        config = self.agent.config
        storage_enabled = self.agent.storage is not None
        user = self._user()
        return {
            "schema_version": 1,
            "agent": self._agent_view(),
            "storage": self._storage_view(config),
            "configuration_path": (
                "" if config is None or config.source_path is None else str(config.source_path)
            ),
            "skills": self._skills_view(),
            "models": self._models_view(config),
            "conversations": [
                _conversation_view(item, self.user_id, self.agent.name)
                for item in user.conversations.list()
            ] if storage_enabled else [],
            "runs": user.runs.list() if storage_enabled else [],
            "memory": [
                item.to_dict()
                for item in user.memory.list_items(lifetime="long_term")
            ] if storage_enabled else [],
            "subagents": self._subagent_tree(self.agent, [self.agent.name], set()),
        }

    def _conversations(
        self,
        method: str,
        parts: list[str],
        body: object | None,
    ) -> tuple[HTTPStatus, object]:
        conversations = self._user().conversations
        if method == "POST" and not parts:
            data = _object(body, "conversation")
            title = _body_text(data.get("title", ""), "title", allow_empty=True)
            created = conversations.create(title)
            return HTTPStatus.CREATED, _conversation_view(created, self.user_id, self.agent.name)
        if len(parts) == 1 and method == "GET":
            return HTTPStatus.OK, _conversation_view(
                conversations.read(parts[0]),
                self.user_id,
                self.agent.name,
            )
        if len(parts) == 1 and method == "PATCH":
            data = _object(body, "conversation")
            renamed = conversations.rename(parts[0], _body_text(data.get("title"), "title"))
            return HTTPStatus.OK, _conversation_view(renamed, self.user_id, self.agent.name)
        if len(parts) == 1 and method == "DELETE":
            conversations.delete(parts[0])
            return HTTPStatus.OK, {"conversation_id": parts[0], "deleted": True}
        if len(parts) == 2 and parts[1] == "clear" and method == "POST":
            cleared = conversations.clear(parts[0])
            return HTTPStatus.OK, _conversation_view(cleared, self.user_id, self.agent.name)
        return HTTPStatus.NOT_FOUND, {"error": "route not found"}

    def _update_config(self, body: object | None) -> None:
        data = _object(body, "agent configuration")
        _reject_fields(
            data,
            {"name", "system", "skills", "disabled_skills", "max_agent_chain_depth"},
            "agent configuration",
        )
        config = self._editable_config()
        if "name" in data and _body_text(data.get("name"), "name") != self.agent.name:
            raise ValueError("Agent name cannot change while the Runtime is running")
        skills = _text_list(data.get("skills", self.agent._enabled_skills), "skills")
        disabled = _text_list(
            data.get("disabled_skills", self.agent._disabled_skills),
            "disabled_skills",
        )
        system = _body_text(
            data.get("system", "\n".join(self.agent.instructions)),
            "system",
            allow_empty=True,
        )
        maximum = (
            config.max_subagent_depth
            if "max_agent_chain_depth" not in data
            else _optional_int(data.get("max_agent_chain_depth"), "max_agent_chain_depth")
        )
        settings = AgentSettings(
            self.agent.settings.warn_subagent_depth,
            maximum,
            self.agent.settings.limits,
        )
        updated = replace(
            config,
            instructions=tuple([system] if system else []),
            enabled_skills=tuple(skills),
            disabled_skills=tuple(disabled),
            max_subagent_depth=maximum,
        )
        _write_config(updated)
        self.agent.set_instructions(*([system] if system else []))
        self.agent._enabled_skills = list(skills)
        self.agent.set_disabled_skills(*disabled)
        self.agent.settings = settings
        self.agent.config = updated

    def _add_model(self, body: object | None) -> None:
        data = _object(body, "model configuration")
        allowed = {
            "name", "description", "provider", "model", "base_url", "api_key_env",
            "supports", "purposes", "weight", "default", "previous_name",
            "input_cost_per_million", "output_cost_per_million",
            "cache_creation_cost_per_million", "cache_read_cost_per_million",
        }
        _reject_fields(data, allowed, "model configuration")
        config = self._editable_config()
        name = _body_text(data.get("name"), "name")
        previous = _optional_text(data.get("previous_name"))
        existing = list(config.models)
        if previous and previous != name and any(item.name == name for item in existing):
            raise ValueError(f"model name already exists: {name}")
        target = previous or name
        positions = [index for index, item in enumerate(existing) if item.name == target]
        if previous and not positions:
            raise KeyError(f"model not found: {previous}")
        position = positions[0] if positions else len(existing)
        current = [item for item in existing if item.name not in {target, name}]
        pricing = ModelPricing(
            _optional_number(data.get("input_cost_per_million"), "input price", 1.0),
            _optional_number(data.get("output_cost_per_million"), "output price", 1.0),
            _optional_number(data.get("cache_creation_cost_per_million"), "cache creation price", 1.0),
            _optional_number(data.get("cache_read_cost_per_million"), "cache read price", 1.0),
        )
        created = ModelConfig(
            name=name,
            provider=_body_text(data.get("provider"), "provider"),
            model=_body_text(data.get("model"), "model"),
            base_url=_optional_text(data.get("base_url")),
            api_key_env=_optional_text(data.get("api_key_env")),
            description=_optional_text(data.get("description")) or "",
            purposes=_text_list(data.get("purposes", ["auto"]), "purposes"),
            features=_text_list(data.get("supports", ["text", "tools"]), "supports"),
            weight=_number(data.get("weight", 1.0), "model weight", positive=True),
            pricing=pricing,
        )
        make_default = _boolean_value(data.get("default", False), "model default")
        if make_default:
            current.insert(0, created)
        elif position == 0 and current:
            current.append(created)
        else:
            current.insert(min(position, len(current)), created)
        updated = replace(config, models=tuple(current))
        profiles = updated.create_model_profiles()
        _write_config(updated)
        self.agent.config = updated
        self.agent.replace_models(profiles, router_settings=updated.router)

    def _remove_model(self, name: str) -> None:
        config = self._editable_config()
        remaining = tuple(item for item in config.models if item.name != name)
        if len(remaining) == len(config.models):
            raise KeyError(f"model not found: {name}")
        updated = replace(config, models=remaining)
        profiles = () if not updated.models else updated.create_model_profiles()
        _write_config(updated)
        self.agent.config = updated
        self.agent.replace_models(profiles, router_settings=updated.router)

    def _editable_config(self) -> Config:
        config = self.agent.config
        if config is None or config.source_path is None:
            raise RuntimeError(
                "configuration editing requires an explicit general configuration file"
            )
        return config

    def _agent_view(self) -> dict[str, object]:
        return {
            "name": self.agent.name,
            "system": "\n\n".join(self.agent.instructions),
            "skills": list(self.agent._enabled_skills),
            "max_agent_chain_depth": self.agent.settings.max_subagent_depth,
            "disabled_skills": list(self.agent._disabled_skills),
        }

    def _storage_view(self, config: Config | None) -> dict[str, object]:
        if config is None:
            return {
                "backend": type(self.agent.storage).__name__ if self.agent.storage else "none",
                "path": "",
            }
        return {
            "backend": config.storage.backend,
            "path": config.storage.path,
            "audit": {
                "detailed_days": config.storage.detailed_log_days,
                "critical_days": config.storage.critical_log_days,
            },
        }

    def _skills_view(self) -> list[dict[str, object]]:
        if self.agent.skill_library is None:
            return []
        identity = RunIdentity(user_id=self.user_id, agent_name=self.agent.name)
        store = None if self.agent.storage is None else EventStore(
            self.agent.storage,
            self.user_id,
            self.agent.name,
        )
        library = self.agent._library(identity, store)
        if library is None:
            return []
        values: list[dict[str, object]] = []
        page = 1
        while True:
            current = library.list_skills(page=page, page_size=100)
            values.extend(dict(item) for item in current.items)
            if not current.has_more:
                break
            page += 1
        for item in values:
            key = str(item["key"])
            skill = library.find(key)
            freshness = None
            call_count = 0
            success_count = 0
            if self.agent.evolution_enabled:
                evolution = self.agent._evolution(identity, library, store)
                freshness = evolution.freshness(key)
                call_count, success_count = evolution.count_skill_evidence(key)
            item.update(
                name=skill.name,
                type=skill.skill_type,
                content_sha256=skill.sha256,
                agent_created=skill.created_by == "agent",
                agent_can_update=skill.agent_can_update,
                freshness=70.0 if freshness is None else freshness.value,
                call_count=call_count,
                success_count=success_count,
            )
        return values

    def _models_view(self, config: Config | None) -> list[dict[str, object]]:
        if config is None:
            return []
        return [
            {
                "key": item.name,
                "name": item.name,
                "description": item.description,
                "provider": item.provider,
                "model": item.model,
                "base_url": item.base_url,
                "api_key_env": item.api_key_env,
                "supports": list(item.features),
                "purposes": list(item.purposes),
                "weight": item.weight,
                "default": index == 0,
                "ready": (
                    item.required_api_key_environment() is None
                    or bool(os.environ.get(item.required_api_key_environment() or ""))
                ),
                **item.pricing.to_dict(),
            }
            for index, item in enumerate(config.models)
        ]

    def _subagent_tree(
        self,
        agent: Agent,
        path: list[str],
        seen: set[int],
    ) -> list[dict[str, object]]:
        if id(agent) in seen:
            return []
        current_seen = seen | {id(agent)}
        values: list[dict[str, object]] = []
        for link in agent.list_subagents():
            child_path = [*path, link.name]
            child_user = link.agent.for_user(self.user_id)
            values.append(
                {
                    "name": link.name,
                    "description": link.description,
                    "agent_name": link.agent.name,
                    "created_by_agent": link.created_by_agent,
                    "path": child_path,
                    "runs": child_user.runs.list() if link.agent.storage is not None else [],
                    "children": self._subagent_tree(link.agent, child_path, current_seen),
                }
            )
        return values

    def _user(self):
        return self.agent.for_user(self.user_id)


def map_api_error(error: Exception) -> tuple[HTTPStatus, dict[str, object]]:
    """把管理 API 的预期错误映射为稳定 HTTP 状态。"""
    if isinstance(error, KeyError):
        message = str(error.args[0]) if error.args else "resource not found"
        return HTTPStatus.NOT_FOUND, {"error": message}
    if isinstance(error, RuntimeError) and "requires" in str(error):
        return HTTPStatus.CONFLICT, {"error": str(error)}
    return HTTPStatus.BAD_REQUEST, {"error": str(error)}


def _conversation_view(value: object, user_id: str, agent_name: str) -> dict[str, object]:
    return {
        "conversation_id": value.conversation_id,
        "user_id": user_id,
        "agent_name": agent_name,
        "title": value.title,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
        "messages": [
            {
                "message_id": f"{item.run_id}-{index}",
                "role": item.role,
                "content": item.content,
                "created_at": item.created_at,
                "run_id": item.run_id,
                "run_result": None,
            }
            for index, item in enumerate(value.messages)
        ],
    }


def _write_config(config: Config) -> None:
    path = config.source_path
    if path is None:
        raise RuntimeError("configuration writing requires an explicit file path")
    content = _format_config(config).encode("utf-8")
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


def _format_config(config: Config) -> str:
    lines = [
        "version = 1",
        f"name = {_toml(config.name)}",
        f"instructions = {_toml(list(config.instructions))}",
        f"skill_paths = {_toml(list(config.skill_paths))}",
        f"enabled_skills = {_toml(list(config.enabled_skills))}",
        f"disabled_skills = {_toml(list(config.disabled_skills))}",
        f"memory = {_boolean(config.memory)}",
        f"evolution = {_boolean(config.evolution)}",
        f"warn_subagent_depth = {config.warn_subagent_depth}",
    ]
    for name in ("writable_skill_path", "skill_cache_path", "max_subagent_depth"):
        value = getattr(config, name)
        if value is not None:
            lines.append(f"{name} = {_toml(value)}")
    lines.extend([
        "",
        "[storage]",
        f"backend = {_toml(config.storage.backend)}",
        f"path = {_toml(config.storage.path)}",
        f"detailed_log_days = {config.storage.detailed_log_days}",
        f"critical_log_days = {config.storage.critical_log_days}",
    ])
    if config.storage.database_url_env is not None:
        lines.append(f"database_url_env = {_toml(config.storage.database_url_env)}")
    lines.extend([
        "",
        "[router]",
        f"max_fallbacks = {config.router.max_fallbacks}",
        f"circuit_failures = {config.router.circuit_failures}",
        f"circuit_wait_seconds = {config.router.circuit_wait_seconds}",
        "",
        "[limits]",
    ])
    for name in config.limits.__dataclass_fields__:
        value = getattr(config.limits, name)
        if value is not None:
            lines.append(f"{name} = {value}")
    for item in config.models:
        lines.extend([
            "",
            "[[models]]",
            f"name = {_toml(item.name)}",
            f"provider = {_toml(item.provider)}",
            f"model = {_toml(item.model)}",
            f"description = {_toml(item.description)}",
            f"purposes = {_toml(list(item.purposes))}",
            f"features = {_toml(list(item.features))}",
            f"weight = {item.weight}",
            f"pricing = {_inline_table(item.pricing.to_dict())}",
        ])
        if item.base_url is not None:
            lines.append(f"base_url = {_toml(item.base_url)}")
        if item.api_key_env is not None:
            lines.append(f"api_key_env = {_toml(item.api_key_env)}")
    return "\n".join(lines) + "\n"


def _inline_table(value: Mapping[str, object]) -> str:
    return "{ " + ", ".join(f"{key} = {_toml(item)}" for key, item in value.items()) + " }"


def _toml(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _boolean(value: bool) -> str:
    return "true" if value else "false"


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        raise ValueError(f"{name} must be a non-empty string with at most 200 characters")
    if any(ord(item) < 32 for item in value):
        raise ValueError(f"{name} must contain printable characters")
    return value.strip()


def _object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a JSON object")
    return value


def _reject_fields(value: Mapping[str, object], allowed: set[str], name: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"unknown {name} fields: {', '.join(unknown)}")


def _body_text(value: object, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or not allow_empty and not value.strip():
        suffix = "" if allow_empty else " and cannot be empty"
        raise ValueError(f"{name} must be text{suffix}")
    return value if allow_empty else value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("optional text must be text or null")
    return value.strip() or None


def _text_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{name} must be an array of non-empty text")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _optional_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer or null")
    return value


def _number(value: object, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    selected = float(value)
    if selected < 0 or positive and selected <= 0:
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be {qualifier}")
    return selected


def _optional_number(value: object, name: str, default: float) -> float:
    return default if value is None else _number(value, name)


def _boolean_value(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be boolean")
    return value

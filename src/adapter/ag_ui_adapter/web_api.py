"""Small JSON management API backed by the same Agent and Runtime state."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from http import HTTPStatus
from urllib.parse import unquote

from super_agent import Agent
from adapter.ag_ui_adapter.configuration import (
    CommonConfigurationInput,
    common_configuration_to_dict,
    update_common_configuration,
)
from skill.runtime.defaults import (
    create_skills,
    load_configured_freshness_rules_if_enabled,
)
from core.config import CommonConfig
from skill.learning.insight import explain_run_with_insight
from core.checks import ActionEffect, ActionRequest
from skill.disclosure import skill_index_to_dict
from skill.runtime.models import model_profile_to_dict, read_model_profiles
from skill.runtime.files.models import model_skill_input_from_dict


@dataclass(frozen=True)
class WebAPIResponse:
    status: HTTPStatus
    body: object


class WebAPI:
    def __init__(self, agent: Agent, user_id: str) -> None:
        self.agent = agent
        self.user_id = user_id
        self.user = agent.for_user(user_id)

    def handle(self, method: str, path: str, body: object | None = None) -> WebAPIResponse:
        if method == "GET" and path == "/api/bootstrap":
            return _ok(self._read_bootstrap())
        parts = [unquote(part) for part in path.split("/") if part]
        if parts[:2] == ["api", "conversations"]:
            return self._handle_conversations(method, parts[2:], body)
        if method == "GET" and parts[:2] == ["api", "runs"] and len(parts) == 3:
            return _ok(self._read_run(parts[2]))
        if method == "DELETE" and parts[:2] == ["api", "memory"] and len(parts) == 3:
            self._forget_memory(parts[2])
            return _ok(self._read_bootstrap())
        if method == "PUT" and path == "/api/config":
            self._update_configuration(body)
            return _ok(self._read_bootstrap())
        if method == "POST" and path == "/api/models":
            self._save_model(body)
            return _ok(self._read_bootstrap(), HTTPStatus.CREATED)
        if method == "DELETE" and parts[:2] == ["api", "models"] and len(parts) == 3:
            self._remove_model(parts[2])
            return _ok(self._read_bootstrap())
        return WebAPIResponse(HTTPStatus.NOT_FOUND, {"error": "route not found"})

    def _handle_conversations(
        self,
        method: str,
        parts: list[str],
        body: object | None,
    ) -> WebAPIResponse:
        if method == "POST" and not parts:
            title = _optional_body_text(body, "title")
            conversation = self.user.conversations.create(title)
            return _ok(asdict(conversation), HTTPStatus.CREATED)
        if len(parts) == 1 and method == "GET":
            conversation = self.user.conversations.read(parts[0])
            return _ok(asdict(conversation))
        if len(parts) == 1 and method == "PATCH":
            title = _required_body_text(body, "title")
            conversation = self.user.conversations.rename(parts[0], title)
            return _ok(asdict(conversation))
        if len(parts) == 1 and method == "DELETE":
            self.user.conversations.delete(parts[0])
            return _ok({"conversation_id": parts[0], "deleted": True})
        if len(parts) == 2 and parts[1] == "clear" and method == "POST":
            return _ok(asdict(self.user.conversations.clear(parts[0])))
        return WebAPIResponse(HTTPStatus.NOT_FOUND, {"error": "route not found"})

    def _read_bootstrap(self) -> dict[str, object]:
        store = self.agent._setup.create_event_store(self.user_id)
        config = self.agent.config
        all_skills_config = replace(
            config,
            agent=replace(config.agent, disabled_skills=[]),
        )
        skills = create_skills(
            all_skills_config,
            store=store,
        )
        environment = self.agent._setup.user_secrets.get_environment_for_user(self.user_id)
        if self.agent._setup.provided_provider is not None and not _has_model_skill(skills):
            environment = {}
        models = read_model_profiles(skills, environment)
        return {
            "schema_version": 3,
            "agent": common_configuration_to_dict(config),
            "storage": {
                "backend": config.storage.backend,
                "path": str(config.storage.path),
                "audit": {
                    "detailed_days": config.storage.audit.detailed_days,
                    "critical_days": config.storage.audit.critical_days,
                },
            },
            "configuration_path": str(config.source),
            "skills": _web_skill_list(skill_index_to_dict(skills.index), config),
            "models": [
                model_profile_to_dict(profile, environment) for profile in models
            ],
            "conversations": [
                asdict(item) for item in self.user.conversations.list()
            ],
            "runs": [asdict(item) for item in store.list_runs(50)],
            "memory": store.memory.list_items(),
            "subagents": _subagent_tree(
                self.agent,
                self.user_id,
                set(),
                [config.agent.name],
            ),
        }
    def _read_run(self, run_id: str) -> dict[str, object]:
        agent = _find_agent_for_run(self.agent, self.user_id, run_id, set())
        store = agent._setup.create_event_store(self.user_id)
        rules = load_configured_freshness_rules_if_enabled(agent.config, store=store)
        return explain_run_with_insight(store, run_id, rules)

    def _forget_memory(self, item_id: str) -> None:
        store = self.agent._setup.create_event_store(self.user_id)
        item = next(
            (
                candidate
                for candidate in store.memory.list_items()
                if candidate["item_id"] == item_id
            ),
            None,
        )
        if item is None:
            raise KeyError(f"active memory item not found: {item_id}")
        self.agent._setup.active_state_access.execute_action(
            self.user_id,
            ActionRequest.create(
                "user:web-memory",
                f"memory:long-term:{item_id}",
                (ActionEffect.DELETE,),
            ),
            lambda: store.memory.forget_items(
                [item_id],
                "forgotten from web interface",
            ),
        )

    def _update_configuration(self, body: object | None) -> None:
        request = CommonConfigurationInput.from_dict(body)
        updated = self.agent._setup.active_state_access.execute_action(
            self.user_id,
            ActionRequest.create(
                "user:web-configuration",
                "config:agent",
                (ActionEffect.UPDATE,),
            ),
            lambda: update_common_configuration(self.agent.config, request),
        )
        self.user.configuration.replace(updated)

    def _save_model(self, body: object | None) -> None:
        manager = self.user.skills.create_model_manager()
        manager.save_model_skill(model_skill_input_from_dict(body))
        self.user.skills.reload_models()

    def _remove_model(self, name: str) -> None:
        self.user.skills.create_model_manager().remove_model_skill(name)
        self.user.skills.reload_models()


def _ok(body: object, status: HTTPStatus = HTTPStatus.OK) -> WebAPIResponse:
    return WebAPIResponse(status, body)


def _has_model_skill(skills) -> bool:
    return any(
        entry.reference.skill_type == "model"
        for entry in skills.index.entries
    )


def _web_skill_list(
    value: dict[str, object],
    config: CommonConfig,
) -> list[dict[str, object]]:
    disabled = set(config.agent.disabled_skills)
    selected = set(config.agent.skills)
    skills = value.get("skills", [])
    return [
        {
            key: field
            for key, field in item.items()
            if key not in {
                "manifest_cache_path",
                "instructions_cache_path",
                "configuration_cache_path",
                "files_cache_path",
            }
        }
        | {
            "enabled": not {
                item["type"],
                item["key"],
                item["name"],
            }
            & disabled,
            "selected": item["key"] in selected or item["name"] in selected,
        }
        for item in skills
        if isinstance(item, dict)
    ]


def _find_agent_for_run(
    agent: Agent,
    user_id: str,
    run_id: str,
    seen: set[int],
) -> Agent:
    if id(agent) in seen:
        raise KeyError(f"run not found: {run_id}")
    seen.add(id(agent))
    try:
        agent._setup.create_event_store(user_id).read_run(run_id)
        return agent
    except KeyError:
        pass
    for subagent in agent.subagents:
        try:
            return _find_agent_for_run(subagent.agent, user_id, run_id, seen)
        except KeyError:
            continue
    raise KeyError(f"run not found: {run_id}")


def _subagent_tree(
    agent: Agent,
    user_id: str,
    seen: set[int],
    path: list[str],
) -> list[dict[str, object]]:
    if id(agent) in seen:
        return []
    next_seen = seen | {id(agent)}
    nodes: list[dict[str, object]] = []
    for subagent in agent.subagents:
        child_path = [*path, subagent.name]
        child_store = subagent.agent._setup.create_event_store(user_id)
        nodes.append(
            {
                "name": subagent.name,
                "description": subagent.description,
                "agent_name": subagent.agent.config.agent.name,
                "created_by_agent": subagent.created_by_agent,
                "path": child_path,
                "runs": [asdict(item) for item in child_store.list_runs(50)],
                "children": _subagent_tree(
                    subagent.agent,
                    user_id,
                    next_seen,
                    child_path,
                ),
            }
        )
    return nodes


def _required_body_text(body: object | None, name: str) -> str:
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"request {name} must be a non-empty string")
    return value.strip()


def _optional_body_text(body: object | None, name: str) -> str:
    if body is None:
        return ""
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    value = body.get(name, "")
    if not isinstance(value, str):
        raise ValueError(f"request {name} must be a string")
    return value.strip()

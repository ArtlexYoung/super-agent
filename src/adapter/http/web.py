"""Small JSON management API backed by the same Agent and Runtime state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from http import HTTPStatus
from urllib.parse import unquote

from super_agent import Agent
from core.config import AgentSettings, CommonConfig
from core.models import reject_unknown_fields, read_int, read_object, read_text, read_text_list
from skill.discovery.catalog import skill_index_to_dict
from skill.handlers.model_management import model_skill_input_from_dict


@dataclass(frozen=True)
class CommonConfigurationInput:
    name: str
    system: str
    skills: list[str]
    max_agent_chain_depth: int | None
    disabled_skills: list[str]

    @classmethod
    def from_dict(cls, value: object) -> "CommonConfigurationInput":
        if not isinstance(value, dict):
            raise ValueError("agent configuration must be a JSON object")
        allowed = set(cls.__dataclass_fields__)
        reject_unknown_fields(value, allowed, "agent configuration fields")
        return cls(
            name=read_text(value.get("name"), "agent configuration name"),
            system=read_text(value.get("system"), "agent configuration system"),
            skills=read_text_list(value.get("skills", []), "agent configuration skills", lower=True),
            max_agent_chain_depth=(None if value.get("max_agent_chain_depth") is None else read_int(value["max_agent_chain_depth"], "max_agent_chain_depth", minimum=1)),
            disabled_skills=read_text_list(value.get("disabled_skills", []), "agent configuration disabled_skills", lower=True),
        )

    def to_agent_settings(self) -> AgentSettings:
        return AgentSettings(name=self.name, system=self.system, skills=self.skills, max_agent_chain_depth=self.max_agent_chain_depth, disabled_skills=self.disabled_skills)


def common_configuration_to_dict(config: CommonConfig) -> dict[str, object]:
    return asdict(config.agent)


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

    def _handle_conversations(self, method: str, parts: list[str], body: object | None) -> WebAPIResponse:
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
        config = self.agent.config
        skills = self.user.skills.list_all()
        models = self.user.skills.list_models()
        return {
            "schema_version": 3,
            "agent": common_configuration_to_dict(config),
            "storage": {"backend": config.storage.backend, "path": str(config.storage.path), "audit": {"detailed_days": config.storage.audit.detailed_days, "critical_days": config.storage.audit.critical_days}},
            "configuration_path": str(config.source),
            "skills": _web_skill_list(skill_index_to_dict(skills.index), config),
            "models": models,
            "conversations": [asdict(item) for item in self.user.conversations.list()],
            "runs": [asdict(item) for item in self.user.runs.list(50)],
            "memory": [asdict(item) for item in self.user.memory.list()],
            "subagents": _subagent_tree(self.agent, self.user_id, set(), [config.agent.name]),
        }

    def _read_run(self, run_id: str) -> dict[str, object]:
        return self.user.runs.explain(run_id)

    def _forget_memory(self, item_id: str) -> None:
        self.user.memory.forget(item_id, "forgotten from web interface")

    def _update_configuration(self, body: object | None) -> None:
        request = CommonConfigurationInput.from_dict(body)
        self.user.configuration.update_agent_settings(request.to_agent_settings())

    def _save_model(self, body: object | None) -> None:
        self.user.skills.save_model(model_skill_input_from_dict(body))

    def _remove_model(self, name: str) -> None:
        self.user.skills.remove_model(name)


def _ok(body: object, status: HTTPStatus = HTTPStatus.OK) -> WebAPIResponse:
    return WebAPIResponse(status, body)


def _web_skill_list(value: dict[str, object], config: CommonConfig) -> list[dict[str, object]]:
    disabled = set(config.agent.disabled_skills)
    selected = set(config.agent.skills)
    skills = value.get("skills", [])
    return [
        {key: field for key, field in item.items() if key not in {"manifest_cache_path", "instructions_cache_path", "configuration_cache_path", "files_cache_path"}} | {"enabled": not {item["type"], item["key"], item["name"]} & disabled, "selected": item["key"] in selected or item["name"] in selected} for item in skills if isinstance(item, dict)
    ]


def _subagent_tree(agent: Agent, user_id: str, seen: set[int], path: list[str]) -> list[dict[str, object]]:
    if id(agent) in seen:
        return []
    next_seen = seen | {id(agent)}
    nodes: list[dict[str, object]] = []
    for subagent in agent.subagents:
        child_path = [*path, subagent.name]
        child = subagent.agent.for_user(user_id)
        nodes.append({"name": subagent.name, "description": subagent.description, "agent_name": subagent.agent.config.agent.name, "created_by_agent": subagent.created_by_agent, "path": child_path, "runs": [asdict(item) for item in child.runs.list(50)], "children": _subagent_tree(subagent.agent, user_id, next_seen, child_path)})
    return nodes


def _required_body_text(body: object | None, name: str) -> str:
    return read_text(read_object(body, "request body").get(name), f"request {name}")


def _optional_body_text(body: object | None, name: str) -> str:
    if body is None:
        return ""
    value = read_object(body, "request body").get(name, "")
    return read_text(value, f"request {name}", allow_empty=True)

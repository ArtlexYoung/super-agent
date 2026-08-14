"""Explicit user-scoped access to one configured Agent."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from core.provider import Message
from core.config import AgentSettings, CommonConfig
from core.checks import write_bytes_atomically
from core.models import Conversation, RunEvent, RunSnapshot, resolve_agent_run_options, validate_user_id
from core.records.conversations import clear_conversation, create_conversation, delete_conversation, list_conversations, read_conversation, rename_conversation
from core.checks import ActionEffect, ActionRequest
from core.models import RunResult, TaskTrace

if TYPE_CHECKING:
    from adapter.agent import Agent
    from core.models import AgentRunOptions, RunLearningResult
    from core.model_calls import ModelUsageStats
    from skill.handlers.memory import Memory, MemoryItem
    from core.records.store import EventStore
    from skill.handlers.runtime import Skills
    from skill.handlers.models import ModelProfile
    from skill.handlers.model_management import ModelSkillInput
    from skill.handlers.model_management import ModelSkillManager
    from skill.learning.update import SkillUpdater


class UserAgent:
    """Bind every stateful operation to one trusted user identifier."""

    def __init__(self, agent: "Agent", user_id: str) -> None:
        self.agent = agent
        self.user_id = validate_user_id(user_id)
        self.conversations = UserConversations(self)
        self.runs = UserRuns(self)
        self.memory = UserMemory(self)
        self.skills = UserSkills(self)
        self.configuration = UserConfiguration(self)

    def run(self, prompt: str, *, messages: list[Message] | None = None, conversation_id: str | None = None, skill: str | None = None, run_options: "AgentRunOptions | None" = None) -> RunResult:
        return self.agent._run_for_user(prompt, self.user_id, messages=messages, conversation_id=conversation_id, run_options=resolve_agent_run_options(run_options, skill))

    def _store(self):
        return self.agent._create_event_store(self.user_id)

    def _execute(self, request: ActionRequest, action):
        return self.agent._execute_action(self.user_id, request, action)


class UserConversations:
    """Manage conversations inside one user and Agent scope."""

    def __init__(self, user: UserAgent) -> None:
        self.user = user

    def create(self, title: str = "", *, conversation_id: str | None = None) -> Conversation:
        return cast(Conversation, self.user._execute(ActionRequest.create("user:conversation", "conversation:new", (ActionEffect.CREATE,)), lambda: create_conversation(self.user._store(), title, conversation_id=conversation_id)))

    def list(self) -> list[Conversation]:
        return list_conversations(self.user._store())

    def read(self, conversation_id: str) -> Conversation:
        return read_conversation(self.user._store(), conversation_id)

    def rename(self, conversation_id: str, title: str) -> Conversation:
        return cast(Conversation, self.user._execute(ActionRequest.create("user:conversation", f"conversation:{conversation_id}", (ActionEffect.UPDATE,)), lambda: rename_conversation(self.user._store(), conversation_id, title)))

    def clear(self, conversation_id: str) -> Conversation:
        return cast(Conversation, self.user._execute(ActionRequest.create("user:conversation", f"conversation:{conversation_id}", (ActionEffect.DELETE,)), lambda: clear_conversation(self.user._store(), conversation_id)))

    def delete(self, conversation_id: str) -> None:
        self.user._execute(ActionRequest.create("user:conversation", f"conversation:{conversation_id}", (ActionEffect.DELETE,)), lambda: delete_conversation(self.user._store(), conversation_id))


class UserRuns:
    """Read traces and record feedback in one user scope."""

    def __init__(self, user: UserAgent) -> None:
        self.user = user

    def read_trace(self, run_id: str) -> TaskTrace:
        return self.user.agent._read_task_trace(self.user.user_id, run_id)

    def list(self, limit: int | None = None, *, conversation_id: str | None = None, include_sensitive: bool = False) -> list[RunSnapshot]:
        return self.user._store().list_runs(limit, conversation_id=conversation_id, include_sensitive=include_sensitive)

    def read(self, run_id: str, *, include_sensitive: bool = False) -> RunSnapshot:
        _, store = _find_run_owner(self.user, run_id)
        return store.read_run(run_id, include_sensitive=include_sensitive)

    def explain(self, run_id: str, *, include_sensitive: bool = False) -> dict[str, object]:
        from skill.learning.run_learning import explain_run_with_insight
        from skill.handlers.runtime import load_configured_freshness_rules_if_enabled

        owner, store = _find_run_owner(self.user, run_id)
        rules = load_configured_freshness_rules_if_enabled(owner.agent.config, store=store)
        return explain_run_with_insight(store, run_id, rules, include_sensitive=include_sensitive)

    def export(self, run_id: str, path: str | Path, *, include_sensitive: bool = False) -> Path:
        _, store = _find_run_owner(self.user, run_id)
        return store.export_run(run_id, Path(path).expanduser(), include_sensitive=include_sensitive)

    def list_checkpoints(self, run_id: str) -> list[dict[str, object]]:
        from core.runtime import list_checkpoint_data

        events = self.user._store().read_run_events(run_id, include_sensitive=True)
        return list_checkpoint_data(events)

    def resume(self, run_id: str, prompt: str, *, checkpoint_id: str | None = None) -> RunResult:
        from core.runtime import find_checkpoint_data

        store = self.user._store()
        events = store.read_run_events(run_id, include_sensitive=True)
        checkpoint = find_checkpoint_data(events, checkpoint_id)
        return self.user.agent._run_for_user(prompt, self.user.user_id, resumed_from_run_id=run_id, resume_checkpoint=checkpoint)

    def record_feedback(self, run_id: str, score: float, reason: str = "") -> RunEvent:
        return self.user.agent._record_task_feedback(self.user.user_id, run_id, score=score, reason=reason, source="explicit")

    def learn(self, run_id: str) -> "RunLearningResult":
        store = self.user._store()
        snapshot = store.read_run(run_id)
        if snapshot.agent_name != self.user.agent.config.agent.name:
            raise ValueError(f"run belongs to another Agent: {run_id}")
        from skill.learning.run_learning import learn_from_run
        from core.models import RunLearningResult
        from skill.handlers.runtime import load_configured_freshness_rules

        rules = load_configured_freshness_rules(self.user.agent.config, store=store)
        result = self.user._execute(ActionRequest.create("user:run-learning", f"run:{run_id}", (ActionEffect.CREATE, ActionEffect.UPDATE)), lambda: learn_from_run(store, run_id, rules))
        if not isinstance(result, RunLearningResult):
            raise TypeError("run learning must return RunLearningResult")
        return result

    def list_model_usage_stats(self, purpose: str | None = None) -> list["ModelUsageStats"]:
        from core.model_calls import list_model_usage_stats

        return list_model_usage_stats(self.user._store(), purpose)

    def review(self, run_id: str, evidence: dict[str, object]):
        from skill.learning.run_learning import review_run_evidence

        agent = self.user.agent
        store = self.user._store()
        skills = agent._create_skills(self.user.user_id)
        runner = agent._create_task_runner(self.user.user_id, skills)
        decision = runner.model_caller.select_task_model("review", ("text",), store)
        reviewer = runner.create_text_model(store, "independent_review", decision=decision)
        return self.user._execute(ActionRequest.create("user:run-review", f"run:{run_id}", (ActionEffect.CREATE,)), lambda: review_run_evidence(store, run_id, evidence, reviewer.send_messages, skills.disclosure))


class UserMemory:
    """Read and explicitly change long-term memory in one user scope."""

    def __init__(self, user: UserAgent) -> None:
        self.user = user

    def list(self, scope: str | None = None) -> list["MemoryItem"]:
        return self._memory().list_long_term(scope)

    def recall(self, query: str, scope: str | None = None, limit: int | None = None) -> list["MemoryItem"]:
        return self._memory().recall_long_term(query, scope, limit)

    def remember(self, text: str, scope: str | None = None, source_run_id: str = "") -> "MemoryItem":
        return cast("MemoryItem", self.user._execute(ActionRequest.create("user:memory", "memory:long-term", (ActionEffect.CREATE,)), lambda: self._memory().remember_long_term(text, scope, source_run_id)))

    def forget(self, item_id: str, reason: str = "") -> None:
        self.user._execute(ActionRequest.create("user:memory", f"memory:long-term:{item_id}", (ActionEffect.DELETE,)), lambda: self._memory().forget_long_term(item_id, reason))

    def usage_habits_instruction(self) -> str:
        return self._memory().usage_habits.build_prompt_instruction()

    def _memory(self) -> "Memory":
        from skill.handlers.memory import create_memory_from_skill

        skills = self.user.agent._create_skills(self.user.user_id)
        selected = skills.index.select_one_configured_or_default_skill("memory", self.user.agent.config.agent.skills)
        return create_memory_from_skill(skills.open(selected.reference), self.user._store())


class UserSkills:
    """Manage explicit Skill changes and model Skills for one user."""

    def __init__(self, user: UserAgent) -> None:
        self.user = user

    def create_skill_updater(self) -> "SkillUpdater":
        return cast("SkillUpdater", _create_skill_updater(self.user))

    def create_model_manager(self) -> "ModelSkillManager":
        from skill.handlers.model_management import ModelSkillManager

        return ModelSkillManager(self.user.agent.config, self.user._store(), self.user.agent._action_rules())

    def list_all(self) -> "Skills":
        from dataclasses import replace

        config = self.user.agent.config
        return self.user.agent._create_skills(self.user.user_id, config=replace(config, agent=replace(config.agent, disabled_skills=[])))

    def list_models(self) -> list[dict[str, object]]:
        from skill.handlers.models import model_profile_to_dict, read_model_profiles

        skills = self.list_all()
        environment = self.user.agent._user_environment(self.user.user_id)
        has_model_skill = any(entry.reference.skill_type == "model" for entry in skills.index.entries)
        if self.user.agent._uses_direct_provider() and not has_model_skill:
            environment = {}
        return [model_profile_to_dict(profile, environment) for profile in read_model_profiles(skills, environment)]

    def save_model(self, request: "ModelSkillInput") -> "ModelProfile":
        profile = self.create_model_manager().save_model_skill(request)
        self.reload_models()
        return profile

    def remove_model(self, name: str) -> None:
        self.create_model_manager().remove_model_skill(name)
        self.reload_models()

    def reload_models(self) -> None:
        self.user.agent._reload_models(self.user.user_id)


class UserConfiguration:
    """Save Agent settings and refresh the active configuration explicitly."""

    def __init__(self, user: UserAgent) -> None:
        self.user = user

    def update_agent_settings(self, settings: AgentSettings) -> CommonConfig:
        def update() -> CommonConfig:
            current = self.user.agent.config
            updated = replace(current, agent=settings)
            content = _common_config_to_toml(updated)
            write_bytes_atomically(current.source, content.encode("utf-8"))
            loaded = CommonConfig.load_from_file(current.source)
            self.user.agent._replace_configuration(loaded)
            return loaded

        updated = self.user._execute(ActionRequest.create("user:configuration", "config:agent", (ActionEffect.UPDATE,)), update)
        if not isinstance(updated, CommonConfig):
            raise TypeError("configuration update must return CommonConfig")
        return updated


def _create_skill_updater(user: UserAgent) -> "SkillUpdater":
    from skill.learning.update import SkillUpdater

    agent = user.agent
    store = user._store()
    skills = agent._create_skills(user.user_id)
    task_runner = agent._create_task_runner(user.user_id, skills)
    return SkillUpdater(
        skills.disclosure, store=store, propose_model=task_runner.create_text_model(store, "skill_change_proposal"), test_model=task_runner.create_text_model(store, "skill_change_test"), on_skill_changed=lambda manifest: agent._reload_models(user.user_id) if manifest.skill_type == "model" else None, action_rules=agent._action_rules()
    )


def _find_run_owner(user: UserAgent, run_id: str) -> tuple[UserAgent, "EventStore"]:
    try:
        return _find_attached_run_owner(user, run_id, set())
    except KeyError:
        return user, user._store().store_for_run(run_id)


def _find_attached_run_owner(user: UserAgent, run_id: str, seen: set[int]) -> tuple[UserAgent, "EventStore"]:
    if id(user.agent) in seen:
        raise KeyError(f"run not found: {run_id}")
    seen.add(id(user.agent))
    store = user._store()
    try:
        store.read_run(run_id)
        return user, store
    except KeyError:
        pass
    for subagent in user.agent.subagents:
        child = subagent.agent.for_user(user.user_id)
        try:
            return _find_attached_run_owner(child, run_id, seen)
        except KeyError:
            continue
    raise KeyError(f"run not found: {run_id}")


def _common_config_to_toml(config: CommonConfig) -> str:
    agent = config.agent
    base = config.source.parent
    lines = ["schema_version = 1", 'kind = "common"', "", "[agent]", f"name = {_toml_string(agent.name)}", f"system = {_toml_string(agent.system)}", f"skills = {_toml_array(agent.skills)}"]
    if agent.max_agent_chain_depth is not None:
        lines.append(f"max_agent_chain_depth = {agent.max_agent_chain_depth}")
    lines.extend([f"disabled_skills = {_toml_array(agent.disabled_skills)}", "", "[paths]", f"skills = {_toml_array([_portable_path(path, base) for path in config.paths.skills])}", "", "[storage]", f"backend = {_toml_string(config.storage.backend)}", f"path = {_toml_string(_portable_path(config.storage.path, base))}"])
    if config.storage.url_env is not None:
        lines.append(f"url_env = {_toml_string(config.storage.url_env)}")
    lines.extend(["", "[storage.audit]", f"detailed_days = {config.storage.audit.detailed_days}", f"critical_days = {config.storage.audit.critical_days}"])
    return "\n".join(lines) + "\n"


def _portable_path(path: Path, base: Path) -> str:
    try:
        relative = path.resolve().relative_to(base.resolve())
    except ValueError:
        return str(path.resolve())
    return str(relative) or "."


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"

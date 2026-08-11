"""Explicit user-scoped access to one configured Agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from core.provider import Message
from core.config import CommonConfig
from core.models import resolve_agent_run_options, validate_user_id
from core.state.models import Conversation, RunEvent
from adapter.agent import (
    AgentUserRunRequest,
    activate_agent_skill_change,
    create_agent_event_store,
    create_agent_skills,
    create_agent_task_loop,
    get_agent_action_rules,
    get_agent_state_access,
    reload_agent_models,
    replace_agent_configuration,
    run_agent_for_user,
)
from core.state.conversations import (
    clear_conversation,
    create_conversation,
    delete_conversation,
    list_conversations,
    read_conversation,
    rename_conversation,
)
from core.checks import ActionEffect, ActionRequest
from core.models import RunResult, TaskTrace

if TYPE_CHECKING:
    from core.runtime.agent import Agent
    from core.models import AgentRunOptions, RunLearningResult
    from core.runtime.model_calls import ModelUsageStats
    from skill.runtime.model_skills import ModelSkillManager
    from skill.learning.update import SkillUpdater


class UserAgent:
    """Bind every stateful operation to one trusted user identifier."""

    def __init__(self, agent: "Agent", user_id: str) -> None:
        self.agent = agent
        self.user_id = validate_user_id(user_id)
        self.conversations = UserConversations(self)
        self.runs = UserRuns(self)
        self.skills = UserSkills(self)
        self.configuration = UserConfiguration(self)

    def run(
        self,
        prompt: str,
        *,
        messages: list[Message] | None = None,
        conversation_id: str | None = None,
        skill: str | None = None,
        run_options: "AgentRunOptions | None" = None,
    ) -> RunResult:
        return run_agent_for_user(
            self.agent,
            AgentUserRunRequest(
                prompt=prompt,
                user_id=self.user_id,
                messages=messages,
                conversation_id=conversation_id,
                run_options=resolve_agent_run_options(
                    run_options,
                    skill,
                ),
            ),
        )


class UserConversations:
    """Manage conversations inside one user and Agent scope."""

    def __init__(self, user: UserAgent) -> None:
        self.user = user

    def create(
        self,
        title: str = "",
        *,
        conversation_id: str | None = None,
    ) -> Conversation:
        state = get_agent_state_access(self.user.agent)
        return cast(
            Conversation,
            state.execute_action(
                self.user.user_id,
                ActionRequest.create(
                    "user:conversation",
                    "conversation:new",
                    (ActionEffect.CREATE,),
                ),
                lambda: create_conversation(
                    state.create_event_store(self.user.user_id),
                    title,
                    conversation_id=conversation_id,
                ),
            ),
        )

    def list(self) -> list[Conversation]:
        store = create_agent_event_store(self.user.agent, self.user.user_id)
        return list_conversations(store)

    def read(self, conversation_id: str) -> Conversation:
        store = create_agent_event_store(self.user.agent, self.user.user_id)
        return read_conversation(store, conversation_id)

    def rename(self, conversation_id: str, title: str) -> Conversation:
        state = get_agent_state_access(self.user.agent)
        return cast(
            Conversation,
            state.execute_action(
                self.user.user_id,
                ActionRequest.create(
                    "user:conversation",
                    f"conversation:{conversation_id}",
                    (ActionEffect.UPDATE,),
                ),
                lambda: rename_conversation(
                    state.create_event_store(self.user.user_id),
                    conversation_id,
                    title,
                ),
            ),
        )

    def clear(self, conversation_id: str) -> Conversation:
        state = get_agent_state_access(self.user.agent)
        return cast(
            Conversation,
            state.execute_action(
                self.user.user_id,
                ActionRequest.create(
                    "user:conversation",
                    f"conversation:{conversation_id}",
                    (ActionEffect.DELETE,),
                ),
                lambda: clear_conversation(
                    state.create_event_store(self.user.user_id),
                    conversation_id
                ),
            ),
        )

    def delete(self, conversation_id: str) -> None:
        state = get_agent_state_access(self.user.agent)
        state.execute_action(
            self.user.user_id,
            ActionRequest.create(
                "user:conversation",
                f"conversation:{conversation_id}",
                (ActionEffect.DELETE,),
            ),
            lambda: delete_conversation(
                state.create_event_store(self.user.user_id),
                conversation_id
            ),
        )


class UserRuns:
    """Read traces and record feedback in one user scope."""

    def __init__(self, user: UserAgent) -> None:
        self.user = user

    def read_trace(self, run_id: str) -> TaskTrace:
        return get_agent_state_access(self.user.agent).read_task_trace(
            self.user.user_id,
            run_id,
        )

    def list_checkpoints(self, run_id: str) -> list[dict[str, object]]:
        from core.runtime.run import list_checkpoint_data

        events = create_agent_event_store(self.user.agent, self.user.user_id).read_run_events(
            run_id,
            include_sensitive=True,
        )
        return list_checkpoint_data(events)

    def resume(
        self,
        run_id: str,
        prompt: str,
        *,
        checkpoint_id: str | None = None,
    ) -> RunResult:
        from core.runtime.run import find_checkpoint_data

        store = create_agent_event_store(self.user.agent, self.user.user_id)
        events = store.read_run_events(run_id, include_sensitive=True)
        checkpoint = find_checkpoint_data(events, checkpoint_id)
        return run_agent_for_user(
            self.user.agent,
            AgentUserRunRequest(
                prompt=prompt,
                user_id=self.user.user_id,
                resumed_from_run_id=run_id,
                resume_checkpoint=checkpoint,
            ),
        )

    def record_feedback(
        self,
        run_id: str,
        score: float,
        reason: str = "",
    ) -> RunEvent:
        return get_agent_state_access(self.user.agent).record_task_feedback(
            self.user.user_id,
            run_id,
            score=score,
            reason=reason,
            source="explicit",
        )

    def learn(self, run_id: str) -> "RunLearningResult":
        store = create_agent_event_store(self.user.agent, self.user.user_id)
        snapshot = store.read_run(run_id)
        if snapshot.agent_name != self.user.agent.config.agent.name:
            raise ValueError(f"run belongs to another Agent: {run_id}")
        from skill.learning.runs import learn_from_run
        from core.models import RunLearningResult
        from skill.runtime.handlers import load_configured_freshness_rules

        rules = load_configured_freshness_rules(
            self.user.agent.config,
            store=store,
        )
        result = get_agent_state_access(self.user.agent).execute_action(
            self.user.user_id,
            ActionRequest.create(
                "user:run-learning",
                f"run:{run_id}",
                (ActionEffect.CREATE, ActionEffect.UPDATE),
            ),
            lambda: learn_from_run(store, run_id, rules),
        )
        if not isinstance(result, RunLearningResult):
            raise TypeError("run learning must return RunLearningResult")
        return result

    def list_model_usage_stats(
        self,
        purpose: str | None = None,
    ) -> list["ModelUsageStats"]:
        from core.runtime.model_calls import list_model_usage_stats

        return list_model_usage_stats(
            create_agent_event_store(self.user.agent, self.user.user_id),
            purpose,
        )

    def review(
        self,
        run_id: str,
        evidence: dict[str, object],
    ):
        from skill.learning.review import review_run_evidence
        agent = self.user.agent
        store = create_agent_event_store(agent, self.user.user_id)
        skills = create_agent_skills(agent, self.user.user_id)
        loop = create_agent_task_loop(agent, self.user.user_id, skills)
        decision = loop.model_calls.select_task_model("review", ("text",), store)
        reviewer = loop.create_text_model(
            store,
            "independent_review",
            decision=decision,
        )
        state = get_agent_state_access(agent)
        return state.execute_action(
            self.user.user_id,
            ActionRequest.create(
                "user:run-review",
                f"run:{run_id}",
                (ActionEffect.CREATE,),
            ),
            lambda: review_run_evidence(
                store,
                run_id,
                evidence,
                reviewer.send_messages,
                skills.disclosure,
            ),
        )


class UserSkills:
    """Manage explicit Skill changes and model Skills for one user."""

    def __init__(self, user: UserAgent) -> None:
        self.user = user

    def create_skill_updater(self) -> "SkillUpdater":
        return cast(
            "SkillUpdater",
            _create_skill_updater(self.user),
        )

    def create_model_manager(self) -> "ModelSkillManager":
        from skill.runtime.model_skills import ModelSkillManager

        return ModelSkillManager(
            self.user.agent.config,
            create_agent_event_store(self.user.agent, self.user.user_id),
            get_agent_action_rules(self.user.agent),
        )

    def reload_models(self) -> None:
        reload_agent_models(self.user.agent, self.user.user_id)


class UserConfiguration:
    """Replace one Agent configuration while retaining its storage connection."""

    def __init__(self, user: UserAgent) -> None:
        self.user = user

    def replace(self, config: CommonConfig) -> None:
        replace_agent_configuration(self.user.agent, config)


def _create_skill_updater(user: UserAgent) -> "SkillUpdater":
    from skill.learning.update import SkillUpdater

    agent = user.agent
    store = create_agent_event_store(agent, user.user_id)
    skills = create_agent_skills(agent, user.user_id)
    task_loop = create_agent_task_loop(agent, user.user_id, skills)
    return SkillUpdater(
        skills.disclosure,
        store=store,
        propose_model=task_loop.create_text_model(store, "skill_change_proposal"),
        test_model=task_loop.create_text_model(store, "skill_change_test"),
        on_skill_changed=lambda manifest: activate_agent_skill_change(
            agent,
            user.user_id,
            manifest,
        ),
        action_rules=get_agent_state_access(agent).require_action_rules(),
    )

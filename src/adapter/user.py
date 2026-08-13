"""Explicit user-scoped access to one configured Agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from core.provider import Message
from core.config import CommonConfig
from core.models import resolve_agent_run_options, validate_user_id
from core.state.models import Conversation, RunEvent
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
    from adapter.agent import Agent
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
        return self.agent._run_for_user(
            prompt,
            self.user_id,
            messages=messages,
            conversation_id=conversation_id,
            run_options=resolve_agent_run_options(run_options, skill),
        )

    def _store(self):
        return self.agent._create_event_store(self.user_id)

    def _execute(self, request: ActionRequest, action):
        return self.agent._execute_action(self.user_id, request, action)


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
        return cast(
            Conversation,
            self.user._execute(
                ActionRequest.create(
                    "user:conversation",
                    "conversation:new",
                    (ActionEffect.CREATE,),
                ),
                lambda: create_conversation(
                    self.user._store(),
                    title,
                    conversation_id=conversation_id,
                ),
            ),
        )

    def list(self) -> list[Conversation]:
        return list_conversations(self.user._store())

    def read(self, conversation_id: str) -> Conversation:
        return read_conversation(self.user._store(), conversation_id)

    def rename(self, conversation_id: str, title: str) -> Conversation:
        return cast(
            Conversation,
            self.user._execute(
                ActionRequest.create(
                    "user:conversation",
                    f"conversation:{conversation_id}",
                    (ActionEffect.UPDATE,),
                ),
                lambda: rename_conversation(
                    self.user._store(),
                    conversation_id,
                    title,
                ),
            ),
        )

    def clear(self, conversation_id: str) -> Conversation:
        return cast(
            Conversation,
            self.user._execute(
                ActionRequest.create(
                    "user:conversation",
                    f"conversation:{conversation_id}",
                    (ActionEffect.DELETE,),
                ),
                lambda: clear_conversation(
                    self.user._store(),
                    conversation_id
                ),
            ),
        )

    def delete(self, conversation_id: str) -> None:
        self.user._execute(
            ActionRequest.create(
                "user:conversation",
                f"conversation:{conversation_id}",
                (ActionEffect.DELETE,),
            ),
            lambda: delete_conversation(
                self.user._store(),
                conversation_id
            ),
        )


class UserRuns:
    """Read traces and record feedback in one user scope."""

    def __init__(self, user: UserAgent) -> None:
        self.user = user

    def read_trace(self, run_id: str) -> TaskTrace:
        return self.user.agent._read_task_trace(
            self.user.user_id,
            run_id,
        )

    def list_checkpoints(self, run_id: str) -> list[dict[str, object]]:
        from core.runtime.run import list_checkpoint_data

        events = self.user._store().read_run_events(
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

        store = self.user._store()
        events = store.read_run_events(run_id, include_sensitive=True)
        checkpoint = find_checkpoint_data(events, checkpoint_id)
        return self.user.agent._run_for_user(
            prompt,
            self.user.user_id,
            resumed_from_run_id=run_id,
            resume_checkpoint=checkpoint,
        )

    def record_feedback(
        self,
        run_id: str,
        score: float,
        reason: str = "",
    ) -> RunEvent:
        return self.user.agent._record_task_feedback(
            self.user.user_id,
            run_id,
            score=score,
            reason=reason,
            source="explicit",
        )

    def learn(self, run_id: str) -> "RunLearningResult":
        store = self.user._store()
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
        result = self.user._execute(
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
            self.user._store(),
            purpose,
        )

    def review(
        self,
        run_id: str,
        evidence: dict[str, object],
    ):
        from skill.learning.runs import review_run_evidence
        agent = self.user.agent
        store = self.user._store()
        skills = agent._create_skills(self.user.user_id)
        loop = agent._create_task_loop(self.user.user_id, skills)
        decision = loop.model_calls.select_task_model("review", ("text",), store)
        reviewer = loop.create_text_model(
            store,
            "independent_review",
            decision=decision,
        )
        return self.user._execute(
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
            self.user._store(),
            self.user.agent._action_rules(),
        )

    def reload_models(self) -> None:
        self.user.agent._reload_models(self.user.user_id)


class UserConfiguration:
    """Replace one Agent configuration while retaining its storage connection."""

    def __init__(self, user: UserAgent) -> None:
        self.user = user

    def replace(self, config: CommonConfig) -> None:
        self.user.agent._replace_configuration(config)


def _create_skill_updater(user: UserAgent) -> "SkillUpdater":
    from skill.learning.update import SkillUpdater

    agent = user.agent
    store = user._store()
    skills = agent._create_skills(user.user_id)
    task_loop = agent._create_task_loop(user.user_id, skills)
    return SkillUpdater(
        skills.disclosure,
        store=store,
        propose_model=task_loop.create_text_model(store, "skill_change_proposal"),
        test_model=task_loop.create_text_model(store, "skill_change_test"),
        on_skill_changed=lambda manifest: (
            agent._reload_models(user.user_id)
            if manifest.skill_type == "model"
            else None
        ),
        action_rules=agent._action_rules(),
    )

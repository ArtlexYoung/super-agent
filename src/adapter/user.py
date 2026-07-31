"""Explicit user-scoped access to one configured Agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from core.provider.chat import Message
from core.config import AgentConfig
from skill.evolution.models import SkillEvolutionState
from skill.evolution.state import list_skill_evolutions, read_skill_evolution
from core.models import validate_user_id
from core.state.models import Conversation, RunEvent
from adapter.conversations import (
    clear_conversation,
    create_conversation,
    delete_conversation,
    list_conversations,
    read_conversation,
    rename_conversation,
)
from skill.task.model_calls import ModelUsageStats
from core.checks import ActionEffect, ActionRequest
from core.models import RunLearningResult, RunResult, TaskTrace
from skill.ecosystem.models import ModelSkillManager

if TYPE_CHECKING:
    from super_agent import Agent
    from core.models import AgentRunOptions
    from skill.evolution.change.manager import SkillEvolutionManager


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
        scene: str | None = None,
        use_scenes: bool | None = None,
        run_options: "AgentRunOptions | None" = None,
    ) -> RunResult:
        return self.agent._run_for_user(
            prompt,
            self.user_id,
            messages=messages,
            conversation_id=conversation_id,
            run_options=self.agent._resolve_run_options(
                run_options,
                scene,
                use_scenes,
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
        runtime = self.user.agent.runtime
        return cast(
            Conversation,
            runtime.execute_management_action(
                self.user.user_id,
                ActionRequest.create(
                    "user:conversation",
                    "conversation:new",
                    (ActionEffect.CREATE,),
                ),
                lambda: create_conversation(
                    runtime.create_event_store(self.user.user_id),
                    title,
                    conversation_id=conversation_id,
                ),
            ),
        )

    def list(self) -> list[Conversation]:
        store = self.user.agent.runtime.create_event_store(self.user.user_id)
        return list_conversations(store)

    def read(self, conversation_id: str) -> Conversation:
        store = self.user.agent.runtime.create_event_store(self.user.user_id)
        return read_conversation(store, conversation_id)

    def rename(self, conversation_id: str, title: str) -> Conversation:
        runtime = self.user.agent.runtime
        return cast(
            Conversation,
            runtime.execute_management_action(
                self.user.user_id,
                ActionRequest.create(
                    "user:conversation",
                    f"conversation:{conversation_id}",
                    (ActionEffect.UPDATE,),
                ),
                lambda: rename_conversation(
                    runtime.create_event_store(self.user.user_id),
                    conversation_id,
                    title,
                ),
            ),
        )

    def clear(self, conversation_id: str) -> Conversation:
        runtime = self.user.agent.runtime
        return cast(
            Conversation,
            runtime.execute_management_action(
                self.user.user_id,
                ActionRequest.create(
                    "user:conversation",
                    f"conversation:{conversation_id}",
                    (ActionEffect.DELETE,),
                ),
                lambda: clear_conversation(
                    runtime.create_event_store(self.user.user_id),
                    conversation_id
                ),
            ),
        )

    def delete(self, conversation_id: str) -> None:
        runtime = self.user.agent.runtime
        runtime.execute_management_action(
            self.user.user_id,
            ActionRequest.create(
                "user:conversation",
                f"conversation:{conversation_id}",
                (ActionEffect.DELETE,),
            ),
            lambda: delete_conversation(
                runtime.create_event_store(self.user.user_id),
                conversation_id
            ),
        )


class UserRuns:
    """Read traces and record feedback in one user scope."""

    def __init__(self, user: UserAgent) -> None:
        self.user = user

    def read_trace(self, run_id: str) -> TaskTrace:
        return self.user.agent.runtime.read_task_trace(
            run_id,
            user_id=self.user.user_id,
        )

    def record_feedback(
        self,
        run_id: str,
        score: float,
        reason: str = "",
    ) -> RunEvent:
        return self.user.agent.runtime.record_task_feedback(
            run_id,
            score,
            reason,
            user_id=self.user.user_id,
        )

    def learn(self, run_id: str) -> RunLearningResult:
        return self.user.agent.runtime.learn_from_run(
            run_id,
            user_id=self.user.user_id,
        )

    def list_model_usage_stats(
        self,
        purpose: str | None = None,
    ) -> list[ModelUsageStats]:
        return self.user.agent.runtime.list_model_usage_stats(
            user_id=self.user.user_id,
            purpose=purpose,
        )


class UserSkills:
    """Manage Skill evolution and model Skills for one user."""

    def __init__(self, user: UserAgent) -> None:
        self.user = user

    def create_evolution_manager(self) -> "SkillEvolutionManager":
        return cast(
            "SkillEvolutionManager",
            self.user.agent.runtime.create_skill_updater(
                self.user.user_id,
                lambda manifest: self.user.agent._activate_changed_skill(
                    manifest,
                    self.user.user_id,
                ),
            ),
        )

    def list_evolutions(self, status: str | None = None) -> list[SkillEvolutionState]:
        store = self.user.agent.runtime.create_event_store(self.user.user_id)
        return list_skill_evolutions(store, status)

    def read_evolution(self, evolution_id: str) -> SkillEvolutionState:
        store = self.user.agent.runtime.create_event_store(self.user.user_id)
        return read_skill_evolution(store, evolution_id)

    def create_model_manager(self) -> ModelSkillManager:
        return ModelSkillManager(
            self.user.agent.config,
            self.user.agent.runtime.create_event_store(self.user.user_id),
            self.user.agent.action_rules,
        )

    def reload_models(self) -> None:
        self.user.agent._reload_model_profiles(self.user.user_id)

class UserConfiguration:
    """Replace one Agent configuration while retaining its storage connection."""

    def __init__(self, user: UserAgent) -> None:
        self.user = user

    def replace(self, config: AgentConfig) -> None:
        self.user.agent._replace_configuration(config)

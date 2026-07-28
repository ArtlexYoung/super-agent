"""Explicit user-scoped access to one configured Agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from provider.chat import Message
from runtime.config import AgentConfig
from runtime.evolution.service import AutomaticEvolutionService
from runtime.evolution.state import SkillEvolutionState
from runtime.models import Conversation, RunEvent
from runtime.routing import ModelRoutingStats
from runtime.safety import ActionEffect, ActionRequest
from runtime.tasks import TaskResult, TaskTrace
from skill.kinds.model_management import ModelSkillManager

if TYPE_CHECKING:
    from agents.agent import Agent, AgentRunOptions
    from skill.evolution.manager import SkillEvolutionManager


class UserAgent:
    """Bind every stateful operation to one trusted user identifier."""

    def __init__(self, agent: "Agent", user_id: str) -> None:
        clean_user_id = user_id.strip()
        if not clean_user_id:
            raise ValueError("user id cannot be empty")
        self.agent = agent
        self.user_id = clean_user_id
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
        run_options: "AgentRunOptions | None" = None,
    ) -> TaskResult:
        return self.agent._run_for_user(
            prompt,
            self.user_id,
            messages=messages,
            conversation_id=conversation_id,
            run_options=run_options,
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
                lambda: runtime.create_store(self.user.user_id).create_conversation(
                    title,
                    conversation_id=conversation_id,
                ),
            ),
        )

    def list(self) -> list[Conversation]:
        return self.user.agent.runtime.create_store(
            self.user.user_id
        ).list_conversations()

    def read(self, conversation_id: str) -> Conversation:
        return self.user.agent.runtime.create_store(
            self.user.user_id
        ).read_conversation(conversation_id)

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
                lambda: runtime.create_store(self.user.user_id).rename_conversation(
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
                lambda: runtime.create_store(self.user.user_id).clear_conversation(
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
            lambda: runtime.create_store(self.user.user_id).delete_conversation(
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

    def list_model_routing_stats(
        self,
        purpose: str | None = None,
    ) -> list[ModelRoutingStats]:
        return self.user.agent.runtime.list_model_routing_stats(
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
        return self._evolution_service().list_skill_evolutions(status)

    def read_evolution(self, evolution_id: str) -> SkillEvolutionState:
        return self._evolution_service().read_skill_evolution(evolution_id)

    def create_model_manager(self) -> ModelSkillManager:
        return ModelSkillManager(
            self.user.agent.config,
            self.user.agent.runtime.create_store(self.user.user_id),
            self.user.agent.safety_policy,
        )

    def reload_models(self) -> None:
        self.user.agent._reload_model_profiles(self.user.user_id)

    def _evolution_service(self) -> AutomaticEvolutionService:
        manager = self.create_evolution_manager()
        return AutomaticEvolutionService(manager.store, manager)


class UserConfiguration:
    """Replace one Agent configuration while retaining its storage connection."""

    def __init__(self, user: UserAgent) -> None:
        self.user = user

    def replace(self, config: AgentConfig) -> None:
        self.user.agent._replace_configuration(config, self.user.user_id)

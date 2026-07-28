"""Explicit user-scoped access to one configured Agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from core.provider.chat import Message
from core.config import AgentConfig
from core.evolution.state import (
    SkillEvolutionState,
    list_skill_evolutions,
    read_skill_evolution,
)
from core.identity import validate_user_id
from core.state.models import Conversation, RunEvent
from core.task.routing import ModelRoutingStats
from core.actions import ActionEffect, ActionRequest
from core.task.models import TaskResult, TaskTrace
from skill.kinds.model_management import ModelSkillManager
from skill.kinds.scene import CreatedSkillScene, SkillSceneInput, SkillSceneManager
from skill.runners.defaults import create_progressive_skill_disclosure

if TYPE_CHECKING:
    from core.agent import Agent, AgentRunOptions
    from skill.evolution.manager import SkillEvolutionManager


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
        run_options: "AgentRunOptions | None" = None,
    ) -> TaskResult:
        return self.agent._run_for_user(
            prompt,
            self.user_id,
            messages=messages,
            conversation_id=conversation_id,
            run_options=self.agent._run_options_for_scene(run_options, scene),
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
        store = self.user.agent.runtime.create_store(self.user.user_id)
        return list_skill_evolutions(store, status)

    def read_evolution(self, evolution_id: str) -> SkillEvolutionState:
        store = self.user.agent.runtime.create_store(self.user.user_id)
        return read_skill_evolution(store, evolution_id)

    def create_model_manager(self) -> ModelSkillManager:
        return ModelSkillManager(
            self.user.agent.config,
            self.user.agent.runtime.create_store(self.user.user_id),
            self.user.agent.action_rules,
        )

    def create_scene(self, request: SkillSceneInput) -> CreatedSkillScene:
        runtime = self.user.agent.runtime
        store = runtime.create_store(self.user.user_id)
        disclosure = create_progressive_skill_disclosure(
            self.user.agent.config,
            store=store,
        )
        index = disclosure.prepare_skill_index()
        manager = SkillSceneManager(store, index)
        return cast(
            CreatedSkillScene,
            runtime.execute_management_action(
                self.user.user_id,
                ActionRequest.create(
                    "user:skill-scene",
                    f"skill:owned:scene:{request.name}",
                    (ActionEffect.CREATE,),
                ),
                lambda: manager.create_skill_scene(request),
            ),
        )

    def reload_models(self) -> None:
        self.user.agent._reload_model_profiles(self.user.user_id)

class UserConfiguration:
    """Replace one Agent configuration while retaining its storage connection."""

    def __init__(self, user: UserAgent) -> None:
        self.user = user

    def replace(self, config: AgentConfig) -> None:
        self.user.agent._replace_configuration(config)

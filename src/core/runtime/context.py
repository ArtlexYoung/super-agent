"""Dependencies shared by one Agent and its task Runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from core.checks import ActionRules
from core.config import AgentConfig
from core.events import StorageBackend
from core.provider.pool import ProviderPool
from core.provider.secrets import UserSecretResolver
from core.skill_use.handlers import SkillHandlers
from core.skill_use.models import ModelProfile
from core.state.subscribers import RuntimeEventSubscribers


@dataclass
class RuntimeContext:
    config: AgentConfig
    provider_pool: ProviderPool
    skill_handlers: SkillHandlers
    storage: StorageBackend | None
    create_action_rules: Callable[[], ActionRules] | None
    user_secrets: UserSecretResolver
    code_model_profiles: tuple[ModelProfile, ...] = ()
    event_subscribers: RuntimeEventSubscribers = field(
        default_factory=RuntimeEventSubscribers
    )

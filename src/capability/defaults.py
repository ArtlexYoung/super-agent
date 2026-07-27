"""Built-in Skill executors and central progressive disclosure defaults."""

from __future__ import annotations

from capability.registry import CapabilityRegistry
from capability.skill_executors import create_builtin_skill_executors
from runtime.config import AgentConfig
from runtime.identity import LOCAL_USER_ID, RunIdentity
from runtime.storage import StorageBackend, create_storage_backend
from runtime.store import RuntimeStore
from skill.disclosure import ProgressiveDisclosureCore


def create_default_capability_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for executor in create_builtin_skill_executors().values():
        registry.add_skill_executor(executor)
    return registry


def create_progressive_skill_disclosure(
    config: AgentConfig,
    *,
    store: RuntimeStore | None = None,
    storage: StorageBackend | None = None,
    user_id: str = LOCAL_USER_ID,
    identity: RunIdentity | None = None,
) -> ProgressiveDisclosureCore:
    selected_store = store
    if selected_store is None:
        selected_storage = storage or create_storage_backend(
            config.storage.backend,
            str(config.storage.path),
            config.storage.url_env,
        )
        selected_store = RuntimeStore(
            selected_storage,
            config.storage.path,
            user_id,
            config.agent.name,
        )
    disabled = set(config.agent.disable_names)
    roots = (
        config.paths.skills
        if "skill" in config.agent.use_features and "skill" not in disabled
        else []
    )
    return ProgressiveDisclosureCore(
        roots,
        selected_store,
        disabled_names=config.agent.disable_names,
        identity=identity,
    )

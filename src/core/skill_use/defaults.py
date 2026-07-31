"""Built-in Skill loaders and central progressive disclosure defaults."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core.skill_use.registry import SkillLoaders
from core.skill_use.builtins import create_builtin_skill_loaders
from core.skill_use.mcp import McpServers
from core.config import AgentConfig
from core.models import RunIdentity
from skill.disclosure import DisclosureRecorder, ProgressiveDisclosureCore
from core.skill_use.skills import Skills

if TYPE_CHECKING:
    from core.state.events import EventStore
    from core.evolution.policy import EvolutionPolicy


def create_default_skill_loaders(
    mcp_servers: McpServers | None = None,
) -> SkillLoaders:
    loaders = SkillLoaders()
    servers = mcp_servers or McpServers()
    for loader in create_builtin_skill_loaders(servers):
        loaders.add_skill_loader(loader)
    return loaders


def create_progressive_skill_disclosure(
    config: AgentConfig,
    *,
    store: EventStore | None = None,
    identity: RunIdentity | None = None,
    record_disclosures: bool | None = None,
    include_freshness: bool = True,
) -> ProgressiveDisclosureCore:
    freshness_stats = {}
    if (
        store is not None
        and include_freshness
        and "evolution" not in config.agent.disabled_skills
    ):
        from core.evolution.metrics import calculate_skill_freshness
        from core.evolution.policy import load_evolution_policy
        from core.evolution.records import read_evaluation_records

        policy_disclosure = create_progressive_skill_disclosure(
            config,
            store=store,
            record_disclosures=False,
            include_freshness=False,
        )
        policy_disclosure.prepare_skill_index()
        policy = load_evolution_policy(
            policy_disclosure,
            config.agent.skills,
            disclose=False,
        )
        freshness_stats = calculate_skill_freshness(
            read_evaluation_records(store, source_type="agent_run"),
            policy,
        )
    disabled = set(config.agent.disabled_skills)
    roots = [] if "skill" in disabled else config.paths.skills
    should_record = identity is not None if record_disclosures is None else record_disclosures
    if should_record and store is None:
        raise ValueError("recording Skill disclosure requires an EventStore")
    return ProgressiveDisclosureCore(
        roots,
        user_skill_roots=([] if store is None else [store.private_root / "skills"]),
        builtin_skill_roots=[_builtin_skill_root()],
        disabled_names=config.agent.disabled_skills,
        freshness_stats=freshness_stats,
        recorder=(
            create_runtime_disclosure_recorder(store, identity)
            if should_record and store is not None
            else None
        ),
        record_event=None,
    )


def load_configured_evolution_policy(
    config: AgentConfig,
    *,
    store: EventStore | None = None,
) -> EvolutionPolicy:
    """Load the Agent's evolution Skill through the same disclosure core."""
    if "evolution" in config.agent.disabled_skills:
        raise ValueError("evolution Skills are disabled for this Agent")
    from core.evolution.policy import load_evolution_policy

    disclosure = create_progressive_skill_disclosure(
        config,
        store=store,
        record_disclosures=False,
        include_freshness=False,
    )
    disclosure.prepare_skill_index()
    return load_evolution_policy(disclosure, config.agent.skills, disclose=False)


def load_configured_evolution_policy_if_enabled(
    config: AgentConfig,
    *,
    store: EventStore | None = None,
) -> EvolutionPolicy | None:
    """Load the selected evolution policy, or return None when its type is disabled."""
    if "evolution" in config.agent.disabled_skills:
        return None
    return load_configured_evolution_policy(config, store=store)


def create_skills(
    config: AgentConfig,
    *,
    loaders: SkillLoaders | None = None,
    store: EventStore | None = None,
    identity: RunIdentity | None = None,
    record_disclosures: bool | None = None,
    include_freshness: bool = True,
) -> Skills:
    """Build one complete Skill snapshot through the central entry point."""
    disclosure = create_progressive_skill_disclosure(
        config,
        store=store,
        identity=identity,
        record_disclosures=record_disclosures,
        include_freshness=include_freshness,
    )
    return Skills(disclosure, loaders)


def create_runtime_disclosure_recorder(
    store: EventStore,
    identity: RunIdentity | None = None,
) -> DisclosureRecorder:
    """Adapt Runtime state recording to the storage-free disclosure contract."""
    disclosure = store.disclosure
    return DisclosureRecorder(
        cache_root=disclosure.cache_root,
        history_path=disclosure.history_path,
        write_text=lambda key, stage, path, content: disclosure.write_text(
            identity,
            key,
            stage,
            path,
            content,
        ),
        write_json=lambda key, stage, path, content: disclosure.write_json(
            identity,
            key,
            stage,
            path,
            content,
        ),
        read_content=disclosure.read_content,
        read_history=disclosure.read_history,
    )


def _builtin_skill_root() -> Path:
    root = Path(__file__).resolve().parents[2] / "skill" / "builtin"
    if not root.is_dir():
        raise RuntimeError(f"built-in Skill root not found: {root}")
    return root

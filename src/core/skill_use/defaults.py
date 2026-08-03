"""Built-in Skill handlers and central progressive disclosure defaults."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core.skill_use.handlers import SkillCollection, SkillHandlers
from core.skill_use.builtins import create_builtin_skill_handlers
from core.skill_use.mcp import McpServers
from core.config import CommonConfig
from core.models import RunIdentity
from skill.disclosure import DisclosureRecorder, ProgressiveDisclosureCore

if TYPE_CHECKING:
    from core.state.events import EventStore
    from core.evaluation.rules import FreshnessRules


def create_default_skill_handlers(
    mcp_servers: McpServers | None = None,
) -> SkillHandlers:
    handlers = SkillHandlers()
    servers = mcp_servers or McpServers()
    for handler in create_builtin_skill_handlers(servers):
        handlers.add(handler)
    return handlers


def create_progressive_skill_disclosure(
    config: CommonConfig,
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
        and "freshness" not in config.agent.disabled_skills
    ):
        from core.evaluation.freshness import calculate_skill_freshness
        from core.evaluation.rules import load_freshness_rules
        from core.evaluation.records import read_evaluation_records

        policy_disclosure = create_progressive_skill_disclosure(
            config,
            store=store,
            record_disclosures=False,
            include_freshness=False,
        )
        policy_disclosure.prepare_skill_index()
        rules = load_freshness_rules(
            policy_disclosure,
            config.agent.skills,
            disclose=False,
        )
        freshness_stats = calculate_skill_freshness(
            read_evaluation_records(store, source_type="agent_run"),
            rules,
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


def load_configured_freshness_rules(
    config: CommonConfig,
    *,
    store: EventStore | None = None,
) -> FreshnessRules:
    """Load deterministic freshness settings through central disclosure."""
    if "freshness" in config.agent.disabled_skills:
        raise ValueError("freshness Skills are disabled for this Agent")
    from core.evaluation.rules import load_freshness_rules

    disclosure = create_progressive_skill_disclosure(
        config,
        store=store,
        record_disclosures=False,
        include_freshness=False,
    )
    disclosure.prepare_skill_index()
    return load_freshness_rules(disclosure, config.agent.skills, disclose=False)


def load_configured_freshness_rules_if_enabled(
    config: CommonConfig,
    *,
    store: EventStore | None = None,
) -> FreshnessRules | None:
    """Load selected freshness rules, or None when explicitly disabled."""
    if "freshness" in config.agent.disabled_skills:
        return None
    return load_configured_freshness_rules(config, store=store)


def create_skills(
    config: CommonConfig,
    *,
    handlers: SkillHandlers | None = None,
    store: EventStore | None = None,
    identity: RunIdentity | None = None,
    record_disclosures: bool | None = None,
    include_freshness: bool = True,
) -> SkillCollection:
    """Build one complete Skill snapshot through the central entry point."""
    disclosure = create_progressive_skill_disclosure(
        config,
        store=store,
        identity=identity,
        record_disclosures=record_disclosures,
        include_freshness=include_freshness,
    )
    return SkillCollection(disclosure, handlers)


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

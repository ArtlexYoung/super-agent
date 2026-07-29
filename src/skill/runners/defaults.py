"""Built-in Skill runners and central progressive disclosure defaults."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from skill.runners.registry import SkillRunners
from skill.runners.builtins import create_builtin_skill_runners
from skill.runners.mcp import McpServers
from core.config import AgentConfig
from core.run import RunIdentity
from skill.disclosure import DisclosureRecorder, ProgressiveDisclosureCore

if TYPE_CHECKING:
    from core.state.store import RuntimeStore


def create_default_skill_runners(
    mcp_servers: McpServers | None = None,
) -> SkillRunners:
    runners = SkillRunners()
    servers = mcp_servers or McpServers()
    for runner in create_builtin_skill_runners(servers):
        runners.add_skill_runner(runner)
    return runners


def create_progressive_skill_disclosure(
    config: AgentConfig,
    *,
    store: RuntimeStore | None = None,
    identity: RunIdentity | None = None,
    record_disclosures: bool | None = None,
    include_freshness: bool = True,
) -> ProgressiveDisclosureCore:
    freshness_stats = {}
    if store is not None and include_freshness:
        from skill.evolution.freshness import calculate_skill_freshness

        freshness_stats = calculate_skill_freshness(
            store.read_evaluation_records(source_type="agent_run")
        )
    disabled = set(config.agent.disabled_skills)
    roots = [] if "skill" in disabled else config.paths.skills
    should_record = identity is not None if record_disclosures is None else record_disclosures
    if should_record and store is None:
        raise ValueError("recording Skill disclosure requires a RuntimeStore")
    return ProgressiveDisclosureCore(
        roots,
        user_skill_roots=([] if store is None else [store.private_root / "skills"]),
        builtin_skill_roots=[_skill_scene_root()],
        disabled_names=config.agent.disabled_skills,
        freshness_stats=freshness_stats,
        recorder=(
            create_runtime_disclosure_recorder(store, identity)
            if should_record and store is not None
            else None
        ),
        record_event=None,
    )


def create_runtime_disclosure_recorder(
    store: RuntimeStore,
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


def _skill_scene_root() -> Path:
    source_root = Path(__file__).resolve().parents[2]
    candidates = [
        source_root / "skill_scenes",
        source_root.parent / "skill_scenes",
    ]
    existing = [path for path in candidates if path.is_dir()]
    if len(existing) != 1:
        paths = ", ".join(str(path) for path in candidates)
        raise RuntimeError(f"expected exactly one installed skill_scenes root: {paths}")
    return existing[0]

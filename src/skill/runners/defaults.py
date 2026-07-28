"""Built-in Skill runners and central progressive disclosure defaults."""

from __future__ import annotations

from pathlib import Path

from skill.runners.registry import SkillRunners
from skill.runners.builtins import create_builtin_skill_runners
from core.config import AgentConfig
from core.identity import LOCAL_USER_ID, RunIdentity
from core.storage import StorageBackend, create_storage_backend
from core.state.store import RuntimeStore
from skill.disclosure import ProgressiveDisclosureCore


def create_default_skill_runners() -> SkillRunners:
    runners = SkillRunners()
    for runner in create_builtin_skill_runners():
        runners.add_skill_runner(runner)
    return runners


def create_progressive_skill_disclosure(
    config: AgentConfig,
    *,
    store: RuntimeStore | None = None,
    storage: StorageBackend | None = None,
    user_id: str = LOCAL_USER_ID,
    identity: RunIdentity | None = None,
    record_disclosures: bool | None = None,
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
    disabled = set(config.agent.disabled_skills)
    roots = [] if "skill" in disabled else config.paths.skills
    return ProgressiveDisclosureCore(
        roots,
        selected_store,
        user_skill_roots=[selected_store.private_root / "skills"],
        builtin_skill_roots=[_skill_scene_root()],
        disabled_names=config.agent.disabled_skills,
        identity=identity,
        record_disclosures=(
            identity is not None
            if record_disclosures is None
            else record_disclosures
        ),
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

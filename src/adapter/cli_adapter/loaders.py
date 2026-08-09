"""CLI adapters that construct the common Agent and storage services."""

from __future__ import annotations

from pathlib import Path

from core.config import CommonConfig
from core.models import LOCAL_USER_ID
from core.state.store import EventStore
from super_agent import Agent
from adapter.cli_adapter.configuration import TerminalActionRules


CommonConfigSource = CommonConfig | str | Path | None


def load_common_config(source: CommonConfigSource = None) -> CommonConfig:
    if source is None:
        return CommonConfig.load_automatically()
    if isinstance(source, CommonConfig):
        return source
    return CommonConfig.load_from_file(source)


def load_agent(
    source: CommonConfigSource = None,
    *,
    use_storage: bool = True,
) -> Agent:
    return Agent(
        load_common_config(source),
        use_storage=use_storage,
        action_rules=TerminalActionRules(),
    )


def load_event_store(
    source: CommonConfigSource = None,
    user_id: str = LOCAL_USER_ID,
) -> EventStore:
    config = load_common_config(source)
    from adapter.storage import create_storage_backend
    from adapter.storage.disclosure import DisclosureStorage

    backend = create_storage_backend(
        config.storage.backend,
        str(config.storage.path),
        config.storage.url_env,
    )
    return EventStore(
        backend,
        config.storage.path,
        user_id,
        config.agent.name,
        disclosure_factory=lambda cache_root, store: DisclosureStorage(
            cache_root,
            store,
        ),
    )

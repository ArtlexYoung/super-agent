"""CLI command groups and their shared readers."""

from __future__ import annotations

from pathlib import Path

from super_agent import Agent
from core.config import CommonConfig
from core.models import LOCAL_USER_ID
from core.state.events import EventStore


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
    return Agent(load_common_config(source), use_storage=use_storage)


def load_event_store(
    source: CommonConfigSource = None,
    user_id: str = LOCAL_USER_ID,
) -> EventStore:
    config = load_common_config(source)
    from adapter.storage import create_storage_backend

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
    )

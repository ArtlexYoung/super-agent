"""CLI command groups and their shared loaders."""

from __future__ import annotations

from pathlib import Path

from super_agent import Agent
from core.config import AgentConfig
from core.models import LOCAL_USER_ID
from skill.state.events import EventStore


AgentConfigSource = AgentConfig | str | Path | None


def load_agent_config(source: AgentConfigSource = None) -> AgentConfig:
    if source is None:
        return AgentConfig.load_automatically()
    if isinstance(source, AgentConfig):
        return source
    return AgentConfig.load_from_file(source)


def load_agent(source: AgentConfigSource = None) -> Agent:
    return Agent(load_agent_config(source), use_storage=True)


def load_event_store(
    source: AgentConfigSource = None,
    user_id: str = LOCAL_USER_ID,
) -> EventStore:
    config = load_agent_config(source)
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

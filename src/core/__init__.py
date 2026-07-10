from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "Agent": ("core.agent", "Agent"),
    "AgentConfig": ("core.config", "AgentConfig"),
    "AgentSettings": ("core.config", "AgentSettings"),
    "AnthropicCompatibleProvider": ("core.provider", "AnthropicCompatibleProvider"),
    "ChatProvider": ("core.provider", "ChatProvider"),
    "MockProvider": ("core.provider", "MockProvider"),
    "ModelSettings": ("core.config", "ModelSettings"),
    "OpenAICompatibleProvider": ("core.provider", "OpenAICompatibleProvider"),
    "PathsSettings": ("core.config", "PathsSettings"),
    "RunContext": ("core.run", "RunContext"),
    "RunEvent": ("core.run", "RunEvent"),
    "RunTraceStore": ("core.run", "RunTraceStore"),
    "create_chat_provider": ("core.provider", "create_chat_provider"),
    "create_skill_loader_for_agent_config": ("core.agent", "create_skill_loader_for_agent_config"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'core' has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value

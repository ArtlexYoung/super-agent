from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "Agent": ("core.agent", "Agent"),
    "AgentConfig": ("core.config", "AgentConfig"),
    "AgentSettings": ("core.config", "AgentSettings"),
    "BenchmarkCase": ("core.benchmark", "BenchmarkCase"),
    "BenchmarkCaseResult": ("core.benchmark", "BenchmarkCaseResult"),
    "BenchmarkReport": ("core.benchmark", "BenchmarkReport"),
    "AnthropicCompatibleProvider": ("core.provider", "AnthropicCompatibleProvider"),
    "ChatProvider": ("core.provider", "ChatProvider"),
    "MockProvider": ("core.provider", "MockProvider"),
    "ModelSettings": ("core.config", "ModelSettings"),
    "ModelResponse": ("core.provider", "ModelResponse"),
    "OpenAICompatibleProvider": ("core.provider", "OpenAICompatibleProvider"),
    "PathsSettings": ("core.config", "PathsSettings"),
    "RunContext": ("core.run", "RunContext"),
    "RunEvent": ("core.run", "RunEvent"),
    "RunTraceStore": ("core.run", "RunTraceStore"),
    "SkillBenchmark": ("core.benchmark", "SkillBenchmark"),
    "run_event_from_dict": ("core.run", "run_event_from_dict"),
    "run_event_to_dict": ("core.run", "run_event_to_dict"),
    "SkillTools": ("core.tools", "SkillTools"),
    "ToolCall": ("core.provider", "ToolCall"),
    "create_chat_provider": ("core.provider", "create_chat_provider"),
    "benchmark_report_to_dict": ("core.benchmark", "benchmark_report_to_dict"),
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

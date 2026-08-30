"""为 Skill 和组织工具测试创建最小运行状态。"""

from collections.abc import Iterable, Mapping

from core.event import RunIdentity
from core.model import Tool
from core.resources import ResourceCenter
from core.run import RuntimeLifecycle, ToolRegistry, _RunState


def make_run_state(
    *,
    identity: RunIdentity | None = None,
    instructions: Iterable[str] = (),
    active_tools: Iterable[Tool] = (),
    available_tools: Iterable[Tool] = (),
    mcp_tools_by_server: Mapping[str, tuple[Tool, ...]] | None = None,
    resource_center: ResourceCenter | None = None,
    runtime_lifecycle: RuntimeLifecycle | None = None,
    context_characters: int = 0,
) -> _RunState:
    return _RunState(
        identity=identity or RunIdentity(),
        messages=[],
        instructions=list(instructions),
        tool_registry=ToolRegistry(active_tools, available_tools),
        resource_center=resource_center or ResourceCenter(),
        mcp_tools_by_server=mcp_tools_by_server or {},
        runtime_lifecycle=runtime_lifecycle,
        context_characters=context_characters,
    )

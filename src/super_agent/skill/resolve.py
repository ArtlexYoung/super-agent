"""把通用能力需求解析为宿主原生工具或 MCP Server 的具体供应。

``compatibility_report`` 只给出“是否满足”；此模块进一步给出“由谁满足”：对每个必需
能力，优先映射到 Harness 原生工具名；若宿主没有原生映射，则看是否有提供该能力的
MCP Server。结果是一个结构化解析蓝图，供宿主把能力落地到自己的工具/MCP，不复制
任何工具实现。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityResolution:
    """一个通用能力到具体供给来源的解析结果。

    ``kind`` 是 ``native``（宿主原生工具）或 ``mcp``（某个 MCP Server 提供的工具）；
    ``provider`` 是工具名或 MCP Server 名；``capability`` 是该能力名。
    """

    capability: str
    kind: str
    provider: str

    def to_dict(self) -> dict[str, object]:
        return {"capability": self.capability, "kind": self.kind, "provider": self.provider}


def resolve_capabilities(
    needs: Iterable[str],
    *,
    native_mapping: Mapping[str, str],
    mcp_providers: Mapping[str, Iterable[str]],
) -> tuple[CapabilityResolution, ...]:
    """把一组必需能力解析为原生工具名或 MCP Server 名，没有来源的则跳过。

    ``native_mapping`` 是能力名 -> 原生工具名；``mcp_providers`` 是 MCP Server 名 ->
    它提供的能力名集合。优先使用原生工具，其次 MCP；两者都没有的能力不生成解析项，
    由调用方按需求缺失处理。
    """
    resolutions: list[CapabilityResolution] = []
    ordered_mcp = tuple(sorted(mcp_providers))
    for capability in dict.fromkeys(needs):
        native = native_mapping.get(capability)
        if native:
            resolutions.append(
                CapabilityResolution(capability, "native", native)
            )
            continue
        server = next(
            (name for name in ordered_mcp if capability in mcp_providers.get(name, ())),
            None,
        )
        if server is not None:
            resolutions.append(CapabilityResolution(capability, "mcp", server))
    return tuple(resolutions)

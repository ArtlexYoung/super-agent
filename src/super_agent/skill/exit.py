"""受限 MCP Server 出口：只暴露白名单工具，且每个工具带上声明的副作用。

``McpExit`` 把本地 Tool 集合放进一个 stdio JSON-RPC MCP Server：外部 MCP 客户端可
``initialize``、``tools/list``、``tools/call``。出口受限的保证是——只暴露显式白名单里的
工具；每个工具保留其声明的 ``effects``；未加入白名单的工具绝对不可经出口调用。它不
复制 Agent 运行时，也不开放未被选择的工具。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TextIO

from super_agent.core.model import Tool

PROTOCOL_VERSION = "2025-03-26"
MCP_SCHEMA = 1


class McpExit:
    """基于本地 Tool 集合的受限 stdio JSON-RPC MCP Server 出口。"""

    def __init__(
        self, tools: Mapping[str, Tool], *, allowed: Mapping[str, str]
    ) -> None:
        """``tools`` 是全部可用工具；``allowed`` 是工具名 -> 想要暴露的展示名。

        只有 ``allowed`` 里的工具会被暴露；未列入的即便在 ``tools`` 中存在也不会被
        调用，从而保证出口受限。展示名可不同于内部工具名。
        """
        selected = dict(tools)
        exposed: dict[str, Tool] = {}
        for internal_name, display_name in allowed.items():
            tool = selected.get(internal_name)
            if tool is None:
                raise ValueError(f"exposed tool is not registered: {internal_name}")
            if not display_name.strip():
                raise ValueError("exposed tool display name cannot be empty")
            exposed[internal_name] = tool
        self._tools = selected
        self._allowed = dict(allowed)
        self._exposed = exposed
        self._display_by_name = {display: internal for internal, display in allowed.items()}
        self._initialized = False

    def list_tools(self) -> list[dict[str, object]]:
        """只列出被白名单选中的工具，每个工具带上声明的副作用。"""
        ordered = sorted(
            self._exposed.items(),
            key=lambda pair: self._allowed[pair[0]],
        )
        return [
            {
                "name": self._allowed[internal],
                "description": tool.description,
                "inputSchema": tool.input_schema or {"type": "object", "properties": {}},
                "effects": list(tool.effects),
            }
            for internal, tool in ordered
        ]

    def call_tool(self, name: str, arguments: Mapping[str, object]) -> object:
        internal = self._display_by_name.get(name)
        if internal is None:
            raise ValueError(f"tool is not exposed by this server: {name}")
        tool = self._exposed[internal]
        return tool.handler(dict(arguments), None)

    def handle(self, line: str) -> str:
        """处理一行请求，返回一行 JSON-RPC 响应或 None（通知不响应）。"""
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            return _json_rpc_error(None, -32700, "parse error")
        if not isinstance(request, Mapping):
            return _json_rpc_error(None, -32600, "invalid request")
        method = request.get("method")
        request_id = request.get("id") if request.get("id") is not None else None
        if "id" not in request:
            return ""  # 通知不返回响应
        try:
            result = self._dispatch(str(method), request.get("params") or {})
        except Exception as error:  # noqa: BLE001 - MCP 错误应序列化给客户端
            return _json_rpc_error(request_id, -32603, str(error))
        return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, ensure_ascii=False)

    def _dispatch(self, method: str, params: Mapping[str, object]) -> object:
        if method == "initialize":
            version = params.get("protocolVersion")
            if version is not None and str(version) != PROTOCOL_VERSION:
                raise ValueError(f"unsupported protocol version: {version}")
            self._initialized = True
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "super-agent-exit", "version": "0.2.102"},
            }
        if not self._initialized:
            raise ValueError("server not initialized")
        if method == "tools/list":
            return {"tools": self.list_tools()}
        if method == "tools/call":
            name = str(params.get("name"))
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, Mapping):
                raise ValueError("tools/call arguments must be an object")
            return {"content": [{"type": "text", "text": json.dumps(self.call_tool(name, arguments), ensure_ascii=False)}]}
        raise ValueError(f"unsupported method: {method}")


def _json_rpc_error(request_id: object, code: int, message: str) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        ensure_ascii=False,
    )


def serve_exit(server: McpExit, *, input: TextIO, output: TextIO) -> None:
    """在给定 stdio 管道上循环处理请求；这是阻塞的服务器主循环。"""
    for line in input:
        response = server.handle(line)
        if response:
            output.write(response + "\n")
            output.flush()

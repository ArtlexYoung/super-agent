"""受限 MCP Server 出口：只暴露白名单，每个工具带声明副作用。"""

import json
import unittest

from super_agent.core.model import Tool
from super_agent.skill.exit import McpExit, PROTOCOL_VERSION, serve_exit


def _echo(arguments, _context):
    return {"echo": arguments.get("text", "")}


def _secret(arguments, _context):
    return {"secret": True}


class RestrictedMcpExitTests(unittest.TestCase):
    def _server(self) -> McpExit:
        tools = {
            "echo": Tool("echo", "Echo back text", _echo, {"type": "object", "properties": {"text": {"type": "string"}}}, ("read",)),
            "secret": Tool("secret", "Secret tool", _secret, {}, ("write",)),
        }
        # 只暴露 echo，展示名为 echo_display；secret 不暴露。
        return McpExit(tools, allowed={"echo": "echo_display"})

    def test_initialize_and_list_only_exposed_tools(self):
        server = self._server()
        init = json.loads(server.handle(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": PROTOCOL_VERSION}})))
        self.assertEqual(PROTOCOL_VERSION, init["result"]["protocolVersion"])
        listing = json.loads(server.handle(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})))
        names = [tool["name"] for tool in listing["result"]["tools"]]
        self.assertEqual(["echo_display"], names)
        self.assertEqual(["read"], listing["result"]["tools"][0]["effects"])

    def test_calls_only_exposed_tool(self):
        server = self._server()
        server.handle(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": PROTOCOL_VERSION}}))
        call = json.loads(server.handle(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "echo_display", "arguments": {"text": "hi"}}})))
        self.assertIn("hi", json.dumps(call["result"]))

    def test_unexposed_tool_is_rejected(self):
        server = self._server()
        server.handle(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": PROTOCOL_VERSION}}))
        call = json.loads(server.handle(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "secret", "arguments": {}}})))
        self.assertIn("error", call)
        self.assertIn("not exposed", call["error"]["message"])

    def test_notifications_return_no_response(self):
        server = self._server()
        response = server.handle(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}))
        self.assertEqual("", response)

    def test_serve_exit_loop(self):
        server = self._server()
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        ]
        class _Input:
            def __init__(self):
                self.lines = iter(lines)
            def __iter__(self):
                return self
            def __next__(self):
                try:
                    return next(self.lines)
                except StopIteration:
                    raise
        class _Output:
            def __init__(self):
                self.lines = []
            def write(self, text):
                self.lines.append(text)
            def flush(self):
                pass
        out = _Output()
        serve_exit(server, input=_Input(), output=out)
        self.assertEqual(2, len(out.lines))
        self.assertIn("tools", out.lines[1])


if __name__ == "__main__":
    unittest.main()

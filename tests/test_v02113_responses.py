"""验证 Responses（POST /responses）适配：请求形状、SSE 解析、失败与截断处理。

使用本地 HTTP 服务检查真实请求体与 SSE 处理，不访问网络模型、不读取模型凭证。
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from super_agent.core.model import Message, ModelRequest, Tool
from super_agent.core.provider import OpenAIResponsesModel, create_model


class _ResponsesHandler(BaseHTTPRequestHandler):
    """记录请求体，并按脚本返回 SSE 事件。"""

    captured: list[dict] = []
    script: list[dict] = []
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 接口
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).captured.append({"path": self.path, "body": body,
                                    "authorization": self.headers.get("Authorization")})
        payload = "".join(
            f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            for event in type(self).script
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        return


class ResponsesAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        _ResponsesHandler.captured = []
        _ResponsesHandler.script = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ResponsesHandler)
        self.addCleanup(self.server.server_close)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def model(self, **options: object) -> OpenAIResponsesModel:
        return OpenAIResponsesModel(model="glm-5.3-flash", base_url=self.base_url,
                                    api_key="local-test-key", **options)

    def request(self, **options: object) -> ModelRequest:
        return ModelRequest(
            messages=(
                Message("system", "You are Super Agent."),
                Message("user", "修复这个错误"),
                Message("assistant", "", tool_calls=(_call("call-1", "run_check", {"command": ["pytest"]}),)),
                Message("tool", "failed", tool_call_id="call-1"),
            ),
            tools=(Tool("run_check", "Run one declared command", _noop, {"type": "object", "properties": {}}, ("execute",)),),
            **options,
        )

    def test_request_uses_responses_shape_not_chat_completions(self) -> None:
        _ResponsesHandler.script = [
            {"type": "response.output_text.delta", "delta": "ok"},
            {"type": "response.completed", "response": {"status": "completed",
                                                        "usage": {"input_tokens": 12, "output_tokens": 3}}},
        ]
        events = list(self.model().stream(self.request(purpose="code")))
        self.assertEqual("/responses", _ResponsesHandler.captured[0]["path"])
        self.assertEqual("Bearer local-test-key", _ResponsesHandler.captured[0]["authorization"])
        body = _ResponsesHandler.captured[0]["body"]
        # 明确使用 Responses 字段：没有 messages/stream_options，system 提升为 instructions。
        self.assertNotIn("messages", body)
        self.assertNotIn("stream_options", body)
        self.assertEqual("You are Super Agent.", body["instructions"])
        self.assertTrue(body["stream"])
        self.assertFalse(body["store"])
        kinds = [item.get("type") for item in body["input"]]
        # 用户轮是普通 role 项，助手工具轮与工具结果使用 Responses 的显式类型项。
        self.assertEqual([None, "function_call", "function_call_output"], kinds)
        self.assertEqual("user", body["input"][0]["role"])
        # Responses 的函数工具是扁平结构。
        self.assertEqual({"type", "name", "description", "parameters"}, set(body["tools"][0]))
        self.assertEqual("function", body["tools"][0]["type"])
        text = "".join(event.text for event in events if event.event_type == "text")
        self.assertEqual("ok", text)
        usage = [event.usage for event in events if event.event_type == "usage"]
        self.assertEqual(12, usage[0]["input_tokens"])

    def test_streamed_function_call_is_reassembled_into_one_tool_call(self) -> None:
        _ResponsesHandler.script = [
            {"type": "response.output_item.added", "output_index": 0,
             "item": {"type": "function_call", "id": "fc_1", "call_id": "call_9", "name": "run_check", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '{"comm'},
            {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": 'and": ["pytest"]}'},
            {"type": "response.completed", "response": {"status": "completed"}},
        ]
        events = list(self.model().stream(self.request()))
        calls = [event.tool_call for event in events if event.event_type == "tool_call"]
        self.assertEqual(1, len(calls))
        self.assertEqual("call_9", calls[0].call_id)
        self.assertEqual({"command": ["pytest"]}, calls[0].arguments)

    def test_failure_truncation_and_bad_arguments_are_not_silent(self) -> None:
        cases = (
            ("failed", [{"type": "response.failed", "response": {"status": "failed"}}], RuntimeError),
            ("incomplete", [{"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"}}}], RuntimeError),
            ("unknown-status", [{"type": "response.completed", "response": {"status": "cancelled"}}], RuntimeError),
            ("bad-arguments", [
                {"type": "response.output_item.added", "output_index": 0,
                 "item": {"type": "function_call", "id": "fc_1", "call_id": "c", "name": "run_check", "arguments": "{not json"}},
                {"type": "response.completed", "response": {"status": "completed"}},
            ], ValueError),
        )
        for name, script, expected in cases:
            with self.subTest(name):
                _ResponsesHandler.script = script
                with self.assertRaises(expected):
                    list(self.model().stream(self.request()))

    def test_reasoning_effort_and_reserved_body_fields(self) -> None:
        _ResponsesHandler.script = [{"type": "response.completed", "response": {"status": "completed"}}]
        list(self.model(reasoning_effort="low").stream(self.request()))
        self.assertEqual({"effort": "low"}, _ResponsesHandler.captured[0]["body"]["reasoning"])
        # Responses 的运行时字段不能被 request_body 覆盖。
        for field in ("input", "instructions", "store", "model"):
            with self.subTest(field):
                with self.assertRaises(ValueError):
                    create_model("openai-responses", "m", request_options=_options({field: "x"}))


def _noop(_arguments: dict, _context: object) -> dict:
    return {}


def _call(call_id: str, name: str, arguments: dict):
    from super_agent.core.model import ToolCall

    return ToolCall(call_id, name, arguments)


def _options(body: dict):
    from super_agent.core.model import ModelRequestOptions

    return ModelRequestOptions(request_body=body)


if __name__ == "__main__":
    unittest.main()

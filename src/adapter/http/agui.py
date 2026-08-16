"""提供可选的 AG-UI SSE、静态页面和轻量管理 API 适配。"""

from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from typing import Mapping, cast
from urllib.parse import unquote, urlsplit

from adapter.http.api import RuntimeWebAPI, map_api_error
from core.event import RunEvent, RunIdentity
from super_agent import Agent, AgentContext


@dataclass(frozen=True)
class AGUIRunInput:
    """AG-UI 请求中 Runtime 真正需要的最小字段。"""

    thread_id: str
    run_id: str
    prompt: str
    skill: str | None = None

    @classmethod
    def from_dict(cls, value: object) -> AGUIRunInput:
        if not isinstance(value, dict):
            raise ValueError("AG-UI input must be a JSON object")
        thread_id = _identifier(value.get("threadId"), "threadId")
        run_id = _identifier(value.get("runId"), "runId")
        messages = value.get("messages")
        if not isinstance(messages, list):
            raise ValueError("AG-UI messages must be an array")
        prompt = _latest_user_message(messages)
        forwarded = value.get("forwardedProps", {})
        if not isinstance(forwarded, dict):
            raise ValueError("AG-UI forwardedProps must be an object")
        raw_skill = forwarded.get("skill")
        skill = None if raw_skill is None else _identifier(raw_skill, "forwardedProps.skill").lower()
        return cls(thread_id, run_id, prompt, skill)


class AGUIEventMapper:
    """把中心 Runtime 事件映射为有序 AG-UI 事件。"""

    def __init__(self, thread_id: str, run_id: str) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.sequence = 0
        self.message_started = False
        self.terminal_event_sent = False

    def map_runtime_event(self, event: RunEvent) -> list[dict[str, object]]:
        if event.run_id != self.run_id:
            raise ValueError("Runtime event run_id does not match AG-UI runId")
        self.sequence += 1
        mapped: list[dict[str, object]] = []
        if event.event_type == "run.started":
            mapped.append({"type": "RUN_STARTED", "threadId": self.thread_id, "runId": self.run_id})
        elif event.event_type == "model.text.delta":
            if not self.message_started:
                self.message_started = True
                mapped.append({"type": "TEXT_MESSAGE_START", "messageId": f"message-{self.run_id}", "role": "assistant"})
            mapped.append(
                {
                    "type": "TEXT_MESSAGE_CONTENT",
                    "messageId": f"message-{self.run_id}",
                    "delta": str(event.data.get("delta", "")),
                }
            )
        elif event.event_type == "model.tool.requested":
            mapped.extend(_tool_started(event, self.sequence))
        elif event.event_type in {"tool.completed", "tool.failed"}:
            mapped.append(_tool_result(event, self.sequence))
        elif event.event_type == "run.completed":
            if self.message_started:
                mapped.append({"type": "TEXT_MESSAGE_END", "messageId": f"message-{self.run_id}"})
            mapped.append(
                {
                    "type": "RUN_FINISHED",
                    "threadId": self.thread_id,
                    "runId": self.run_id,
                    "outcome": {"type": "success"},
                    "result": _finish_result(event.data),
                }
            )
            self.terminal_event_sent = True
        elif event.event_type == "run.failed":
            mapped.append(
                {
                    "type": "RUN_ERROR",
                    "message": str(event.data.get("message", "Agent run failed")),
                    "code": str(event.data.get("error_type", "RuntimeError")),
                }
            )
            self.terminal_event_sent = True
        custom = {
            "type": "CUSTOM",
            "name": event.event_type,
            "value": {
                "runId": self.run_id,
                "sequence": self.sequence,
                "createdAt": event.created_at,
                "data": _redact_event_data(event.data),
            },
        }
        return [*mapped, custom]

    def create_error_event(self, error: Exception) -> dict[str, str]:
        self.terminal_event_sent = True
        return {"type": "RUN_ERROR", "message": str(error) or type(error).__name__, "code": type(error).__name__}


def encode_sse_event(event: Mapping[str, object]) -> bytes:
    payload = json.dumps(dict(event), ensure_ascii=False, separators=(",", ":"))
    return f"data: {payload}\n\n".encode("utf-8")


class AGUIHTTPServer(ThreadingHTTPServer):
    """标准库 HTTP 服务，不改变 Core 的执行语义。"""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        agent: Agent,
        *,
        user_id: str = "local",
        allowed_origins: tuple[str, ...] = ("http://127.0.0.1:5173", "http://localhost:5173"),
        static_root: str | Path | None = None,
    ) -> None:
        self.agent = agent
        self.user_id = _identifier(user_id, "user_id")
        self.allowed_origins = frozenset(allowed_origins)
        self.api = RuntimeWebAPI(agent, self.user_id)
        self.api_lock = RLock()
        self.static_root = None if static_root is None else Path(static_root).expanduser().resolve()
        super().__init__(address, AGUIRequestHandler)


class AGUIRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SuperAgent"
    sys_version = ""

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok", "protocol": "AG-UI"})
        elif path.startswith("/api/"):
            self._handle_api("GET", path)
        else:
            self._serve_static(path, include_body=True)

    def do_HEAD(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok", "protocol": "AG-UI"}, include_body=False)
        else:
            self._serve_static(path, include_body=False)

    def do_OPTIONS(self) -> None:
        if not self._allowed_origin():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "origin is not allowed"})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self._security()
        self.end_headers()

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            self._handle_api("POST", path)
            return
        if path != "/ag-ui":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
            return
        if not self._allowed_origin():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "origin is not allowed"})
            return
        try:
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Content-Type must be application/json")
            request = AGUIRunInput.from_dict(self._read_json())
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        self._run(request)

    def do_PUT(self) -> None:
        self._handle_api("PUT", urlsplit(self.path).path)

    def do_PATCH(self) -> None:
        self._handle_api("PATCH", urlsplit(self.path).path)

    def do_DELETE(self) -> None:
        self._handle_api("DELETE", urlsplit(self.path).path)

    def _handle_api(self, method: str, path: str) -> None:
        if not self._allowed_origin():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "origin is not allowed"})
            return
        try:
            body = self._read_optional_json(method)
            with self._server.api_lock:
                status, value = self._server.api.handle(method, path, body)
        except (KeyError, TypeError, ValueError, RuntimeError, OSError) as error:
            status, value = map_api_error(error)
        self._send_json(status, value)

    def _run(self, request: AGUIRunInput) -> None:
        self.send_response(HTTPStatus.OK)
        self._cors()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self._security()
        self.end_headers()
        self.close_connection = True
        mapper = AGUIEventMapper(request.thread_id, request.run_id)

        def forward(event: RunEvent) -> None:
            for value in mapper.map_runtime_event(event):
                self._write_sse(value)

        try:
            context = AgentContext(
                user_id=self._server.user_id,
                conversation_id=request.thread_id,
                skill=request.skill,
                identity=RunIdentity(
                    user_id=self._server.user_id,
                    agent_name=self._server.agent.name,
                    run_id=request.run_id,
                    conversation_id=request.thread_id,
                ),
                listeners=(forward,),
                save_conversation=True,
            )
            stream = self._server.agent.stream(request.prompt, context=context)
            for _event in stream:
                pass
        except Exception as error:
            if not mapper.terminal_event_sent:
                self._write_sse(mapper.create_error_event(error))

    def _read_json(self) -> object:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        length = int(raw_length)
        if length < 0 or length > 1_048_576:
            raise ValueError("request body must be at most 1048576 bytes")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _read_optional_json(self, method: str) -> object | None:
        if method not in {"POST", "PUT", "PATCH"}:
            return None
        if self.headers.get("Content-Length") in {None, "0"}:
            return None
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json")
        return self._read_json()

    def _write_sse(self, event: Mapping[str, object]) -> None:
        try:
            self.wfile.write(encode_sse_event(event))
            self.wfile.flush()
        except OSError:
            self.close_connection = True

    def _serve_static(self, path: str, *, include_body: bool) -> None:
        root = self._server.static_root
        if root is None or not root.is_dir():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"}, include_body=include_body)
            return
        relative = unquote(path).lstrip("/") or "index.html"
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"}, include_body=include_body)
            return
        if not candidate.is_file() and not Path(relative).suffix:
            candidate = root / "index.html"
        if not candidate.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"}, include_body=include_body)
            return
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self._send_bytes(HTTPStatus.OK, body, content_type, include_body=include_body, cache_control="no-cache" if candidate.name == "index.html" else "public, max-age=31536000, immutable")

    def _send_json(self, status: HTTPStatus, value: object, *, include_body: bool = True) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8", include_body=include_body, cors=True)

    def _send_bytes(self, status: HTTPStatus, body: bytes, content_type: str, *, include_body: bool, cors: bool = False, cache_control: str | None = None) -> None:
        self.send_response(status)
        if cors:
            self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        self._security()
        self.end_headers()
        if include_body:
            self.wfile.write(body)
        self.close_connection = True

    def _allowed_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None or origin in self._server.allowed_origins:
            return True
        host = self.headers.get("Host", "").strip().lower()
        return bool(host) and origin.strip().lower() == f"http://{host}"

    def _cors(self) -> None:
        origin = self.headers.get("Origin")
        if origin in self._server.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _security(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'")

    @property
    def _server(self) -> AGUIHTTPServer:
        return cast(AGUIHTTPServer, self.server)


def create_ag_ui_server(
    agent: Agent,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    user_id: str = "local",
    allowed_origins: tuple[str, ...] = ("http://127.0.0.1:5173", "http://localhost:5173"),
    static_root: str | Path | None = Path(__file__).resolve().parents[1] / "static",
) -> AGUIHTTPServer:
    if not host.strip():
        raise ValueError("AG-UI server host cannot be empty")
    if not 0 <= port <= 65_535:
        raise ValueError("AG-UI server port must be between 0 and 65535")
    return AGUIHTTPServer((host.strip(), port), agent, user_id=user_id, allowed_origins=allowed_origins, static_root=static_root)


def _tool_started(event: RunEvent, sequence: int) -> list[dict[str, object]]:
    call_id = str(event.data.get("call_id") or f"tool-{sequence}")
    return [
        {"type": "TOOL_CALL_START", "toolCallId": call_id, "toolCallName": str(event.data.get("name", "runtime_tool"))},
        {"type": "TOOL_CALL_ARGS", "toolCallId": call_id, "delta": json.dumps(event.data.get("arguments", {}), ensure_ascii=False, separators=(",", ":"))},
        {"type": "TOOL_CALL_END", "toolCallId": call_id},
    ]


def _tool_result(event: RunEvent, sequence: int) -> dict[str, object]:
    call_id = str(event.data.get("call_id") or f"tool-{sequence}")
    result = event.data.get("result", {}) if event.event_type == "tool.completed" else {"error": event.data.get("message", "Tool call failed"), "error_type": event.data.get("error_type", "RuntimeError")}
    return {"type": "TOOL_CALL_RESULT", "messageId": f"{call_id}-result", "toolCallId": call_id, "content": json.dumps(result, ensure_ascii=False, separators=(",", ":")), "role": "tool"}


def _finish_result(data: Mapping[str, object]) -> dict[str, object]:
    return {key: data.get(key) for key in ("stop_reason", "skills", "workflow", "usage") if key in data}


def _redact_event_data(value: object, key: str = "") -> object:
    normalized = key.lower().replace("-", "_")
    if normalized in {"token", "access_token", "refresh_token", "authorization"} or any(
        item in normalized for item in ("secret", "password", "api_key")
    ):
        return "[redacted]"
    if normalized in {"prompt", "text", "content", "arguments", "result", "message", "body", "reason", "error"}:
        raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
        return {"redacted": True, "sha256": hashlib.sha256(raw.encode()).hexdigest(), "characters": len(raw)}
    if isinstance(value, Mapping):
        return {str(name): _redact_event_data(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact_event_data(item) for item in value]
    return value


def _latest_user_message(messages: list[object]) -> str:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts = [str(item.get("text", "")).strip() for item in content if isinstance(item, dict) and item.get("type") == "text"]
                selected = "\n".join(item for item in parts if item)
                if selected:
                    return selected
    raise ValueError("AG-UI messages must contain a non-empty user message")


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200 or any(ord(item) < 32 for item in value):
        raise ValueError(f"AG-UI {name} must be a non-empty printable string")
    return value.strip()

"""基于标准库的 HTTP、SSE、管理 API 与静态 Web 服务器。"""

from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from typing import Any, cast
from urllib.parse import unquote, urlsplit

from super_agent import Agent
from core.models import AgentRunOptions, LOCAL_USER_ID, RunEvent, read_text, validate_user_id
from adapter.http.web import WebAPI
from core.checks import ActionBlockedError


@dataclass(frozen=True)
class AGUIRunInput:
    thread_id: str
    run_id: str
    prompt: str
    skill: str | None = None

    @classmethod
    def from_dict(cls, value: object) -> "AGUIRunInput":
        if not isinstance(value, dict):
            raise ValueError("AG-UI input must be a JSON object")
        thread_id = _read_identifier(value, "threadId")
        run_id = _read_identifier(value, "runId")
        messages = value.get("messages")
        if not isinstance(messages, list):
            raise ValueError("AG-UI messages must be an array")
        prompt = _read_latest_user_message(messages)
        forwarded = value.get("forwardedProps", {})
        if not isinstance(forwarded, dict):
            raise ValueError("AG-UI forwardedProps must be an object")
        skill = _read_optional_skill(forwarded.get("skill"))
        return cls(thread_id, run_id, prompt, skill)


class AGUIEventMapper:
    """将 Runtime 标准事件流映射为有序 AG-UI 事件。"""

    def __init__(self, thread_id: str, run_id: str) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.terminal_event_sent = False

    def map_runtime_event(self, event: RunEvent) -> list[dict[str, Any]]:
        if event.run_id != self.run_id:
            raise ValueError("Runtime event run_id does not match AG-UI runId")
        core_events = self._map_core_event(event)
        custom_event = _custom_runtime_event(event)
        if event.event_type in {"run.completed", "run.failed"}:
            return [custom_event, *core_events]
        return [*core_events, custom_event]

    def create_error_event(self, error: Exception) -> dict[str, str]:
        self.terminal_event_sent = True
        return {"type": "RUN_ERROR", "message": str(error) or type(error).__name__, "code": type(error).__name__}

    def _map_core_event(self, event: RunEvent) -> list[dict[str, Any]]:
        event_type = event.event_type
        if event_type == "run.started":
            return [self._run_started(event)]
        if event_type == "task.started":
            return [{"type": "STEP_STARTED", "stepName": "task"}]
        if event_type == "tool.requested":
            return _tool_call_started_events(event)
        if event_type in {"tool.completed", "tool.failed"}:
            return [_tool_call_result_event(event)]
        if event_type == "task.completed":
            return [*_assistant_message_events(event), {"type": "STEP_FINISHED", "stepName": "task"}]
        if event_type == "run.completed":
            self.terminal_event_sent = True
            return [{"type": "RUN_FINISHED", "threadId": self.thread_id, "runId": self.run_id, "result": event.data, "outcome": {"type": "success"}}]
        if event_type == "run.failed":
            self.terminal_event_sent = True
            return [{"type": "RUN_ERROR", "message": str(event.data.get("message", "Agent run failed")), "code": str(event.data.get("error_type", "RuntimeError"))}]
        return []

    def _run_started(self, event: RunEvent) -> dict[str, Any]:
        mapped: dict[str, Any] = {"type": "RUN_STARTED", "threadId": self.thread_id, "runId": self.run_id}
        if event.parent_run_id:
            mapped["parentRunId"] = event.parent_run_id
        return mapped


def encode_sse_event(event: dict[str, object]) -> bytes:
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"data: {payload}\n\n".encode("utf-8")


def _read_identifier(data: dict[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str):
        raise ValueError(f"AG-UI {name} must be a non-empty string")
    clean = read_text(value, f"AG-UI {name}")
    if len(clean) > 200 or any(ord(character) < 32 for character in clean):
        raise ValueError(f"AG-UI {name} must be at most 200 printable characters")
    return clean


def _read_optional_skill(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("AG-UI forwardedProps.skill must be a non-empty string")
    clean = read_text(value, "AG-UI forwardedProps.skill").lower()
    if len(clean) > 129 or any(ord(character) < 32 for character in clean):
        raise ValueError("AG-UI forwardedProps.skill is invalid")
    return clean


def _read_latest_user_message(messages: list[object]) -> str:
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = _read_user_content(message.get("content"))
        if content:
            return content
    raise ValueError("AG-UI messages must contain a non-empty user message")


def _read_user_content(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    parts = [str(item.get("text", "")).strip() for item in value if isinstance(item, dict) and item.get("type") == "text"]
    return "\n".join(part for part in parts if part)


def _custom_runtime_event(event: RunEvent) -> dict[str, object]:
    return {"type": "CUSTOM", "name": event.event_type, "value": {"runId": event.run_id, "sequence": event.sequence, "createdAt": event.created_at, "agentName": event.agent_name, "parentRunId": event.parent_run_id, "data": event.data}}


def _tool_call_started_events(event: RunEvent) -> list[dict[str, object]]:
    call_id = str(event.data.get("call_id") or f"tool-{event.sequence}")
    arguments = event.data.get("arguments", {})
    return [{"type": "TOOL_CALL_START", "toolCallId": call_id, "toolCallName": str(event.data.get("name", "runtime_tool"))}, {"type": "TOOL_CALL_ARGS", "toolCallId": call_id, "delta": json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))}, {"type": "TOOL_CALL_END", "toolCallId": call_id}]


def _tool_call_result_event(event: RunEvent) -> dict[str, object]:
    call_id = str(event.data.get("call_id") or f"tool-{event.sequence}")
    content = event.data.get("result") if event.event_type == "tool.completed" else {"error": str(event.data.get("message", "Tool call failed")), "errorType": str(event.data.get("error_type", "RuntimeError"))}
    return {"type": "TOOL_CALL_RESULT", "messageId": f"{call_id}-result", "toolCallId": call_id, "content": json.dumps(content, ensure_ascii=False, separators=(",", ":")), "role": "tool"}


def _assistant_message_events(event: RunEvent) -> list[dict[str, str]]:
    message_id = f"message-{event.run_id}"
    return [{"type": "TEXT_MESSAGE_START", "messageId": message_id, "role": "assistant"}, {"type": "TEXT_MESSAGE_CONTENT", "messageId": message_id, "delta": str(event.data.get("text", ""))}, {"type": "TEXT_MESSAGE_END", "messageId": message_id}]


MAX_REQUEST_BYTES = 1_048_576
DEFAULT_ALLOWED_ORIGINS = ("http://127.0.0.1:5173", "http://localhost:5173")
DEFAULT_STATIC_ROOT = Path(__file__).resolve().parents[1] / "static"


class AGUIHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], agent: Agent, *, user_id: str = LOCAL_USER_ID, allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS, static_root: str | Path | None = DEFAULT_STATIC_ROOT) -> None:
        self.agent = agent
        self.user_id = validate_user_id(user_id)
        self.allowed_origins = frozenset(allowed_origins)
        self.web_api = WebAPI(agent, user_id)
        self.web_api_lock = RLock()
        self.static_root = None if static_root is None else Path(static_root).expanduser().resolve()
        super().__init__(address, AGUIRequestHandler)


class AGUIRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SuperAgent"
    sys_version = ""

    def do_GET(self) -> None:
        self._handle_read(include_body=True)

    def do_HEAD(self) -> None:
        self._handle_read(include_body=False)

    def _handle_read(self, *, include_body: bool) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok", "protocol": "AG-UI"}, include_body=include_body)
            return
        if include_body and path.startswith("/api/"):
            self._handle_web_api("GET", path)
            return
        self._serve_static_file(path, include_body=include_body)

    def do_OPTIONS(self) -> None:
        if not self._origin_is_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "origin is not allowed"})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self._send_security_headers()
        self.end_headers()

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            self._handle_web_api("POST", path)
            return
        if path != "/ag-ui":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
            return
        if not self._origin_is_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "origin is not allowed"})
            return
        if self.headers.get_content_type() != "application/json":
            self._send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Content-Type must be application/json"})
            return
        try:
            request = AGUIRunInput.from_dict(self._read_json_body())
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        self._run_agent(request)

    def do_PUT(self) -> None:
        self._handle_web_api(self.command, urlsplit(self.path).path)

    do_PATCH = do_PUT
    do_DELETE = do_PUT

    def _handle_web_api(self, method: str, path: str) -> None:
        if not path.startswith("/api/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
            return
        if not self._origin_is_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "origin is not allowed"})
            return
        try:
            body = self._read_optional_json_body(method)
            with self._server.web_api_lock:
                response = self._server.web_api.handle(method, path, body)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError, KeyError, FileExistsError, ActionBlockedError, OSError) as error:
            status, body = _web_error_response(error)
            self._send_json(status, body)
            return
        self._send_json(response.status, response.body)

    def _run_agent(self, request: AGUIRunInput) -> None:
        self.send_response(HTTPStatus.OK)
        self._send_cors_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self._send_security_headers()
        self.end_headers()
        self.close_connection = True
        mapper = AGUIEventMapper(request.thread_id, request.run_id)

        def send_runtime_event(event: RunEvent) -> None:
            for mapped in mapper.map_runtime_event(event):
                self._write_sse_event(mapped)

        try:
            self._server.agent.for_user(self._server.user_id).run(request.prompt, conversation_id=request.thread_id, skill=request.skill, run_options=AgentRunOptions(run_id=request.run_id, event_listener=send_runtime_event))
        except Exception as error:
            if not mapper.terminal_event_sent:
                self._write_sse_event(mapper.create_error_event(error))

    def _read_json_body(self) -> object:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ValueError("Content-Length must be an integer") from error
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError(f"request body must be at most {MAX_REQUEST_BYTES} bytes")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _read_optional_json_body(self, method: str) -> object | None:
        if method not in {"POST", "PUT", "PATCH"}:
            return None
        raw_length = self.headers.get("Content-Length")
        if raw_length in {None, "0"}:
            return None
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json")
        return self._read_json_body()

    def _write_sse_event(self, event: dict[str, object]) -> None:
        try:
            self.wfile.write(encode_sse_event(event))
            self.wfile.flush()
        except OSError:
            self.close_connection = True

    def _send_json(self, status: HTTPStatus, value: object, *, include_body: bool = True) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8", include_body=include_body, cors=True, close=True)

    def _send_bytes(self, status: HTTPStatus, body: bytes, content_type: str, *, include_body: bool, cors: bool = False, close: bool = False, cache_control: str | None = None) -> None:
        self.send_response(status)
        if cors:
            self._send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
        if cache_control is not None:
            self.send_header("Cache-Control", cache_control)
        self._send_security_headers()
        self.end_headers()
        if include_body:
            self.wfile.write(body)
        if close:
            self.close_connection = True

    def _serve_static_file(self, path: str, *, include_body: bool) -> None:
        root = self._server.static_root
        if root is None or not root.is_dir():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
            return
        try:
            relative = unquote(path).lstrip("/") or "index.html"
            candidate = (root / relative).resolve()
        except (OSError, ValueError):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
            return
        if candidate != root and root not in candidate.parents:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
            return
        if not candidate.is_file() and not Path(relative).suffix:
            candidate = root / "index.html"
        if not candidate.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
            return
        try:
            body = candidate.read_bytes()
        except OSError:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "unable to read web asset"})
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        cache_control = "no-cache" if candidate.name == "index.html" else "public, max-age=31536000, immutable"
        self._send_bytes(HTTPStatus.OK, body, content_type, include_body=include_body, cache_control=cache_control)

    def _send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'")

    def _origin_is_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None or origin in self._server.allowed_origins:
            return True
        host = self.headers.get("Host", "").strip().lower()
        return bool(host) and origin.strip().lower() == f"http://{host}"

    def _send_cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin in self._server.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    @property
    def _server(self) -> AGUIHTTPServer:
        return cast(AGUIHTTPServer, self.server)


def create_ag_ui_server(agent: Agent, host: str = "127.0.0.1", port: int = 8765, *, user_id: str = LOCAL_USER_ID, allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS, static_root: str | Path | None = DEFAULT_STATIC_ROOT) -> AGUIHTTPServer:
    clean_host = host.strip()
    if not clean_host:
        raise ValueError("AG-UI server host cannot be empty")
    if port < 0 or port > 65_535:
        raise ValueError("AG-UI server port must be between 0 and 65535")
    if not user_id.strip():
        raise ValueError("AG-UI server user_id cannot be empty")
    return AGUIHTTPServer((clean_host, port), agent, user_id=user_id, allowed_origins=allowed_origins, static_root=static_root)


def _web_error_response(error: Exception) -> tuple[HTTPStatus, dict[str, object]]:
    if isinstance(error, ActionBlockedError):
        return HTTPStatus.FORBIDDEN, {"error": str(error), "action_id": error.request.action_id, "decision": error.decision.decision.value}
    if isinstance(error, KeyError):
        return HTTPStatus.NOT_FOUND, {"error": str(error.args[0]) if error.args else "resource not found"}
    if isinstance(error, FileExistsError):
        return HTTPStatus.CONFLICT, {"error": str(error)}
    if isinstance(error, OSError):
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "unable to persist the requested change"}
    return HTTPStatus.BAD_REQUEST, {"error": str(error)}

"""Standard-library HTTP and SSE server for the AG-UI protocol."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib.parse import urlsplit

from agents.agent import Agent, AgentRunOptions
from ag_ui_bridge.protocol import AGUIEventMapper, AGUIRunInput, encode_sse_event
from runtime.identity import LOCAL_USER_ID
from runtime.models import RunEvent


MAX_REQUEST_BYTES = 1_048_576
DEFAULT_ALLOWED_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)


class AGUIHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        agent: Agent,
        *,
        user_id: str = LOCAL_USER_ID,
        allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS,
    ) -> None:
        self.agent = agent
        self.user_id = user_id
        self.allowed_origins = frozenset(allowed_origins)
        super().__init__(address, AGUIRequestHandler)


class AGUIRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if urlsplit(self.path).path != "/health":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
            return
        self._send_json(HTTPStatus.OK, {"status": "ok", "protocol": "AG-UI"})

    def do_OPTIONS(self) -> None:
        if not self._origin_is_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "origin is not allowed"})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/ag-ui":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
            return
        if not self._origin_is_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "origin is not allowed"})
            return
        if self.headers.get_content_type() != "application/json":
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": "Content-Type must be application/json"},
            )
            return
        try:
            request = AGUIRunInput.from_dict(self._read_json_body())
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        self._run_agent(request)

    def _run_agent(self, request: AGUIRunInput) -> None:
        self.send_response(HTTPStatus.OK)
        self._send_cors_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        mapper = AGUIEventMapper(request.thread_id, request.run_id)

        def send_runtime_event(event: RunEvent) -> None:
            for mapped in mapper.map_runtime_event(event):
                self._write_sse_event(mapped)

        try:
            self._server.agent.run(
                request.prompt,
                user_id=self._server.user_id,
                conversation_id=request.thread_id,
                run_options=AgentRunOptions(
                    run_id=request.run_id,
                    event_listener=send_runtime_event,
                ),
            )
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

    def _write_sse_event(self, event: dict[str, object]) -> None:
        try:
            self.wfile.write(encode_sse_event(event))
            self.wfile.flush()
        except OSError:
            self.close_connection = True

    def _send_json(self, status: HTTPStatus, value: dict[str, object]) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _origin_is_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin in self._server.allowed_origins

    def _send_cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin in self._server.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    @property
    def _server(self) -> AGUIHTTPServer:
        return cast(AGUIHTTPServer, self.server)


def create_ag_ui_server(
    agent: Agent,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    user_id: str = LOCAL_USER_ID,
    allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS,
) -> AGUIHTTPServer:
    clean_host = host.strip()
    if not clean_host:
        raise ValueError("AG-UI server host cannot be empty")
    if port < 0 or port > 65_535:
        raise ValueError("AG-UI server port must be between 0 and 65535")
    if not user_id.strip():
        raise ValueError("AG-UI server user_id cannot be empty")
    return AGUIHTTPServer(
        (clean_host, port),
        agent,
        user_id=user_id,
        allowed_origins=allowed_origins,
    )

"""Standard-library HTTP, SSE, management API, and static web server."""

from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from typing import cast
from urllib.parse import unquote, urlsplit

from super_agent import Agent
from core.models import AgentRunOptions
from adapter.ag_ui_adapter.protocol import AGUIEventMapper, AGUIRunInput, encode_sse_event
from adapter.ag_ui_adapter.web_api import WebAPI, WebAPIResponse
from core.models import LOCAL_USER_ID, validate_user_id
from core.state.models import RunEvent
from core.checks import ActionBlockedError


MAX_REQUEST_BYTES = 1_048_576
DEFAULT_ALLOWED_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)
DEFAULT_STATIC_ROOT = Path(__file__).with_name("static")


class AGUIHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        agent: Agent,
        *,
        user_id: str = LOCAL_USER_ID,
        allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS,
        static_root: str | Path | None = DEFAULT_STATIC_ROOT,
    ) -> None:
        self.agent = agent
        self.user_id = validate_user_id(user_id)
        self.allowed_origins = frozenset(allowed_origins)
        self.web_api = WebAPI(agent, user_id)
        self.web_api_lock = RLock()
        self.static_root = (
            None if static_root is None else Path(static_root).expanduser().resolve()
        )
        super().__init__(address, AGUIRequestHandler)


class AGUIRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SuperAgent"
    sys_version = ""

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok", "protocol": "AG-UI"})
            return
        if path.startswith("/api/"):
            self._handle_web_api("GET", path)
            return
        self._serve_static_file(path, include_body=True)

    def do_HEAD(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {"status": "ok", "protocol": "AG-UI"},
                include_body=False,
            )
            return
        self._serve_static_file(path, include_body=False)

    def do_OPTIONS(self) -> None:
        if not self._origin_is_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "origin is not allowed"})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS",
        )
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

    def do_PUT(self) -> None:
        self._handle_web_api("PUT", urlsplit(self.path).path)

    def do_PATCH(self) -> None:
        self._handle_web_api("PATCH", urlsplit(self.path).path)

    def do_DELETE(self) -> None:
        self._handle_web_api("DELETE", urlsplit(self.path).path)

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
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        except KeyError as error:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": _error_message(error)})
            return
        except FileExistsError as error:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(error)})
            return
        except ActionBlockedError as error:
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {
                    "error": str(error),
                    "action_id": error.request.action_id,
                    "decision": error.decision.decision.value,
                },
            )
            return
        except OSError:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "unable to persist the requested change"},
            )
            return
        self._send_web_api_response(response)

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
            self._server.agent.for_user(self._server.user_id).run(
                request.prompt,
                conversation_id=request.thread_id,
                scene=request.scene,
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

    def _send_web_api_response(self, response: WebAPIResponse) -> None:
        self._send_json(response.status, response.body)

    def _send_json(
        self,
        status: HTTPStatus,
        value: object,
        *,
        include_body: bool = True,
    ) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self._send_security_headers()
        self.end_headers()
        if include_body:
            self.wfile.write(body)
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
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "unable to read web asset"},
            )
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header(
            "Cache-Control",
            (
                "no-cache"
                if candidate.name == "index.html"
                else "public, max-age=31536000, immutable"
            ),
        )
        self._send_security_headers()
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def _send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )

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


def create_ag_ui_server(
    agent: Agent,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    user_id: str = LOCAL_USER_ID,
    allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS,
    static_root: str | Path | None = DEFAULT_STATIC_ROOT,
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
        static_root=static_root,
    )


def _error_message(error: KeyError) -> str:
    return str(error.args[0]) if error.args else "resource not found"

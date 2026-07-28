import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from core.agent import Agent
from adapter.ag_ui_adapter.server import create_ag_ui_server
from core.provider.chat import MockProvider
from core.config import AgentConfig
from support import write_workflow_skill


class AGUIServerTests(unittest.TestCase):
    def test_http_server_streams_agent_run_and_persists_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = _make_agent(Path(tmp), MockProvider("streamed answer"))
            server, thread = _start_server(agent)
            try:
                status, headers, body = _post_run(server, _run_input())
            finally:
                _stop_server(server, thread)

            events = _read_sse_events(body)
            self.assertEqual(200, status)
            self.assertEqual("text/event-stream; charset=utf-8", headers["Content-Type"])
            self.assertEqual("RUN_STARTED", events[0]["type"])
            self.assertEqual("run-browser-1", events[0]["runId"])
            self.assertEqual("RUN_FINISHED", events[-1]["type"])
            self.assertIn(
                "streamed answer",
                [event.get("delta") for event in events],
            )
            stored = agent.for_user("browser-user").conversations.read("thread-browser-1")
            self.assertEqual(["user", "assistant"], [item.role for item in stored.messages])
            self.assertEqual("run-browser-1", stored.messages[-1].run_id)
            snapshot = agent.runtime.create_store("browser-user").read_run("run-browser-1")
            self.assertEqual("thread-browser-1", snapshot.conversation_id)

    def test_http_server_rejects_unlisted_browser_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = _make_agent(Path(tmp), MockProvider())
            server, thread = _start_server(agent)
            try:
                status, _, body = _post_run(
                    server,
                    _run_input(),
                    origin="https://untrusted.example",
                )
            finally:
                _stop_server(server, thread)

            self.assertEqual(403, status)
            self.assertEqual("origin is not allowed", json.loads(body)["error"])
            self.assertEqual([], agent.runtime.create_store("browser-user").list_runs())

    def test_health_route_is_dependency_free_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server, thread = _start_server(_make_agent(Path(tmp), MockProvider()))
            try:
                connection = http.client.HTTPConnection(*server.server_address, timeout=5)
                connection.request("GET", "/health")
                response = connection.getresponse()
                body = json.loads(response.read())
                connection.close()
            finally:
                _stop_server(server, thread)

            self.assertEqual(200, response.status)
            self.assertEqual({"status": "ok", "protocol": "AG-UI"}, body)

    def test_server_serves_spa_and_management_api_from_one_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            static_root = root / "static"
            static_root.mkdir()
            (static_root / "index.html").write_text(
                "<!doctype html><title>Super Agent</title><main>app</main>",
                encoding="utf-8",
            )
            agent = _make_agent(root, MockProvider())
            server, thread = _start_server(agent, static_root=static_root)
            try:
                root_status, root_headers, root_body = _request(server, "GET", "/")
                route_status, _, route_body = _request(
                    server,
                    "GET",
                    "/conversations/current",
                )
                api_status, _, api_body = _request(server, "GET", "/api/bootstrap")
                created_status, _, created_body = _json_request(
                    server,
                    "POST",
                    "/api/conversations",
                    {"title": "Browser conversation"},
                    origin=f"http://{server.server_address[0]}:{server.server_address[1]}",
                )
            finally:
                _stop_server(server, thread)

            self.assertEqual(200, root_status)
            self.assertEqual(root_body, route_body)
            self.assertEqual("DENY", root_headers["X-Frame-Options"])
            self.assertEqual(200, route_status)
            self.assertEqual("ag-ui-agent", json.loads(api_body)["agent"]["name"])
            self.assertEqual(200, api_status)
            self.assertEqual(201, created_status)
            self.assertEqual("Browser conversation", json.loads(created_body)["title"])

    def test_static_server_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            static_root = root / "static"
            static_root.mkdir()
            (static_root / "index.html").write_text("app", encoding="utf-8")
            (root / "secret.txt").write_text("secret", encoding="utf-8")
            server, thread = _start_server(
                _make_agent(root, MockProvider()),
                static_root=static_root,
            )
            try:
                status, _, body = _request(server, "GET", "/%2e%2e/secret.txt")
            finally:
                _stop_server(server, thread)

            self.assertEqual(404, status)
            self.assertNotIn(b"secret", body)


def _start_server(agent: Agent, *, static_root=None):
    server = create_ag_ui_server(
        agent,
        port=0,
        user_id="browser-user",
        static_root=static_root,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop_server(server, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _post_run(
    server,
    payload: dict[str, object],
    *,
    origin: str = "http://127.0.0.1:5173",
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection(*server.server_address, timeout=10)
    body = json.dumps(payload).encode("utf-8")
    connection.request(
        "POST",
        "/ag-ui",
        body=body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "Origin": origin,
        },
    )
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def _request(server, method: str, path: str) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    connection.request(method, path)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def _json_request(
    server,
    method: str,
    path: str,
    value: object,
    *,
    origin: str,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    body = json.dumps(value).encode("utf-8")
    connection.request(
        method,
        path,
        body=body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "Origin": origin,
        },
    )
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def _read_sse_events(body: bytes) -> list[dict[str, object]]:
    return [
        json.loads(block.removeprefix("data: "))
        for block in body.decode("utf-8").strip().split("\n\n")
        if block.startswith("data: ")
    ]


def _run_input() -> dict[str, object]:
    return {
        "threadId": "thread-browser-1",
        "runId": "run-browser-1",
        "state": {},
        "messages": [{"id": "message-1", "role": "user", "content": "hello"}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


def _make_agent(root: Path, provider: MockProvider) -> Agent:
    write_workflow_skill(root)
    config_path = root / "agent.toml"
    config_path.write_text(
        """
[agent]
name = "ag-ui-agent"
system = "Answer clearly."
skills = ["workflow:direct", "memory:default"]

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return Agent(AgentConfig.load_from_file(config_path), provider=provider)

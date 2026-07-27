import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from agents.agent import Agent
from ag_ui_bridge.server import create_ag_ui_server
from provider.chat import MockProvider
from runtime.config import AgentConfig
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
            stored = agent.read_conversation("thread-browser-1", user_id="browser-user")
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


def _start_server(agent: Agent):
    server = create_ag_ui_server(agent, port=0, user_id="browser-user")
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
workflow = "direct"
memory = "default"
skills = []

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return Agent(AgentConfig.load_from_file(config_path), provider=provider)

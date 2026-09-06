"""可靠 MCP 客户端：通知穿插、初始化错误与响应 ID 匹配。"""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

tests_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(tests_dir))

from super_agent.adapter import process as process_module
from super_agent.adapter.process import StdioMcpServer  # noqa: E402


class _FakeProcess:
    """模拟 stdio MCP 进程的 stdin/stdout 管道。"""

    def __init__(self, lines):
        self.reader = _FakeReader(lines)
        self.stdout = self.reader
        self.stdin = _FakeWriter()
        self.writer = self.stdin

    def poll(self):
        return None

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


class _FakeReader:
    def __init__(self, lines):
        self.lines = list(lines)
        self.index = 0

    def readline(self):
        if self.index >= len(self.lines):
            return ""
        line = self.lines[self.index]
        self.index += 1
        return line + "\n"

    def fileno(self):
        return 0


class _FakeWriter:
    def __init__(self):
        self.sent = []

    def write(self, text):
        self.sent.append(text)

    def flush(self):
        pass


def _ready_select(readers, writers, others, timeout):
    """模拟管道总是立即可读，交给 readline 消费。"""
    return list(readers), [], []


class ReliableMcpClientTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(process_module.select, "select", side_effect=_ready_select)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_interleaved_notification_is_skipped(self):
        server = StdioMcpServer(["a", "b"])
        # 初始化成功后在正常响应前插入一条无 ID 通知。
        init_ok = {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "s"}}}
        notify = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
        response = {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}
        process = _FakeProcess([json.dumps(init_ok), json.dumps(notify), json.dumps(response)])
        with patch("subprocess.Popen", return_value=process):
            with patch.multiple(server, _process=process, _request_id=0):
                server._initialize(process)
                tools = server._request("tools/list", {})
        self.assertEqual([], tools["tools"])

    def test_initialize_error_prevents_initialized_notification(self):
        server = StdioMcpServer(["a", "b"])
        error = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32001, "message": "rejected"}}
        process = _FakeProcess([json.dumps(error)])
        with patch("subprocess.Popen", return_value=process):
            with patch.multiple(server, _process=process, _request_id=0):
                with self.assertRaisesRegex(RuntimeError, "initialize error"):
                    server._initialize(process)
        # 初始化失败时不应发送 notifications/initialized。
        joined = "\n".join(process.stdin.sent)
        self.assertNotIn("notifications/initialized", joined)

    def test_response_id_mismatch_raises(self):
        server = StdioMcpServer(["a", "b"])
        init_ok = {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "s"}}}
        mismatch = {"jsonrpc": "2.0", "id": 999, "result": {"tools": []}}
        process = _FakeProcess([json.dumps(init_ok), json.dumps(mismatch)])
        with patch("subprocess.Popen", return_value=process):
            with patch.multiple(server, _process=process, _request_id=0):
                server._initialize(process)
                with self.assertRaisesRegex(ValueError, "ID does not match"):
                    server._request("tools/list", {})


if __name__ == "__main__":
    unittest.main()


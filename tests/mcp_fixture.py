"""共享真实 MCP 测试进程和有限观察，所有资源由测试拥有并关闭。"""

import json
import sys
import tempfile
import time
from pathlib import Path

from super_agent.adapter.process import StdioMcpServer


class McpFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.configure()

    def configure(self, **values):
        (self.root / "settings.json").write_text(json.dumps(values))

    def server(self, **limits):
        script = Path(__file__).parent / "fixtures" / "mcp_fault_worker.py"
        instance = StdioMcpServer((sys.executable, "-I", "-B", "-u", str(script), str(self.root)),
                                  root=self.root, timeout_seconds=limits.pop("timeout_seconds", 0.2), **limits)
        self.addCleanup(instance.close)
        return instance

    def received(self):
        path = self.root / "received.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def wait_received(self, method, *, count=1):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            matching = [row for row in self.received() if row["method"] == method]
            if len(matching) >= count:
                return matching
            time.sleep(0.005)
        self.fail(f"fixture did not receive {method}")

"""可靠 MCP 客户端：用真实 stdio 验证通知、初始化错误与响应 ID。"""

import unittest

from mcp_fixture import McpFixture


class ReliableMcpClientTests(McpFixture, unittest.TestCase):
    def test_interleaved_notification_is_skipped(self):
        self.assertEqual("work", self.server().list_tools()[0]["name"])

    def test_initialize_error_prevents_initialized_notification(self):
        self.configure(reject_initialize=True)
        server = self.server()
        with self.assertRaisesRegex(RuntimeError, "initialize error"):
            server.list_tools()
        self.assertIsNone(server._process)
        self.assertEqual(["initialize"], [row["method"] for row in self.received()])
        self.configure()
        self.assertEqual("work", server.list_tools()[0]["name"])

    def test_response_id_mismatch_raises(self):
        self.configure(mode="mismatch")
        server = self.server()
        with self.assertRaisesRegex(ValueError, "ID does not match"):
            server.list_tools()
        self.assertIsNone(server._process)


if __name__ == "__main__":
    unittest.main()

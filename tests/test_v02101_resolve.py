"""把通用能力需求映射到 Harness 原生工具或 MCP 的具体供应。"""

import unittest

from super_agent.skill.resolve import resolve_capabilities


class CapabilityResolutionTests(unittest.TestCase):
    def test_prefers_native_tool_over_mcp(self):
        result = resolve_capabilities(
            ["file_read", "file_write"],
            native_mapping={"file_read": "Read", "file_write": "Write"},
            mcp_providers={"notes": ["file_write", "file_read"]},
        )
        kinds = {item.to_dict()["capability"]: item.to_dict()["kind"] for item in result}
        self.assertEqual("native", kinds["file_read"])
        self.assertEqual("native", kinds["file_write"])

    def test_falls_back_to_mcp_when_no_native(self):
        result = resolve_capabilities(
            ["file_read", "network_read"],
            native_mapping={"file_read": "Read"},
            mcp_providers={"fetcher": ["network_read"]},
        )
        by_capability = {item.capability: item for item in result}
        self.assertEqual("native", by_capability["file_read"].kind)
        self.assertEqual("mcp", by_capability["network_read"].kind)
        self.assertEqual("fetcher", by_capability["network_read"].provider)

    def test_missing_capability_is_skipped_not_fabricated(self):
        result = resolve_capabilities(
            ["file_read", "process_run"],
            native_mapping={"file_read": "Read"},
            mcp_providers={},
        )
        caps = {item.capability for item in result}
        self.assertEqual({"file_read"}, caps)
        self.assertNotIn("process_run", caps)

    def test_deduplicates_capabilities(self):
        result = resolve_capabilities(
            ["file_read", "file_read"],
            native_mapping={"file_read": "Read"},
            mcp_providers={},
        )
        self.assertEqual(1, len(result))


if __name__ == "__main__":
    unittest.main()

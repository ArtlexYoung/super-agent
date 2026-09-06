"""Harness 能力自描述与结构化兼容报告：不宣称未验证的兼容。"""

import unittest

from super_agent.skill.capability import capability_needs
from super_agent.skill.harness import Harness, compatibility_report


class HarnessCompatibilityTests(unittest.TestCase):
    def test_harness_describes_capabilities(self):
        harness = Harness(
            "codex", "0.1.0", supplied_capabilities=("file_read", "file_write"),
            tool_mapping={"file_read": "read_file", "file_write": "write_file"},
        )
        description = harness.describes()
        self.assertEqual("codex", description["name"])
        self.assertEqual(["file_read", "file_write"], description["supplied_capabilities"])
        self.assertEqual({"file_read": "read_file", "file_write": "write_file"}, description["tool_mapping"])
        self.assertTrue(harness.can_supply("file_read"))
        self.assertFalse(harness.can_supply("git_read"))

    def test_report_blocked_when_required_capability_missing(self):
        needs = capability_needs(["file_read", "git_read"], ["file_write"])
        harness = Harness("codex", "0.1.0", supplied_capabilities=("file_read",))
        report = compatibility_report(needs, harness)
        self.assertEqual("blocked", report["status"])
        self.assertEqual(["git_read"], report["missing"])
        self.assertEqual("L0", report["level"])

    def test_report_partial_when_only_optional_missing(self):
        needs = capability_needs(["file_read"], ["file_write"])
        harness = Harness("claude", "0.2.0", supplied_capabilities=("file_read",))
        report = compatibility_report(needs, harness)
        self.assertEqual("partial", report["status"])
        self.assertEqual(["file_write"], report["optional_missing"])
        self.assertEqual("L1", report["level"])

    def test_report_complete_and_maps_tools(self):
        needs = capability_needs(["file_read", "process_run"], [])
        harness = Harness(
            "opencode", "0.3.0",
            supplied_capabilities=("file_read", "process_run"),
            tool_mapping={"file_read": "cat", "process_run": "exec"},
        )
        report = compatibility_report(needs, harness)
        self.assertEqual("complete", report["status"])
        self.assertEqual([], report["missing"])
        self.assertEqual({"file_read": "cat", "process_run": "exec"}, report["tool_mapping"])
        self.assertEqual("L1", report["level"])

    def test_reports_never_claim_undeclared_capability(self):
        # 宿主仅声明 file_read，报告不得把 process_run 当作支持。
        needs = capability_needs(["file_read", "process_run"], [])
        harness = Harness("pi", "0.1.0", supplied_capabilities=("file_read",))
        report = compatibility_report(needs, harness)
        self.assertEqual("blocked", report["status"])
        self.assertIn("process_run", report["missing"])
        self.assertNotIn("process_run", report["supplied"])


if __name__ == "__main__":
    unittest.main()

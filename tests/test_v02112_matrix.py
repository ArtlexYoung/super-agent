"""全链路发行验收兼容矩阵：L1/L2/L3 等级声明，不虚构未验证兼容。"""

import unittest

from super_agent.skill.capability import capability_needs
from super_agent.skill.matrix import compatibility_matrix, coverage_summary


class CompatibilityMatrixTests(unittest.TestCase):
    def test_matrix_lists_all_presets_with_levels(self):
        needs = capability_needs(["file_read"], ["file_write"])
        matrix = compatibility_matrix(needs)
        names = {entry["harness"] for entry in matrix["entries"]}
        self.assertEqual({"deepseek-harness", "codex", "claude-code"}, names)
        # 未验证宿主不声明必需能力支持，因此全部为 L0/blocked。
        for entry in matrix["entries"]:
            self.assertEqual("L0", entry["level"])
            self.assertEqual("blocked", entry["status"])

    def test_coverage_summary_separates_local_and_pending(self):
        needs = capability_needs(["file_read"], [])
        summary = coverage_summary(needs)
        self.assertEqual([], summary["local_verified"])
        self.assertEqual(3, len(summary["pending_external"]))
        self.assertIn("L2/L3", summary["note"])

    def test_matrix_reports_entry_kind_per_harness(self):
        matrix = compatibility_matrix(capability_needs([], []))
        kinds = {entry["harness"]: entry["entry_kind"] for entry in matrix["entries"]}
        self.assertEqual("plugin", kinds["deepseek-harness"])
        self.assertEqual("skill", kinds["codex"])
        self.assertEqual("plugin", kinds["claude-code"])


if __name__ == "__main__":
    unittest.main()

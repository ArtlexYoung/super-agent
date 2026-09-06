"""固定参考 Harness 预设：仅供 L1 格式说明，不宣称 L2/L3 已验证。"""

import unittest

from super_agent.skill.capability import capability_needs
from super_agent.skill.harness import compatibility_report
from super_agent.skill.preset import ENTRY_KINDS, PRESETS, harness_preset, preset_names
from super_agent.skill.projection import project_harness


class HarnessPresetTests(unittest.TestCase):
    def test_presets_are_declared_not_verified(self):
        self.assertEqual(
            {"claude-code", "codex", "deepseek-harness"}, set(preset_names())
        )
        for name, harness in PRESETS.items():
            description = harness.describes()
            self.assertEqual(name, description["name"])
            # 未在真实宿主验证：能力供应为空，投影不会伪造支持。
            self.assertEqual([], description["supplied_capabilities"])

    def test_entry_kinds_are_declared_per_harness(self):
        self.assertEqual("plugin", ENTRY_KINDS["deepseek-harness"])
        self.assertEqual("skill", ENTRY_KINDS["codex"])
        self.assertEqual("plugin", ENTRY_KINDS["claude-code"])

    def test_unverified_preset_cannot_claim_capability_support(self):
        needs = capability_needs(["file_read"], ["file_write"])
        report = compatibility_report(needs, harness_preset("codex"))
        self.assertEqual("blocked", report["status"])
        self.assertEqual(["file_read"], report["missing"])
        self.assertEqual("L0", report["level"])

    def test_projection_rejects_unverified_harness_needs(self):
        needs = capability_needs(["file_read"], [])
        with self.assertRaisesRegex(ValueError, "cannot provide required capabilities"):
            project_harness(
                ("skill:super-agent/common",),
                harness_preset("codex"),
                entry_kind=ENTRY_KINDS["codex"],
                source_hash="x",
                needs=needs,
            )


if __name__ == "__main__":
    unittest.main()

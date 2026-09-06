"""通用资源到 Harness 原生入口的声明式投影：只记录入口差异，不改写正文。"""

import unittest

from super_agent.skill.capability import capability_needs
from super_agent.skill.harness import Harness
from super_agent.skill.projection import project_harness


class ProjectionTests(unittest.TestCase):
    def test_projection_declares_harness_native_entry(self):
        harness = Harness(
            "claude", "0.2.0",
            supplied_capabilities=("file_read", "file_write"),
            tool_mapping={"file_read": "Read", "file_write": "Write"},
        )
        plan = project_harness(
            ("skill:super-agent/code",),
            harness,
            entry_kind="plugin",
            source_hash="abc123",
            needs=capability_needs(["file_read", "file_write"], []),
            harness_specific={"plugin_key": "super-agent.code"},
        )
        self.assertEqual("claude", plan.harness)
        self.assertEqual("plugin", plan.entry_kind)
        self.assertEqual(("skill:super-agent/code",), plan.resources)
        self.assertEqual({"file_read": "Read", "file_write": "Write"}, plan.capability_bindings)
        self.assertEqual("abc123", plan.source_hash)
        self.assertEqual({"plugin_key": "super-agent.code"}, plan.harness_specific)

    def test_projection_never_rewrites_bodies(self):
        # 投影结果只含引用与差异，不携带任何资源正文。
        harness = Harness("codex", "0.1.0", supplied_capabilities=("file_read",))
        plan = project_harness(
            ("skill:super-agent/common",), harness, entry_kind="skill",
            source_hash="s0h1", harness_specific={},
        )
        serialized = str(plan.to_dict())
        self.assertIn("super-agent/common", serialized)
        self.assertNotIn("Body.", serialized)

    def test_projection_rejects_missing_required_capability(self):
        harness = Harness("pi", "0.1.0", supplied_capabilities=("file_read",))
        with self.assertRaisesRegex(ValueError, "cannot provide required capabilities"):
            project_harness(
                ("skill:super-agent/code",), harness, entry_kind="skill",
                source_hash="x", needs=capability_needs(["process_run"], []),
            )

    def test_projection_deduplicates_resources(self):
        harness = Harness("opencode", "0.3.0", supplied_capabilities=("file_read",))
        plan = project_harness(
            ("skill:a", "skill:b", "skill:a"), harness, entry_kind="plugin", source_hash="h"
        )
        self.assertEqual(("skill:a", "skill:b"), plan.resources)


if __name__ == "__main__":
    unittest.main()

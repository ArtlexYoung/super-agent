"""通用能力需求：不绑定具体工具名的能力描述与满足核算。"""

import unittest
from pathlib import Path

from super_agent.skill.capability import (
    CAPABILITY_VOCABULARY,
    capability_needs,
)
from super_agent.skill.document import parse_skill_text


class CapabilityNeedsTests(unittest.TestCase):
    def test_capability_needs_are_tool_name_free(self):
        # 能力需求只写通用能力名，不写宿主工具名。
        needs = capability_needs(["file_read", "git_read"], ["process_poll"])
        self.assertEqual(("file_read", "git_read"), needs.required)
        self.assertEqual(("process_poll",), needs.optional)

    def test_describe_reports_known_and_unknown_without_dropping(self):
        needs = capability_needs(["file_read", "mystery_op"], ["file_write"])
        description = needs.describe()
        self.assertEqual(["file_read", "mystery_op"], description["required"])
        self.assertEqual(["file_write"], description["optional"])
        self.assertEqual(["file_read"], description["known_required"])
        self.assertEqual(["mystery_op"], description["unknown_required"])

    def test_support_accounts_by_capability_not_tool(self):
        needs = capability_needs(["file_read", "git_read"], ["process_poll"])
        # 宿主提供 file_read 但不提供 git_read -> missing。
        report = needs.support(["file_read"])
        self.assertEqual("missing", report["support"])
        self.assertEqual(["git_read"], report["missing"])
        # 宿主提供全部必需但缺可选 -> partial。
        report = needs.support(["file_read", "git_read"])
        self.assertEqual("partial", report["support"])
        self.assertEqual(["process_poll"], report["optional_missing"])
        # 宿主提供全部 -> complete。
        report = needs.support(["file_read", "git_read", "process_poll"])
        self.assertEqual("complete", report["support"])

    def test_vocabulary_is_shared_cross_host_reference(self):
        self.assertIn("file_read", CAPABILITY_VOCABULARY)
        self.assertIn("process_run", CAPABILITY_VOCABULARY)

    def test_skill_surfaces_host_neutral_needs(self):
        text = (
            "---\nname: code\ndescription: A coding method\n"
            "metadata: {super-agent-capabilities: [\"file_read\"], "
            'super-agent-optional-capabilities: ["file_write"]}\n'
            "---\n\nBody.\n"
        )
        skill = parse_skill_text(text, Path("/tmp/code/SKILL.md"))
        needs = skill.capability_needs()
        self.assertEqual(("file_read",), needs.required)
        report = skill.capability_support(["file_read"])
        self.assertEqual("partial", report["support"])
        self.assertEqual(["file_write"], report["optional_missing"])


if __name__ == "__main__":
    unittest.main()

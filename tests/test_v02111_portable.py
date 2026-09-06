"""把 Skill 方法能力与可选执行部分拆为可移植内容，供任意宿主消费。"""

import unittest
from pathlib import Path

from super_agent.skill.document import parse_skill_text
from super_agent.skill.portable import portable_skill_content


def _skill():
    text = (
        "---\nname: code\ndescription: A coding method\n"
        "metadata:\n"
        '  super-agent-capabilities: "[\\"file_read\\",\\"file_write\\"]"\n'
        '  super-agent-optional-capabilities: "[\\"file_search\\"]"\n'
        '  author: "example"\n'
        "---\n\nRead the file, decide, verify.\n"
    )
    return parse_skill_text(text, Path("/tmp/code/SKILL.md"))


class PortableContentTests(unittest.TestCase):
    def test_method_body_is_portable(self):
        content = portable_skill_content(_skill())
        self.assertEqual("Read the file, decide, verify.", content["body"])
        self.assertEqual("code", content["name"])

    def test_execution_is_marked_optional_and_host_mapped(self):
        content = portable_skill_content(_skill())
        execution = content["execution"]
        self.assertTrue(execution["optional"])
        self.assertTrue(execution["mapped_by_host"])
        self.assertIn("portable", execution["note"])

    def test_capabilities_are_declared_not_tool_bound(self):
        content = portable_skill_content(_skill())
        self.assertEqual(["file_read", "file_write"], content["required_capabilities"])
        self.assertEqual(["file_search"], content["optional_capabilities"])

    def test_unknown_extensions_are_preserved_not_dropped(self):
        content = portable_skill_content(_skill())
        self.assertEqual("example", content["extensions"]["author"])


if __name__ == "__main__":
    unittest.main()

"""完善标准 Skill 解析：流列表、转义与内置 Skill 宿主中立内容。"""

import unittest
from pathlib import Path

from super_agent.skill.document import parse_skill_text


def _parse(text, name="demo"):
    return parse_skill_text(text, Path(f"/tmp/{name}/SKILL.md"))


class StandardSkillParsingTests(unittest.TestCase):
    def test_flow_list_values_parse_to_strings(self):
        skill = _parse(
            '---\nname: demo\ndescription: A skill\nmetadata:\n'
            '  super-agent-requires: ["list_files", "read_file"]\n---\n\nBody.\n'
        )
        self.assertEqual(("list_files", "read_file"), skill.requires)

    def test_flow_list_preserves_commas_inside_quotes_and_escapes(self):
        skill = _parse(
            '---\nname: demo\ndescription: A skill\nmetadata:\n'
            '  super-agent-requires: ["a,b", "c\\"d"]\n---\n\nBody.\n'
        )
        self.assertEqual(("a,b", 'c"d'), skill.requires)

    def test_single_quoted_flow_list_items(self):
        skill = _parse(
            "---\nname: demo\ndescription: A skill\nmetadata:\n"
            "  super-agent-requires: ['x', 'y']\n---\n\nBody.\n"
        )
        self.assertEqual(("x", "y"), skill.requires)


class HostNeutralBuiltinTests(unittest.TestCase):
    def test_builtin_code_skill_declares_host_neutral_capabilities(self):
        root = (
            Path(__file__).resolve().parents[1]
            / "src" / "super_agent" / "skill" / "builtin"
        )
        from super_agent.skill.library import AgentLibrary

        library = AgentLibrary((root,))
        skill = library.find_skill("skill:super-agent/code")
        # 通用能力需求不绑定具体工具名。
        self.assertEqual(("file_read", "file_write", "file_delete", "git_read", "process_run"), skill.capabilities)
        # 正文仍描述通用方法，而不是 Super Agent 专属工具。
        self.assertIn("Repository coding method", skill.body)


if __name__ == "__main__":
    unittest.main()

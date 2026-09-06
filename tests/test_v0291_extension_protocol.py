"""通用扩展包协议：规范版本、Schema、能力约束与宿主扩展分离。"""

import tempfile
import unittest
from pathlib import Path

from super_agent.skill.document import (
    AGENT_SKILLS_SPEC,
    Skill,
    parse_skill_text,
)


def _parse(name="demo", body="Body text.", extra_meta="", extra_top=""):
    front = f"---\nname: {name}\ndescription: A skill\n{extra_top}"
    if extra_meta:
        front += extra_meta + "\n"
    front += "---\n\n"
    return parse_skill_text(front + body + "\n", Path(f"/tmp/{name}/SKILL.md"))


class ExtensionProtocolTests(unittest.TestCase):
    def test_spec_version_is_pinned(self):
        self.assertEqual("0.1", AGENT_SKILLS_SPEC)

    def test_plain_skill_parses_and_reports_complete(self):
        skill = _parse()
        self.assertEqual("demo", skill.name)
        self.assertEqual("prompt", skill.skill_type)
        audit = skill.protocol_audit()
        self.assertEqual("complete", audit["status"])
        self.assertEqual(AGENT_SKILLS_SPEC, audit["spec"])

    def test_inline_metadata_mapping_accepted(self):
        skill = _parse(extra_meta='\nmetadata: {author: "example"}')
        # 内联映射中的 author 键被保留，而不是被拒绝。
        self.assertEqual("example", skill.metadata.get("standard_metadata", {}).get(
            "author"
        ))

    def test_capability_constraints_are_ported_separately(self):
        skill = _parse(
            extra_meta=(
                '\nmetadata: {super-agent-capabilities: ["read_file"], '
                'super-agent-optional-capabilities: ["run_test"]}'
            )
        )
        self.assertIn("read_file", skill.capabilities)
        self.assertIn("run_test", skill.optional_capabilities)
        audit = skill.protocol_audit()
        self.assertEqual("partial", audit["status"])
        self.assertEqual(["read_file"], audit["required_capabilities"])
        self.assertEqual(["run_test"], audit["optional_capabilities"])

    def test_host_extensions_are_preserved_not_dropped(self):
        skill = _parse(extra_meta='\nmetadata: {disable-model-invocation: "true"}')
        extensions = skill.protocol_audit()["extensions"]
        self.assertIn("disable-model-invocation", extensions)

    def test_skill_with_resources_detects_scripts_references_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "multi"
            (root / "scripts").mkdir(parents=True)
            (root / "references").mkdir(parents=True)
            (root / "assets").mkdir(parents=True)
            (root / "scripts" / "run.py").write_text("print(1)\n")
            (root / "references" / "guide.md").write_text("guide\n")
            (root / "assets" / "data.txt").write_text("data\n")
            text = "---\nname: multi\ndescription: Has resources\n---\n\nBody.\n"
            skill = parse_skill_text(text, root / "SKILL.md")
            self.assertEqual("multi", skill.name)
            self.assertTrue(skill.root / "scripts" / "run.py" in list(skill.root.rglob("*.py")))

    def test_on_disk_protocol_fixture_loads_with_report(self):
        fixture = (
            Path(__file__).resolve().parents[1]
            / "tests" / "fixtures" / "extensions" / "example-skill"
        )
        from super_agent.skill.document import read_skill_package

        package = read_skill_package(fixture)
        skill = parse_skill_text(package.skill_content.decode(), fixture / "SKILL.md")
        self.assertEqual("example-skill", skill.name)
        self.assertEqual(("read_file", "search_files"), skill.capabilities)
        self.assertEqual(("run_test",), skill.optional_capabilities)
        audit = skill.protocol_audit()
        self.assertEqual("partial", audit["status"])
        # 宿主扩展被保留，而不是被丢弃。
        self.assertIn("author", audit["extensions"])
        # 资源（scripts/references/assets）随包一起携带。
        relative = {path.as_posix() for path, _ in package.files}
        self.assertIn("scripts/run.py", relative)
        self.assertIn("references/guide.md", relative)
        self.assertIn("assets/data.txt", relative)


if __name__ == "__main__":
    unittest.main()

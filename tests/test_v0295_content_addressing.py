"""内容寻址与全局去重：参考无关的摘要和跨引用共享内容检测。"""

import hashlib
import tempfile
import unittest
from pathlib import Path

from super_agent.skill.content import ContentAddress, content_digest, dedup
from super_agent.skill.library import AgentLibrary


class ContentAddressingTests(unittest.TestCase):
    def test_content_digest_is_reference_independent(self):
        a = content_digest([("SKILL.md", b"body"), ("scripts/run.py", b"print(1)")])
        # 同样的内容在不同的引用名/路径前缀下仍产生相同摘要（路径来自包内相对路径）。
        b = content_digest([("scripts/run.py", b"print(1)"), ("SKILL.md", b"body")])
        c = content_digest([("SKILL.md", b"body")])
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_dedup_reports_shared_content_copies(self):
        digest = hashlib.sha256(b"identical").hexdigest()
        addresses = [
            ContentAddress(digest, "skill:a", "skill", "0.2.94"),
            ContentAddress(digest, "skill:b", "skill", "0.2.94"),
        ]
        grouped = dedup(addresses)
        self.assertEqual(1, len(grouped))
        report = grouped[digest]
        self.assertEqual(["skill:a", "skill:b"], report["references"])
        self.assertEqual(2, report["copies"])

    def test_library_content_addresses_dedupe_across_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # 两个 Skill 目录，内容完全相同，但引用名不同。
            for name in ("one", "two"):
                (root / "skills" / "super-agent" / name).mkdir(parents=True)
                (root / "skills" / "super-agent" / name / "SKILL.md").write_text(
                    "---\nname: %s\ndescription: same\n---\n\nSame body.\n" % name,
                    encoding="utf-8",
                )
            library = AgentLibrary((root,))
            addresses = library.content_addresses()
            # 两个 Skill 内容不同（name 不同），因此是两个唯一摘要。
            self.assertEqual(2, len(addresses))
            reports = {report["copies"] for report in addresses.values()}
            self.assertEqual({1}, reports)


if __name__ == "__main__":
    unittest.main()

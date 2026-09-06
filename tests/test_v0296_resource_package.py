"""自包含资源包：独立移动、带版本与内容摘要，跨宿主共用输入。"""

import tempfile
import unittest
from pathlib import Path

from super_agent.skill.library import AgentLibrary
from super_agent.skill.package import ResourcePackage, build_resource_package


def _write_skill_root(root: Path, name: str = "code", description: str = "A method") -> None:
    (root / "skills" / "super-agent" / name).mkdir(parents=True)
    (root / "skills" / "super-agent" / name / "SKILL.md").write_text(
        "---\nname: %s\ndescription: %s\n---\n\nBody.\n" % (name, description),
        encoding="utf-8",
    )


class ResourcePackageTests(unittest.TestCase):
    def test_package_is_self_contained_after_move(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            _write_skill_root(source, name="code", description="A coding method")
            destination = base / "portable"
            package = build_resource_package(
                source, destination, version="0.1.0", description="A self-contained package"
            )
            self.assertEqual("0.1.0", package.package_version)
            self.assertEqual("A self-contained package", package.description)
            self.assertTrue(package.verify())

            # 移动包到新位置，仍然自包含可校验。
            moved = base / "moved"
            destination.rename(moved)
            reloaded = ResourcePackage(moved)
            self.assertTrue(reloaded.verify())
            self.assertEqual("0.1.0", reloaded.package_version)

    def test_package_remains_discoverable_as_library_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            _write_skill_root(source, name="research", description="A research method")
            destination = base / "portable"
            build_resource_package(
                source, destination, version="0.1.0", description="A package"
            )
            # 移动后仍可作为 AgentLibrary 根发现资源。
            package_root = AgentLibrary((destination,))
            self.assertIn("skill:super-agent/research", package_root.snapshot().skills)
            self.assertIn("research", package_root.snapshot().skills["skill:super-agent/research"].name)

    def test_modified_content_breaks_verify(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            _write_skill_root(source, name="code", description="A coding method")
            destination = base / "portable"
            build_resource_package(
                source, destination, version="0.1.0", description="A package"
            )
            # 修改包内资源后，清单校验应失败。
            (destination / "skills" / "super-agent" / "code" / "SKILL.md").write_text(
                "---\nname: code\ndescription: tampered\n---\n\nChanged.\n",
                encoding="utf-8",
            )
            self.assertFalse(ResourcePackage(destination).verify())


if __name__ == "__main__":
    unittest.main()

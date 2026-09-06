"""隔离验收套装：为单个 Harness 物化可检查、可移动的 L1 受检目录，不改用户配置。"""

import json
import tempfile
import unittest
from pathlib import Path

from super_agent.skill.bundle import BUNDLE_FILE, create_acceptance_bundle
from super_agent.skill.package import build_resource_package


def _write_skill(root: Path) -> None:
    (root / "skills" / "super-agent" / "code").mkdir(parents=True)
    (root / "skills" / "super-agent" / "code" / "SKILL.md").write_text(
        "---\nname: code\ndescription: A coding method\n"
        'metadata: {super-agent-capabilities: ["file_read"], '
        'super-agent-optional-capabilities: ["file_write"]}\n'
        "---\n\nBody.\n",
        encoding="utf-8",
    )


class AcceptanceBundleTests(unittest.TestCase):
    def _package(self, base: Path) -> "object":
        source = base / "source"
        _write_skill(source)
        return build_resource_package(
            source, base / "portable", version="0.1.0", description="A package"
        )

    def test_bundle_materializes_in_isolated_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            package = self._package(base)
            dest = base / "bundle"
            bundle = create_acceptance_bundle(
                package,
                harness_name="codex",
                supplied_capabilities=("file_read", "file_write"),
                destination=dest,
            )
            self.assertEqual("codex", bundle.harness)
            self.assertTrue((dest / "projection.json").is_file())
            self.assertTrue((dest / "compatibility.json").is_file())
            self.assertTrue((dest / BUNDLE_FILE).is_file())
            report = json.loads((dest / "compatibility.json").read_text())
            self.assertEqual("complete", report["status"])

    def test_bundle_rejects_unsatisfiable_supply(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            package = self._package(base)
            dest = base / "bundle"
            with self.assertRaisesRegex(ValueError, "cannot satisfy required"):
                create_acceptance_bundle(
                    package,
                    harness_name="codex",
                    supplied_capabilities=(),
                    destination=dest,
                )
            # 失败时不留下半成品目录。
            self.assertFalse(dest.exists())


if __name__ == "__main__":
    unittest.main()

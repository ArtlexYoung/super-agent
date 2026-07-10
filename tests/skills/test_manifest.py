import tempfile
import unittest
from pathlib import Path

from skill import (
    SkillLoader,
    SkillManifest,
    explain_skill_selection,
    validate_skill_manifests,
)


class SkillManifestContractTests(unittest.TestCase):
    def test_manifest_reads_supported_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = _write_skill(Path(tmp), schema_version=1)

            manifest = SkillManifest.load_from_file(manifest_path)

            self.assertEqual(1, manifest.schema_version)

    def test_manifest_rejects_unsupported_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = _write_skill(Path(tmp), schema_version=2)

            with self.assertRaisesRegex(ValueError, "unsupported skill schema_version: 2"):
                SkillManifest.load_from_file(manifest_path)

    def test_loader_reports_invalid_manifests_without_hiding_valid_ones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, name="valid", schema_version=1)
            _write_skill(root, name="invalid", schema_version=2)

            issues = validate_skill_manifests(SkillLoader([root]))

            self.assertEqual(1, len(issues))
            self.assertEqual("invalid", issues[0].path.parent.name)
            self.assertIn("unsupported skill schema_version", issues[0].message)

    def test_loader_explains_trigger_and_enabled_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, name="echo", triggers=["echo"])
            _write_skill(root, name="always", triggers=[])

            selections = explain_skill_selection(SkillLoader([root]), "please echo", enabled=["always"])

            selected = {item.name: item for item in selections}
            self.assertTrue(selected["echo"].selected)
            self.assertEqual("matched trigger: echo", selected["echo"].reason)
            self.assertTrue(selected["always"].selected)
            self.assertEqual("enabled by agent config", selected["always"].reason)


def _write_skill(
    root: Path,
    *,
    name: str = "demo",
    schema_version: int = 1,
    triggers: list[str] | None = None,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    trigger_text = ", ".join(f'"{item}"' for item in triggers or [])
    manifest_path = skill_dir / "skill.toml"
    manifest_path.write_text(
        f"""
schema_version = {schema_version}
name = "{name}"
kind = "prompt"
description = "{name} skill"
version = "0.1.0"
triggers = [{trigger_text}]

[entry]
instructions = "SKILL.md"
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(f"Use {name}.", encoding="utf-8")
    return manifest_path

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from skill.disclosure import ProgressiveDisclosureCore
from skill.manifest import skill_manifest_to_dict


class SkillManifestContractTests(unittest.TestCase):
    def test_manifest_reads_supported_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_skill(Path(tmp), schema_version=3)

            manifest = _read_manifest(Path(tmp), "demo")

            self.assertEqual(3, manifest.schema_version)

    def test_manifest_rejects_unsupported_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_skill(Path(tmp), schema_version=2)

            with self.assertRaisesRegex(ValueError, "unsupported.*schema_version.*3"):
                _create_disclosure(Path(tmp)).prepare_skill_index()

    def test_manifest_requires_explicit_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = _write_skill(Path(tmp), schema_version=3)
            text = manifest_path.read_text(encoding="utf-8").replace("schema_version = 3\n", "")
            manifest_path.write_text(text, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing schema_version.*schema_version = 3"):
                _create_disclosure(Path(tmp)).prepare_skill_index()

    def test_manifest_rejects_wrong_field_type_in_schema_v3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = _write_skill(Path(tmp), schema_version=3)
            text = manifest_path.read_text(encoding="utf-8").replace('name = "demo"', "name = 123")
            manifest_path.write_text(text, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "skill name must be a string"):
                _create_disclosure(Path(tmp)).prepare_skill_index()

    def test_manifest_serializer_emits_normalized_schema_v3_without_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_skill(Path(tmp), schema_version=3)
            manifest = _read_manifest(Path(tmp), "demo")

            data = skill_manifest_to_dict(manifest)

            self.assertEqual(3, data["schema_version"])
            self.assertEqual("prompt", data["type"])
            self.assertNotIn("kind", data)
            self.assertEqual("demo", data["name"])
            self.assertEqual(["demo"], data["provides"])
            self.assertNotIn("path", data)

    def test_manifest_serializer_rejects_unsupported_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_skill(Path(tmp), schema_version=3)
            manifest = _read_manifest(Path(tmp), "demo")

            with self.assertRaisesRegex(
                ValueError,
                "unsupported skill schema_version: 2; expected 3",
            ):
                skill_manifest_to_dict(replace(manifest, schema_version=2))

    def test_core_reports_invalid_manifests_without_hiding_valid_ones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, name="valid", schema_version=3)
            _write_skill(root, name="invalid", schema_version=2)

            issues = _create_disclosure(root).validate_skill_sources()

            self.assertEqual(1, len(issues))
            self.assertEqual("invalid", issues[0].path.parent.name)
            self.assertIn("unsupported skill schema_version", issues[0].message)

    def test_core_discovers_a_custom_skill_type_without_a_supported_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, name="translate", skill_type="transform")

            index = _create_disclosure(root).prepare_skill_index()

            self.assertEqual("transform:translate", index.entries[0].reference.key)

    def test_schema_v3_rejects_old_type_specific_configuration_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = _write_skill(root)
            with manifest_path.open("a", encoding="utf-8") as file:
                file.write("\n[memory]\nrecall_limit = 10\n")

            issues = _create_disclosure(root).validate_skill_sources()

            self.assertEqual(1, len(issues))
            self.assertIn("unknown skill manifest fields: memory", issues[0].message)

    def test_core_selects_triggered_and_explicitly_enabled_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, name="echo", triggers=["echo"])
            _write_skill(root, name="always", triggers=[])

            disclosure = _create_disclosure(root)
            disclosure.prepare_skill_index()
            selections = disclosure.select_skill_references_for_prompt(
                "please echo",
                enabled_names=["always"],
                allowed_types={"prompt", "mcp"},
            )

            self.assertEqual({"prompt:echo", "prompt:always"}, {item.key for item in selections})


def _write_skill(
    root: Path,
    *,
    name: str = "demo",
    schema_version: int = 3,
    skill_type: str = "prompt",
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
type = "{skill_type}"
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


def _create_disclosure(root: Path) -> ProgressiveDisclosureCore:
    return ProgressiveDisclosureCore([root])


def _read_manifest(root: Path, name: str):
    disclosure = _create_disclosure(root)
    disclosure.prepare_skill_index()
    return disclosure.open_skill(name, "prompt").read_manifest()

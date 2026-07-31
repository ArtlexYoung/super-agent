import tempfile
import unittest
from pathlib import Path

from skill.disclosure import ProgressiveDisclosureCore
from skill.manifest import calculate_skill_directory_sha256, skill_manifest_to_dict


class SkillManifestContractTests(unittest.TestCase):
    def test_directory_hash_rejects_directory_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = _write_skill(root)
            outside = root / "outside"
            outside.mkdir()
            (manifest_path.parent / "linked").symlink_to(
                outside,
                target_is_directory=True,
            )

            with self.assertRaisesRegex(ValueError, "cannot contain symlinks"):
                calculate_skill_directory_sha256(manifest_path.parent)

            skill_link = root / "skill-link"
            skill_link.symlink_to(manifest_path.parent, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "cannot contain symlinks"):
                calculate_skill_directory_sha256(skill_link)

    def test_manifest_uses_directory_name_and_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_skill(Path(tmp))

            manifest = _read_manifest(Path(tmp), "demo")

            self.assertEqual("demo", manifest.name)
            self.assertEqual("prompt", manifest.skill_type)
            self.assertEqual("0.1.0", manifest.version)
            self.assertEqual("SKILL.md", manifest.entry.instructions)

    def test_manifest_rejects_removed_schema_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = _write_skill(Path(tmp))
            manifest_path.write_text(
                'schema_version = 3\ndescription = "demo skill"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unknown skill manifest fields: schema_version"):
                _create_disclosure(Path(tmp)).prepare_skill_index()

    def test_manifest_rejects_removed_name_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = _write_skill(Path(tmp))
            manifest_path.write_text(
                'name = "demo"\ndescription = "demo skill"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unknown skill manifest fields: name"):
                _create_disclosure(Path(tmp)).prepare_skill_index()

    def test_manifest_rejects_wrong_description_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = _write_skill(Path(tmp))
            manifest_path.write_text("description = 123\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "skill description must be a non-empty string"):
                _create_disclosure(Path(tmp)).prepare_skill_index()

    def test_manifest_serializer_emits_derived_values_without_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_skill(Path(tmp))
            manifest = _read_manifest(Path(tmp), "demo")

            data = skill_manifest_to_dict(manifest)

            self.assertEqual("prompt", data["type"])
            self.assertEqual("demo", data["name"])
            self.assertEqual(
                {"name", "type", "description", "version"},
                set(data),
            )

    def test_core_reports_invalid_manifests_without_hiding_valid_ones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, name="valid")
            invalid = _write_skill(root, name="invalid")
            invalid.write_text("schema_version = 3\ndescription = \"invalid\"\n")

            issues = _create_disclosure(root).validate_skill_sources()

            self.assertEqual(1, len(issues))
            self.assertEqual("invalid", issues[0].path.parent.name)
            self.assertIn("unknown skill manifest fields", issues[0].message)

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

    def test_core_selects_only_explicitly_enabled_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, name="echo")
            _write_skill(root, name="always")

            disclosure = _create_disclosure(root)
            disclosure.prepare_skill_index()
            selections = disclosure.select_skill_references(
                selected_names=["always"],
                allowed_types={"prompt", "mcp"},
            )

            self.assertEqual({"prompt:always"}, {item.key for item in selections})


def _write_skill(
    root: Path,
    *,
    name: str = "demo",
    skill_type: str = "prompt",
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    manifest_path = skill_dir / "skill.toml"
    manifest_path.write_text(
        f'type = "{skill_type}"\ndescription = "{name} skill"\n',
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

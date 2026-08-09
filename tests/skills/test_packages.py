import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from core.state.events import create_local_event_store
from skill.disclosure import ProgressiveDisclosureCore
from skill.runtime.files.package import SkillPackageManager


class SkillPackageManagerTests(unittest.TestCase):
    def test_pack_skill_creates_deterministic_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root = root / "skills"
            _write_skill(skill_root / "demo", "demo", "0.1.0", "Use demo.")
            manager = _manager(skill_root)
            first = root / "first.zip"
            second = root / "second.zip"

            manager.pack_skill("demo", first)
            manager.pack_skill("demo", second)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(
                    ["demo/SKILL.md", "demo/skill.toml"],
                    archive.namelist(),
                )

    def test_install_skill_from_local_directory_and_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root = root / "source"
            source = source_root / "demo"
            _write_skill(source, "demo", "0.1.0", "Use local demo.")
            source_manager = _manager(source_root)
            package_path = source_manager.pack_skill("demo", root / "demo.zip")

            directory_target = root / "directory-target"
            zip_target = root / "zip-target"
            directory_manager = _manager(directory_target)
            zip_manager = _manager(zip_target)
            installed_directory = directory_manager.install_skill(str(source))
            installed_zip = zip_manager.install_skill(str(package_path))

            self.assertEqual("demo", installed_directory.name)
            self.assertEqual("demo", installed_zip.name)
            self.assertEqual(
                "Use local demo.",
                (zip_manager.user_skill_root / "prompt" / "demo" / "SKILL.md").read_text(
                    encoding="utf-8"
                ),
            )

    @unittest.skipIf(shutil.which("git") is None, "git is required")
    def test_install_skill_from_local_git_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = root / "repository"
            repository.mkdir()
            _write_skill(repository / "skills" / "git-demo", "git-demo", "0.2.0", "Use Git demo.")
            _commit_git_repository(repository)

            installed = _manager(root / "installed").install_skill(
                f"git+{repository}#skills/git-demo"
            )

            self.assertEqual("git-demo", installed.name)
            self.assertEqual("0.2.0", installed.version)

    def test_install_rejects_unexpected_content_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "hash-demo"
            _write_skill(source, "hash-demo", "0.1.0", "Hash me.")
            target = root / "skills"

            with self.assertRaisesRegex(ValueError, "skill content SHA-256 mismatch"):
                _manager(target).install_skill(str(source), expected_sha256="0" * 64)

            self.assertFalse((target / "hash-demo").exists())

    def test_update_replaces_existing_skill_and_remove_deletes_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root = root / "skills"
            current = skill_root / "demo"
            source = root / "updated" / "demo"
            _write_skill(current, "demo", "0.1.0", "Old instructions.")
            _write_skill(source, "demo", "0.2.0", "New instructions.")
            manager = _manager(skill_root)

            updated = manager.update_skill("demo", str(source))

            self.assertEqual("0.2.0", updated.version)
            user_skill = manager.user_skill_root / "prompt" / "demo"
            self.assertEqual("Old instructions.", (current / "SKILL.md").read_text())
            self.assertEqual("New instructions.", (user_skill / "SKILL.md").read_text())
            manager.remove_skill("demo")
            self.assertFalse(user_skill.exists())
            self.assertTrue(current.exists())

    def test_skill_type_selects_one_skill_when_names_are_shared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root = root / "skills"
            _write_skill(skill_root / "shared", "shared", "0.1.0", "Prompt instructions.")
            _write_skill(
                skill_root / "memory" / "shared",
                "shared",
                "0.2.0",
                "Memory instructions.",
                skill_type="memory",
            )
            manager = _manager(skill_root)

            package_path = manager.pack_skill("memory:shared", root / "memory-shared.zip")

            with zipfile.ZipFile(package_path) as archive:
                manifest = archive.read("shared/skill.toml").decode("utf-8")
            self.assertIn('type = "memory"', manifest)

    def test_update_rejects_changing_skill_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root = root / "skills"
            current = skill_root / "memory" / "shared"
            source = root / "updated" / "shared"
            _write_skill(current, "shared", "0.1.0", "Memory instructions.", skill_type="memory")
            _write_skill(source, "shared", "0.2.0", "Prompt instructions.")

            with self.assertRaisesRegex(ValueError, "Skill type does not match target"):
                _manager(skill_root).update_skill("memory:shared", str(source))

            self.assertIn(
                'type = "memory"',
                (current / "skill.toml").read_text(encoding="utf-8"),
            )

    def test_install_rejects_zip_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package_path = root / "malicious.zip"
            with zipfile.ZipFile(package_path, "w") as archive:
                archive.writestr("../outside.txt", "escaped")
                archive.writestr("demo/skill.toml", 'name = "demo"\n')

            with self.assertRaisesRegex(ValueError, "unsafe path in skill package"):
                _manager(root / "skills").install_skill(str(package_path))

            self.assertFalse((root / "outside.txt").exists())

    def test_install_rejects_removed_entry_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            _write_skill(source, "unsafe", "0.1.0", "Unsafe.")
            manifest_path = source / "skill.toml"
            manifest_path.write_text(
                manifest_path.read_text(encoding="utf-8")
                + '\n[entry]\ninstructions = "../../outside.txt"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unknown skill manifest fields: entry"):
                _manager(root / "skills").install_skill(str(source))


def _manager(skill_root: Path) -> SkillPackageManager:
    store = create_local_event_store(
        skill_root.parent / f".{skill_root.name}-package-runtime"
    )
    disclosure = ProgressiveDisclosureCore([skill_root])
    return SkillPackageManager(disclosure, store)


def _write_skill(
    path: Path,
    name: str,
    version: str,
    instructions: str,
    *,
    skill_type: str = "prompt",
) -> None:
    path.mkdir(parents=True, exist_ok=True)
    configuration = (
        ""
        if skill_type == "prompt"
        else (
            '\n[configuration]\ndefault_scope = "agent"\n'
            "recall_limit = 20"
        )
    )
    (path / "skill.toml").write_text(
        f"""
type = "{skill_type}"
description = "Packaged skill"
version = "{version}"

{configuration}
""".strip(),
        encoding="utf-8",
    )
    (path / "SKILL.md").write_text(instructions, encoding="utf-8")


def _commit_git_repository(path: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Super Agent Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "add skill",
        ],
        check=True,
    )

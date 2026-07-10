import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from skill import SkillLoader, SkillPackageManager


class SkillPackageManagerTests(unittest.TestCase):
    def test_pack_skill_creates_deterministic_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root = root / "skills"
            _write_skill(skill_root / "demo", "demo", "0.1.0", "Use demo.")
            manager = SkillPackageManager(SkillLoader([skill_root]), skill_root)
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
            source_manager = SkillPackageManager(SkillLoader([source_root]), source_root)
            package_path = source_manager.pack_skill("demo", root / "demo.zip")

            directory_target = root / "directory-target"
            zip_target = root / "zip-target"
            installed_directory = _manager(directory_target).install_skill(str(source))
            installed_zip = _manager(zip_target).install_skill(str(package_path))

            self.assertEqual("demo", installed_directory.name)
            self.assertEqual("demo", installed_zip.name)
            self.assertEqual(
                "Use local demo.",
                (zip_target / "demo" / "SKILL.md").read_text(encoding="utf-8"),
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
            source = root / "source"
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
            source = root / "updated"
            _write_skill(current, "demo", "0.1.0", "Old instructions.")
            _write_skill(source, "demo", "0.2.0", "New instructions.")
            manager = _manager(skill_root)

            updated = manager.update_skill("demo", str(source))

            self.assertEqual("0.2.0", updated.version)
            self.assertEqual("New instructions.", (current / "SKILL.md").read_text(encoding="utf-8"))
            manager.remove_skill("demo")
            self.assertFalse(current.exists())

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

    def test_install_rejects_manifest_instruction_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            _write_skill(source, "unsafe", "0.1.0", "Unsafe.")
            manifest_path = source / "skill.toml"
            manifest_path.write_text(
                manifest_path.read_text(encoding="utf-8").replace(
                    'instructions = "SKILL.md"',
                    'instructions = "../../outside.txt"',
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "instruction path leaves skill directory"):
                _manager(root / "skills").install_skill(str(source))


def _manager(skill_root: Path) -> SkillPackageManager:
    return SkillPackageManager(SkillLoader([skill_root]), skill_root)


def _write_skill(path: Path, name: str, version: str, instructions: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "skill.toml").write_text(
        f"""
schema_version = 1
name = "{name}"
kind = "prompt"
description = "Packaged skill"
version = "{version}"
triggers = ["{name}"]

[entry]
instructions = "SKILL.md"
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

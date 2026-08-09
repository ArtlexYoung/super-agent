import tempfile
import tomllib
import unittest
from pathlib import Path

from super_agent import Agent
from core.config import CommonConfig
from core.provider import MockProvider
from skill.disclosure import ProgressiveDisclosureCore
from core.skill_use.files.lock import write_skill_lock_file
from support import write_workflow_skill


class SkillCompositionTests(unittest.TestCase):
    def test_manifest_does_not_embed_a_dependency_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_skill(
                Path(tmp),
                "research",
            )

            disclosure, index = _prepare_disclosure(Path(tmp))
            manifest = disclosure.open_skill("research", "prompt").read_manifest()

            self.assertEqual(["research"], manifest.provides)
            self.assertEqual([], manifest.requires)

    def test_resolver_returns_only_explicitly_requested_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "transport")
            _write_skill(root, "research")
            _write_skill(root, "report")
            _, index = _prepare_disclosure(root)

            resolved = index.resolve_skill_dependencies(["report"])

            self.assertEqual(
                ["report"],
                [item.reference.name for item in resolved],
            )

    def test_resolver_rejects_missing_requested_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "report")
            _, index = _prepare_disclosure(root)

            with self.assertRaisesRegex(KeyError, "skill not found: missing"):
                index.resolve_skill_dependencies(["missing"])

    def test_resolver_keeps_requested_order_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "alpha")
            _write_skill(root, "beta")
            _, index = _prepare_disclosure(root)

            resolved = index.resolve_skill_dependencies(["beta", "alpha"])

            self.assertEqual(["alpha", "beta"], [item.reference.name for item in resolved])

    def test_resolver_accepts_same_type_without_provider_indirection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "first-http")
            _write_skill(root, "second-http")
            _write_skill(root, "research")
            _, index = _prepare_disclosure(root)

            resolved = index.resolve_skill_dependencies(["research"])

            self.assertEqual(["research"], [item.reference.name for item in resolved])

    def test_lock_is_deterministic_and_does_not_store_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "transport")
            _write_skill(root, "research")
            disclosure, index = _prepare_disclosure(root)
            resolved = index.resolve_skill_dependencies(["research"])
            manifests = [
                disclosure.open_skill(item.reference.name, item.reference.skill_type).read_manifest()
                for item in resolved
            ]
            first_path = root / "first.lock"
            second_path = root / "second.lock"

            write_skill_lock_file(manifests, first_path)
            write_skill_lock_file(list(reversed(manifests)), second_path)

            first = first_path.read_text(encoding="utf-8")
            lock_data = tomllib.loads(first)
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
            self.assertNotIn(str(root), first)
            self.assertIn('name = "research"', first)
            self.assertIn("sha256 = ", first)
            self.assertEqual(["research"], [item["name"] for item in lock_data["skills"]])

    def test_agent_loads_dependencies_of_configured_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root = root / "skills"
            _write_skill(skill_root, "transport")
            _write_skill(skill_root, "research")
            _write_skill(skill_root, "report")
            write_workflow_skill(root)
            config_path = root / "common.toml"
            config_path.write_text(
                """
schema_version = 1
kind = "common"

[agent]
name = "demo"
system = "Base system."
skills = ["workflow:direct", "memory:default", "report"]

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
                encoding="utf-8",
            )
            agent = Agent(
                CommonConfig.load_from_file(config_path),
                provider=MockProvider("ok"),
                use_storage=True,
            )

            result = agent.run("build an unrelated output")

            self.assertEqual(
                {
                    "memory:default",
                    "workflow:direct",
                    "prompt:report",
                },
                set(result.skills),
            )


def _write_skill(
    root: Path,
    name: str,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
type = "prompt"
description = "{name} skill"

""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(f"Use {name}.", encoding="utf-8")
    return skill_dir


def _prepare_disclosure(root: Path):
    disclosure = ProgressiveDisclosureCore([root])
    return disclosure, disclosure.prepare_skill_index()

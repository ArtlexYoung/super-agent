import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from core.agent import Agent
from core.config import AgentConfig
from core.provider import MockProvider
from skill.disclosure import ProgressiveDisclosureCore
from skill.ecosystem.lock import write_skill_lock_file
from support import write_workflow_skill


class SkillCompositionTests(unittest.TestCase):
    def test_manifest_reads_provided_and_required_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_skill(
                Path(tmp),
                "research",
                provides=["facts"],
                requires=["http"],
            )

            disclosure, index = _prepare_disclosure(Path(tmp))
            manifest = disclosure.open_skill("research", "prompt").read_manifest()

            self.assertEqual(["facts"], manifest.provides)
            self.assertEqual(["http"], manifest.requires)

    def test_resolver_orders_dependencies_before_requested_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "transport", provides=["http"])
            _write_skill(root, "research", provides=["facts"], requires=["http"])
            _write_skill(root, "report", requires=["facts"])
            _, index = _prepare_disclosure(root)

            resolved = index.resolve_skill_dependencies(["report"])

            self.assertEqual(
                ["transport", "research", "report"],
                [item.reference.name for item in resolved],
            )

    def test_resolver_rejects_missing_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "report", requires=["missing-capability"])
            _, index = _prepare_disclosure(root)

            with self.assertRaisesRegex(KeyError, "missing skill capability: missing-capability"):
                index.resolve_skill_dependencies(["report"])

    def test_resolver_reports_dependency_cycle_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "alpha", requires=["beta"])
            _write_skill(root, "beta", requires=["alpha"])
            _, index = _prepare_disclosure(root)

            with self.assertRaisesRegex(ValueError, "prompt:alpha -> prompt:beta -> prompt:alpha"):
                index.resolve_skill_dependencies(["alpha"])

    def test_resolver_rejects_ambiguous_capability_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "first-http", provides=["http"])
            _write_skill(root, "second-http", provides=["http"])
            _write_skill(root, "research", requires=["http"])
            _, index = _prepare_disclosure(root)

            with self.assertRaisesRegex(ValueError, "ambiguous skill capability http"):
                index.resolve_skill_dependencies(["research"])

    def test_lock_is_deterministic_and_does_not_store_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "transport", provides=["http"])
            _write_skill(root, "research", requires=["http"])
            disclosure, index = _prepare_disclosure(root)
            resolved = index.resolve_skill_dependencies(["research"])
            manifests = [
                disclosure.open_skill(item.reference.name, item.reference.kind).read_manifest()
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
            self.assertEqual(["research", "transport"], [item["name"] for item in lock_data["skills"]])

    def test_agent_loads_dependencies_of_configured_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root = root / "skills"
            _write_skill(skill_root, "transport", provides=["http"])
            _write_skill(skill_root, "research", provides=["facts"], requires=["http"])
            _write_skill(skill_root, "report", requires=["facts"])
            write_workflow_skill(root)
            config_path = root / "agent.toml"
            config_path.write_text(
                """
[agent]
name = "demo"
system = "Base system."
workflow = "direct"
memory = "default"
skills = ["report"]

[model]
provider = "mock"
model = "unit-test"

[paths]
skills = ["skills"]
memory = ".super-agent/memory"
""".strip(),
                encoding="utf-8",
            )
            agent = Agent(AgentConfig.load_from_file(config_path), provider=MockProvider("ok"))

            result = agent.run("build an unrelated output")

            self.assertEqual({"report", "research", "transport"}, set(result.skills))
            index = json.loads(
                (root / ".super-agent" / "memory" / "disclosure" / "index.json").read_text()
            )
            indexed = {item["name"]: item for item in index["skills"]}
            self.assertEqual(["facts"], indexed["research"]["provides"])
            self.assertEqual(["http"], indexed["research"]["requires"])


def _write_skill(
    root: Path,
    name: str,
    *,
    provides: list[str] | None = None,
    requires: list[str] | None = None,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    provided = _toml_array(provides if provides is not None else [name])
    required = _toml_array(requires or [])
    (skill_dir / "skill.toml").write_text(
        f"""
schema_version = 1
name = "{name}"
kind = "prompt"
description = "{name} skill"
version = "0.1.0"
provides = {provided}
requires = {required}
triggers = []

[entry]
instructions = "SKILL.md"
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(f"Use {name}.", encoding="utf-8")
    return skill_dir


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(f'"{value}"' for value in values) + "]"


def _prepare_disclosure(root: Path):
    disclosure = ProgressiveDisclosureCore([root], root / ".disclosure-cache")
    return disclosure, disclosure.prepare_skill_index()

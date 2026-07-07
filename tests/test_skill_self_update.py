import tempfile
import unittest
from pathlib import Path

from super_agent import Agent, AgentConfig
from super_agent.core.provider import MockProvider
from super_agent.skill import SkillManifest


class SkillSelfUpdateTests(unittest.TestCase):
    def test_manifest_marks_agent_created_skill_as_updateable_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "draft"
            skill_dir.mkdir(parents=True)
            (skill_dir / "skill.toml").write_text(
                """
name = "draft"
description = "Agent drafted skill"
agent_created = true
triggers = ["draft"]

[entry]
instructions = "SKILL.md"
""".strip(),
                encoding="utf-8",
            )

            manifest = SkillManifest.load_from_file(skill_dir / "skill.toml")

            self.assertTrue(manifest.agent_created)
            self.assertTrue(manifest.agent_can_update)

    def test_manifest_keeps_human_skill_locked_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "human"
            skill_dir.mkdir(parents=True)
            (skill_dir / "skill.toml").write_text(
                """
name = "human"
description = "Human maintained skill"
triggers = ["human"]

[entry]
instructions = "SKILL.md"
""".strip(),
                encoding="utf-8",
            )

            manifest = SkillManifest.load_from_file(skill_dir / "skill.toml")

            self.assertFalse(manifest.agent_created)
            self.assertFalse(manifest.agent_can_update)

    def test_manifest_rejects_string_boolean_update_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "bad"
            skill_dir.mkdir(parents=True)
            (skill_dir / "skill.toml").write_text(
                """
name = "bad"
description = "Bad bool"
agent_created = "false"
triggers = ["bad"]

[entry]
instructions = "SKILL.md"
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                SkillManifest.load_from_file(skill_dir / "skill.toml")

    def test_agent_creates_updateable_skill_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = _make_agent(Path(tmp))

            manifest = agent.create_skill(
                "research-note",
                instructions="Summarize research notes with sources.",
                description="Research note helper",
                triggers=["research", "source"],
            )
            loaded = agent.skill_loader.load_skill("research-note")

            self.assertEqual("research-note", manifest.name)
            self.assertTrue(manifest.agent_created)
            self.assertTrue(manifest.agent_can_update)
            self.assertIn("Summarize research notes", loaded.instructions)
            self.assertTrue((Path(tmp) / "skills" / "research-note" / "skill.toml").exists())

    def test_agent_updates_only_skills_that_allow_agent_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _make_agent(root)
            _write_skill(root, "human", agent_created=False, agent_can_update=False)
            agent.create_skill("agent-made", instructions="Old instructions.")

            with self.assertRaises(PermissionError):
                agent.update_skill("human", instructions="Should not change.")

            updated = agent.update_skill("agent-made", instructions="New instructions.", triggers=["new"])

            self.assertEqual(["new"], updated.triggers)
            self.assertEqual("New instructions.", agent.skill_loader.load_skill("agent-made").instructions)

    def test_agent_optimizes_skill_with_model_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = MockProvider("Optimized instructions.")
            agent = _make_agent(Path(tmp), provider=provider)
            agent.create_skill("agent-made", instructions="Old instructions.")

            updated = agent.optimize_skill("agent-made", goal="make it clearer")

            self.assertEqual("agent-made", updated.name)
            self.assertEqual("Optimized instructions.", agent.skill_loader.load_skill("agent-made").instructions)
            self.assertIn("make it clearer", provider.last_messages[-1]["content"])


def _make_agent(root: Path, provider: MockProvider | None = None) -> Agent:
    config_path = root / "agent.toml"
    config_path.write_text(
        """
[agent]
name = "demo"
system = "Base system."
workflow = "direct"
skills = []

[model]
provider = "mock"
model = "unit-test"

[paths]
skills = ["skills"]
memory = ".super-agent/memory"
""".strip(),
        encoding="utf-8",
    )
    return Agent(AgentConfig.load_from_file(config_path), provider=provider or MockProvider("ok"))


def _write_skill(root: Path, name: str, *, agent_created: bool, agent_can_update: bool) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
name = "{name}"
description = "{name} helper"
agent_created = {str(agent_created).lower()}
agent_can_update = {str(agent_can_update).lower()}
triggers = ["{name}"]

[entry]
instructions = "SKILL.md"
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text("Original instructions.", encoding="utf-8")

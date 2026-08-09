import json
import tempfile
import unittest
from pathlib import Path

from super_agent import Agent
from core.config import CommonConfig
from core.state.events import create_local_event_store
from core.provider import MockProvider
from skill.disclosure import ProgressiveDisclosureCore
from skill.runtime.defaults import create_runtime_disclosure_recorder
from support import write_workflow_skill


class ProgressiveDisclosureTests(unittest.TestCase):
    def test_disclosure_writes_index_instruction_cache_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "echo", "Echo helper", "Always answer briefly.")
            store = create_local_event_store(root / ".super-agent")
            disclosure = ProgressiveDisclosureCore(
                [root / "skills"],
                recorder=create_runtime_disclosure_recorder(store),
            )

            index = disclosure.prepare_skill_index()
            instruction = disclosure.open_skill(
                "echo",
                "prompt",
            ).disclose_instructions()

            index_data = json.loads(index.index_path.read_text(encoding="utf-8"))
            history = disclosure.read_disclosure_history()

            self.assertEqual("prompt:echo", index.entries[0].reference.key)
            self.assertTrue(instruction.cache_path.exists())
            self.assertEqual(
                "Always answer briefly.",
                disclosure.read_disclosed_content(instruction.cache_path).content,
            )
            self.assertEqual("echo", index_data["skills"][0]["name"])
            self.assertEqual(6, index_data["schema_version"])
            self.assertEqual("project", index_data["skills"][0]["source"])
            self.assertFalse(index_data["skills"][0]["agent_created"])
            self.assertTrue(index_data["skills"][0]["agent_can_update"])
            self.assertEqual(70.0, index_data["skills"][0]["freshness"])
            self.assertEqual("echo", index_data["skills"][0]["function_group"])
            self.assertEqual(["index", "instructions"], [item.stage for item in history])

    def test_agent_exposes_disclosure_cache_paths_to_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            _write_skill(root, "echo", "Echo helper", "Use cached context.")
            config_path = _write_config(root)
            provider = MockProvider("ok")

            agent = Agent(
                CommonConfig.load_from_file(config_path),
                provider=provider,
                use_storage=True,
            )
            result = agent.run("echo hello")

            content = provider.last_messages[0]["content"]
            self.assertEqual(
                ["memory:default", "prompt:echo", "workflow:direct"],
                result.skills,
            )
            self.assertIn("Progressive skill disclosure", content)
            self.assertIn("history.json", content)
            self.assertIn("skills/prompt/echo/manifest.json", content)
            self.assertTrue(
                agent._setup.create_event_store()
                .disclosure.cache_root.joinpath("index.json")
                .exists()
            )


def _write_skill(root: Path, name: str, description: str, instruction: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
type = "prompt"
description = "{description}"

""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(instruction, encoding="utf-8")


def _write_config(root: Path) -> Path:
    config_path = root / "common.toml"
    config_path.write_text(
        """
schema_version = 1
kind = "common"

[agent]
name = "demo"
system = "Base system."
skills = ["workflow:direct", "memory:default", "echo"]

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return config_path

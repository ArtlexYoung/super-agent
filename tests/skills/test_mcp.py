import tempfile
import unittest
from pathlib import Path

from core import Agent, AgentConfig
from core.provider import MockProvider
from skill import SkillLoader
from support import write_memory_skill, write_workflow_skill


class McpSkillTests(unittest.TestCase):
    def test_skill_loader_registers_mcp_server_as_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mcp_server(root, "filesystem", "Filesystem MCP", "npx", ["-y", "@mcp/server-filesystem"])

            loader = SkillLoader([root / "skills"])
            skill = loader.load_skill("filesystem")
            selected = loader.load_skills_for_prompt("please inspect filesystem")

            self.assertEqual("filesystem", skill.manifest.name)
            self.assertEqual("mcp", skill.manifest.kind)
            self.assertIn("Protocol: mcp", skill.instructions)
            self.assertIn("Command: npx -y @mcp/server-filesystem", skill.instructions)
            self.assertEqual(["filesystem"], [item.manifest.name for item in selected])

    def test_mcp_skill_instruction_lists_env_names_without_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mcp_server(
                root,
                "github",
                "GitHub MCP",
                "npx",
                ["-y", "@mcp/server-github"],
                env={"GITHUB_TOKEN": "secret-token"},
            )

            skill = SkillLoader([root / "skills"]).load_skill("github")

            self.assertIn("Environment variables: GITHUB_TOKEN", skill.instructions)
            self.assertNotIn("secret-token", skill.instructions)

    def test_mcp_skill_requires_mcp_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server_dir = root / "skills" / "mcp" / "bad"
            server_dir.mkdir(parents=True)
            (server_dir / "skill.toml").write_text(
                """
name = "bad"
kind = "mcp"
description = "Missing mcp table"
transport = "stdio"
command = "npx"
args = ["-y", "@mcp/server-bad"]

[entry]
instructions = "SKILL.md"
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                SkillLoader([root / "skills"]).load_skill("bad")

    def test_config_can_disable_whole_mcp_feature_by_name_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            _write_mcp_server(root, "github", "GitHub MCP", "npx", ["-y", "@mcp/server-github"])
            config_path = _write_agent_config(root, disable_names=["mcp"])
            provider = MockProvider("ok")

            result = Agent(AgentConfig.load_from_file(config_path), provider=provider).run("github")

            self.assertEqual([], result.skills)
            self.assertNotIn("GitHub MCP", provider.last_messages[0]["content"])

    def test_config_can_disable_memory_and_named_skills_in_one_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            write_memory_skill(root)
            _write_skill(root, "echo", "Echo helper", "Use echo skill.")
            _write_mcp_server(root, "github", "GitHub MCP", "npx", ["-y", "@mcp/server-github"])
            memory_dir = root / ".super-agent" / "memory"
            memory_dir.mkdir(parents=True)
            (memory_dir / "memory.md").write_text("- Keep answers short.\n", encoding="utf-8")
            config_path = _write_agent_config(
                root,
                skills=["echo", "github"],
                disable_names=["memory:default", "echo", "mcp:github"],
            )
            provider = MockProvider("ok")

            result = Agent(AgentConfig.load_from_file(config_path), provider=provider).run("echo github")

            system_prompt = provider.last_messages[0]["content"]
            self.assertEqual([], result.skills)
            self.assertNotIn("Keep answers short.", system_prompt)
            self.assertNotIn("Use echo skill.", system_prompt)
            self.assertNotIn("GitHub MCP", system_prompt)
            self.assertFalse((memory_dir / "habits.json").exists())


def _write_mcp_server(
    root: Path,
    name: str,
    description: str,
    command: str,
    args: list[str],
    env: dict[str, str] | None = None,
) -> None:
    server_dir = root / "skills" / "mcp" / name
    server_dir.mkdir(parents=True)
    args_text = ", ".join(f'"{item}"' for item in args)
    env_text = ""
    if env:
        env_lines = "\n".join(f'{key} = "{value}"' for key, value in env.items())
        env_text = f"\n[mcp.env]\n{env_lines}"
    (server_dir / "skill.toml").write_text(
        f"""
name = "{name}"
kind = "mcp"
description = "{description}"
version = "0.1.0"
triggers = ["{name}"]

[entry]
instructions = "SKILL.md"

[mcp]
transport = "stdio"
command = "{command}"
args = [{args_text}]
{env_text}
""".strip(),
        encoding="utf-8",
    )
    (server_dir / "SKILL.md").write_text("Use this MCP skill when needed.", encoding="utf-8")


def _write_skill(root: Path, name: str, description: str, instruction: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
name = "{name}"
description = "{description}"
version = "0.1.0"
triggers = ["{name}"]

[entry]
instructions = "SKILL.md"
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(instruction, encoding="utf-8")


def _write_agent_config(
    root: Path,
    *,
    skills: list[str] | None = None,
    use_features: list[str] | None = None,
    disable_names: list[str] | None = None,
) -> Path:
    config_path = root / "agent.toml"
    skills_text = _toml_list(skills or [])
    use_features_line = "" if use_features is None else f"use_features = {_toml_list(use_features)}"
    disable_names_line = "" if disable_names is None else f"disable_names = {_toml_list(disable_names)}"
    config_path.write_text(
        f"""
[agent]
name = "demo"
system = "Base system."
workflow = "direct"
memory = "default"
skills = {skills_text}
{use_features_line}
{disable_names_line}

[model]
provider = "mock"
model = "unit-test"

[paths]
skills = ["skills"]
memory = ".super-agent/memory"
""".strip(),
        encoding="utf-8",
    )
    return config_path


def _toml_list(items: list[str]) -> str:
    return "[" + ", ".join(f'"{item}"' for item in items) + "]"

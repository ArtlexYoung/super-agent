import tempfile
import unittest
import json
import sys
from pathlib import Path

from agents.agent import Agent
from runtime.config import AgentConfig
from runtime.store import create_local_runtime_store
from provider.chat import MockProvider
from capability.skill_executors import create_builtin_skill_executors, load_skill_for_model_context
from skill.disclosure import ProgressiveDisclosureCore
from skill.kinds.mcp import create_mcp_server_from_skill_disclosure
from skill.kinds.memory import MiniMemory
from support import write_memory_skill, write_workflow_skill


class McpSkillTests(unittest.TestCase):
    def test_stdio_mcp_lists_and_calls_real_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server_script = root / "fake_mcp.py"
            _write_fake_mcp_server(server_script)
            _write_mcp_server(
                root,
                "calculator",
                "Calculator MCP",
                sys.executable,
                [str(server_script)],
                env={"MCP_TEST_VALUE": "from-env"},
            )
            disclosure = _prepare_disclosure(root)
            server = create_mcp_server_from_skill_disclosure(
                disclosure.open_skill("calculator", "mcp")
            )

            tools = server.list_tools()
            result = server.call_tool("add", {"left": 2, "right": 3})

            self.assertEqual("add", tools[0]["name"])
            self.assertEqual(5, result["structuredContent"]["sum"])
            self.assertEqual("from-env", result["structuredContent"]["env"])

    def test_skill_loader_registers_mcp_server_as_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mcp_server(root, "filesystem", "Filesystem MCP", "npx", ["-y", "@mcp/server-filesystem"])

            disclosure = _prepare_disclosure(root)
            selected = disclosure.select_skill_references_for_prompt(
                "please inspect filesystem",
                allowed_capabilities={"prompt", "mcp"},
            )
            skill = load_skill_for_model_context(
                disclosure,
                selected[0],
                create_builtin_skill_executors(),
                disclosure.store,
            )

            self.assertEqual("filesystem", skill.manifest.name)
            self.assertEqual("mcp", skill.manifest.capability)
            self.assertIn("Protocol: mcp", skill.instructions)
            self.assertIn("Command: npx -y @mcp/server-filesystem", skill.instructions)
            self.assertEqual(["mcp:filesystem"], [item.key for item in selected])

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

            disclosure = _prepare_disclosure(root)
            skill = load_skill_for_model_context(
                disclosure,
                disclosure.prepare_skill_index().require_skill("github", "mcp").reference,
                create_builtin_skill_executors(),
                disclosure.store,
            )

            self.assertIn("Environment variables: GITHUB_TOKEN", skill.instructions)
            self.assertNotIn("secret-token", skill.instructions)

    def test_mcp_executor_requires_command_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server_dir = root / "skills" / "mcp" / "bad"
            server_dir.mkdir(parents=True)
            (server_dir / "skill.toml").write_text(
                """
schema_version = 2
name = "bad"
capability = "mcp"
description = "Missing mcp table"
version = "0.1.0"
triggers = ["bad"]

[entry]
instructions = "SKILL.md"
""".strip(),
                encoding="utf-8",
            )

            disclosure = _prepare_disclosure(root)

            with self.assertRaisesRegex(ValueError, "configuration.command cannot be empty"):
                create_mcp_server_from_skill_disclosure(
                    disclosure.open_skill("bad", "mcp")
                )

    def test_mcp_configuration_rejects_invalid_types_without_starting_server(self) -> None:
        invalid_configurations = {
            "transport": ('transport = "http"\ncommand = "echo"', "transport"),
            "command": ("command = 1", "command must be a string"),
            "args": ('command = "echo"\nargs = "bad"', "args must be a string array"),
            "env": ('command = "echo"\nenv = ["bad"]', "env must be a table"),
        }
        for name, (configuration, message) in invalid_configurations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _write_raw_mcp_configuration(root, name, configuration)
                disclosure = _prepare_disclosure(root)

                with self.assertRaisesRegex(ValueError, message):
                    create_mcp_server_from_skill_disclosure(
                        disclosure.open_skill(name, "mcp")
                    )

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
            memory = MiniMemory(
                create_local_runtime_store(root / ".super-agent", agent_name="demo")
            )
            memory.add_memory_item("Keep answers short.")
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
            self.assertEqual(0, memory.usage_habits.read_usage_habits()["total_runs"])


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
        env_text = f"\n[configuration.env]\n{env_lines}"
    (server_dir / "skill.toml").write_text(
        f"""
schema_version = 2
name = "{name}"
capability = "mcp"
description = "{description}"
version = "0.1.0"
triggers = ["{name}"]

[entry]
instructions = "SKILL.md"

[configuration]
transport = "stdio"
command = "{command}"
args = [{args_text}]
{env_text}
""".strip(),
        encoding="utf-8",
    )
    (server_dir / "SKILL.md").write_text("Use this MCP skill when needed.", encoding="utf-8")


def _write_raw_mcp_configuration(root: Path, name: str, configuration: str) -> None:
    server_dir = root / "skills" / "mcp" / name
    server_dir.mkdir(parents=True)
    (server_dir / "skill.toml").write_text(
        f"""
schema_version = 2
name = "{name}"
capability = "mcp"
description = "Invalid MCP configuration"
version = "0.1.0"
triggers = ["{name}"]

[configuration]
{configuration}
""".strip(),
        encoding="utf-8",
    )


def _write_skill(root: Path, name: str, description: str, instruction: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
schema_version = 2
name = "{name}"
capability = "prompt"
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

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return config_path


def _toml_list(items: list[str]) -> str:
    return "[" + ", ".join(f'"{item}"' for item in items) + "]"


def _write_fake_mcp_server(path: Path) -> None:
    path.write_text(
        """
import json
import os
import sys

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if "id" not in request:
        continue
    if method == "initialize":
        result = {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1"},
        }
    elif method == "tools/list":
        result = {
            "tools": [{
                "name": "add",
                "description": "Add two numbers",
                "inputSchema": {"type": "object"},
            }]
        }
    elif method == "tools/call":
        arguments = request["params"]["arguments"]
        result = {
            "content": [{"type": "text", "text": str(arguments["left"] + arguments["right"])}],
            "structuredContent": {
                "sum": arguments["left"] + arguments["right"],
                "env": os.environ.get("MCP_TEST_VALUE"),
            },
        }
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -1, "message": method}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
""".strip(),
        encoding="utf-8",
    )


def _prepare_disclosure(root: Path) -> ProgressiveDisclosureCore:
    disclosure = ProgressiveDisclosureCore(
        [root / "skills"],
        create_local_runtime_store(root / "state"),
    )
    disclosure.prepare_skill_index()
    return disclosure

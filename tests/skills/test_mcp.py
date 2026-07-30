import tempfile
import unittest
import json
import sys
from pathlib import Path

from core import __version__
from core.checks import ActionEffect
from super_agent import Agent
from skill.loaders.defaults import create_default_skill_loaders
from skill.loaders.registry import SkillLoadRequest
from core.config import AgentConfig
from skill.state.events import create_local_event_store
from core.provider.chat import MockProvider
from skill.task.preflight import TaskPreflightError
from skill.disclosure import ProgressiveDisclosureCore, SkillReference
from skill.loaders.mcp import read_mcp_skill_settings
from skill.state.memory_service import MiniMemory
from skill.manifest import Skill
from skill.loaders.mcp import McpServers, StdioMcpServer
from support import write_memory_skill, write_workflow_skill


class McpSkillTests(unittest.TestCase):
    def test_stdio_mcp_lists_and_calls_real_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server_script = root / "fake_mcp.py"
            _write_fake_mcp_server(server_script)
            server = StdioMcpServer(
                sys.executable,
                arguments=(str(server_script),),
                environment={"MCP_TEST_VALUE": "from-env"},
            )

            tools = server.list_tools()
            result = server.call_tool("add", {"left": 2, "right": 3})

            self.assertEqual("add", tools[0]["name"])
            self.assertEqual(5, result["structuredContent"]["sum"])
            self.assertEqual("from-env", result["structuredContent"]["env"])
            self.assertEqual(__version__, result["structuredContent"]["client_version"])

    def test_skill_loader_uses_only_a_code_registered_mcp_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mcp_skill(root, "filesystem", "Filesystem MCP")
            servers = _registered_servers("filesystem")

            disclosure = _prepare_disclosure(root)
            selected = disclosure.select_skill_references(
                ["mcp:filesystem"],
                allowed_types={"prompt", "mcp"},
            )
            skill = _load_model_context(disclosure, selected[0], servers)

            self.assertEqual("filesystem", skill.manifest.name)
            self.assertEqual("mcp", skill.manifest.skill_type)
            self.assertIn("Use this MCP Skill when needed.", skill.instructions)
            self.assertIn("Registered MCP server: filesystem", skill.instructions)
            self.assertNotIn("Command:", skill.instructions)
            self.assertEqual(["mcp:filesystem"], [item.key for item in selected])

    def test_mcp_skill_does_not_disclose_registered_command_or_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mcp_skill(root, "github", "GitHub MCP")
            servers = McpServers()
            servers.add_mcp_server(
                "github",
                StdioMcpServer(
                    "private-command",
                    environment={"GITHUB_TOKEN": "secret-token"},
                ),
                effects=(ActionEffect.EXECUTE, ActionEffect.NETWORK),
            )

            disclosure = _prepare_disclosure(root)
            skill = _load_model_context(
                disclosure,
                disclosure.prepare_skill_index().require_skill(
                    "github", "mcp"
                ).reference,
                servers,
            )

            self.assertNotIn("private-command", skill.instructions)
            self.assertNotIn("GITHUB_TOKEN", skill.instructions)
            self.assertNotIn("secret-token", skill.instructions)
            locked = servers.list_code_registrations()[0]
            self.assertEqual(["execute", "network"], locked["effects"])
            self.assertNotIn("secret-token", str(locked))

    def test_mcp_tools_are_loaded_with_their_specific_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mcp_skill(root, "alpha", "Alpha MCP")
            _write_mcp_skill(root, "beta", "Beta MCP")
            disclosure = _prepare_disclosure(root)
            index = disclosure.prepare_skill_index()
            servers = _registered_servers("alpha", "beta")
            registry = create_default_skill_loaders(servers)

            alpha = registry.load_skill(
                SkillLoadRequest(
                    disclosure,
                    index.require_skill("alpha", "mcp").reference,
                )
            )
            beta = registry.load_skill(
                SkillLoadRequest(
                    disclosure,
                    index.require_skill("beta", "mcp").reference,
                )
            )

            alpha_names = {tool.name for tool in alpha.tools}
            beta_names = {tool.name for tool in beta.tools}
            self.assertEqual({"mcp_alpha_list", "mcp_alpha_run"}, alpha_names)
            self.assertEqual({"mcp_beta_list", "mcp_beta_run"}, beta_names)
            self.assertFalse(alpha_names & beta_names)
            self.assertIn("mcp_alpha_run", alpha.model_context.instructions)
            self.assertEqual(
                (ActionEffect.EXECUTE,),
                alpha.tools[0].action.effects,
            )

    def test_mcp_skill_loader_requires_matching_code_registration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server_dir = root / "skills" / "mcp" / "bad"
            server_dir.mkdir(parents=True)
            (server_dir / "skill.toml").write_text(
                """
schema_version = 3
name = "bad"
type = "mcp"
description = "Missing mcp table"
version = "0.1.0"

[entry]
instructions = "SKILL.md"
""".strip(),
                encoding="utf-8",
            )

            disclosure = _prepare_disclosure(root)

            with self.assertRaisesRegex(KeyError, "not registered in code"):
                create_default_skill_loaders().load_skill(
                    SkillLoadRequest(
                        disclosure,
                        disclosure.prepare_skill_index().require_skill(
                            "bad", "mcp"
                        ).reference,
                    )
                )

    def test_missing_mcp_registration_fails_preflight_before_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            _write_mcp_skill(root, "missing", "Missing MCP registration")
            config = AgentConfig.load_from_file(
                _write_agent_config(root, skills=["mcp:missing"])
            )
            provider = MockProvider("must not run")

            with self.assertRaises(TaskPreflightError) as raised:
                Agent(config, provider=provider).run("use missing")

            self.assertIn("response_contract", provider.last_messages[-1]["content"])
            self.assertEqual([], provider.tool_requests)
            problem = next(
                item for item in raised.exception.problems if item.target == "mcp:missing"
            )
            self.assertEqual("skill_invalid", problem.code)
            self.assertIn("not registered in code", problem.message)

    def test_mcp_skill_rejects_executable_connection_settings(self) -> None:
        invalid_configurations = {
            "transport": ('transport = "stdio"', "unknown MCP Skill settings"),
            "command": ('command = "echo"', "unknown MCP Skill settings"),
            "arguments": ('arguments = ["bad"]', "unknown MCP Skill settings"),
            "environment": ('environment = { TOKEN = "secret" }', "unknown MCP Skill settings"),
            "server": ("server = 1", "server must be a non-empty string"),
        }
        for name, (configuration, message) in invalid_configurations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _write_raw_mcp_configuration(root, name, configuration)
                disclosure = _prepare_disclosure(root)

                with self.assertRaisesRegex(ValueError, message):
                    read_mcp_skill_settings(
                        disclosure.open_skill(name, "mcp")
                    )

    def test_runtime_lock_records_registered_mcp_code_and_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            _write_mcp_skill(root, "calculator", "Calculator MCP")
            config = AgentConfig.load_from_file(
                _write_agent_config(root, skills=["mcp:calculator"])
            )
            agent = Agent(config, provider=MockProvider("ok"), use_storage=True)
            agent.add_mcp_server(
                "calculator",
                _FakeMcpServer(),
                effects=(ActionEffect.EXECUTE,),
            )

            result = agent.run("use calculator")

            runtime_lock = agent.runtime.create_event_store().read_runtime_lock(result.run_id)
            registered = runtime_lock["registered_code"]
            self.assertEqual(18, runtime_lock["schema_version"])
            self.assertEqual("calculator", registered[0]["name"])
            self.assertEqual(["execute"], registered[0]["effects"])
            self.assertEqual("mcp_server", registered[0]["kind"])

    def test_config_can_disable_whole_mcp_feature_by_name_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            _write_mcp_skill(root, "github", "GitHub MCP")
            config_path = _write_agent_config(root, disabled_skills=["mcp"])
            provider = MockProvider("ok")

            result = Agent(
                AgentConfig.load_from_file(config_path),
                provider=provider,
                use_storage=True,
            ).run("github")

            self.assertEqual(["common"], result.skills)
            self.assertNotIn("GitHub MCP", provider.last_messages[0]["content"])

    def test_config_can_disable_memory_and_named_skills_in_one_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_workflow_skill(root)
            write_memory_skill(root)
            _write_skill(root, "echo", "Echo helper", "Use echo skill.")
            _write_mcp_skill(root, "github", "GitHub MCP")
            memory = MiniMemory(
                create_local_event_store(root / ".super-agent", agent_name="demo")
            )
            memory.add_long_term_memory("Keep answers short.")
            config_path = _write_agent_config(
                root,
                skills=["echo", "github"],
                disabled_skills=["memory:default", "echo", "mcp:github"],
            )
            provider = MockProvider("ok")

            result = Agent(
                AgentConfig.load_from_file(config_path),
                provider=provider,
                use_storage=True,
            ).run("echo github")

            system_prompt = provider.last_messages[0]["content"]
            self.assertEqual(["common"], result.skills)
            self.assertNotIn("Keep answers short.", system_prompt)
            self.assertNotIn("Use echo skill.", system_prompt)
            self.assertNotIn("GitHub MCP", system_prompt)
            self.assertEqual(0, memory.usage_habits.read_usage_habits()["total_runs"])


def _write_mcp_skill(
    root: Path,
    name: str,
    description: str,
    server_name: str | None = None,
) -> None:
    server_dir = root / "skills" / "mcp" / name
    server_dir.mkdir(parents=True)
    configuration = (
        ""
        if server_name is None
        else f'\n[configuration]\nserver = "{server_name}"'
    )
    (server_dir / "skill.toml").write_text(
        f"""
schema_version = 3
name = "{name}"
type = "mcp"
description = "{description}"
version = "0.1.0"

[entry]
instructions = "SKILL.md"

{configuration}
""".strip(),
        encoding="utf-8",
    )
    (server_dir / "SKILL.md").write_text(
        "Use this MCP Skill when needed.",
        encoding="utf-8",
    )


def _write_raw_mcp_configuration(root: Path, name: str, configuration: str) -> None:
    server_dir = root / "skills" / "mcp" / name
    server_dir.mkdir(parents=True)
    (server_dir / "skill.toml").write_text(
        f"""
schema_version = 3
name = "{name}"
type = "mcp"
description = "Invalid MCP configuration"
version = "0.1.0"

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
schema_version = 3
name = "{name}"
type = "prompt"
description = "{description}"
version = "0.1.0"

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
    disabled_skills: list[str] | None = None,
) -> Path:
    config_path = root / "agent.toml"
    skills_text = _toml_list(["workflow:direct", "memory:default", *(skills or [])])
    disabled_skills_line = "" if disabled_skills is None else f"disabled_skills = {_toml_list(disabled_skills)}"
    config_path.write_text(
        f"""
[agent]
name = "demo"
system = "Base system."
skills = {skills_text}
{disabled_skills_line}

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


def _load_model_context(
    disclosure: ProgressiveDisclosureCore,
    reference: SkillReference,
    servers: McpServers,
) -> Skill:
    contribution = create_default_skill_loaders(servers).load_skill(
        SkillLoadRequest(disclosure, reference)
    )
    if contribution.model_context is None:
        raise AssertionError("MCP SkillLoader did not provide model context")
    return contribution.model_context


class _FakeMcpServer:
    def list_tools(self) -> list[dict[str, object]]:
        return [{"name": "fake", "inputSchema": {"type": "object"}}]

    def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        return {"name": name, "arguments": arguments}


def _registered_servers(*names: str) -> McpServers:
    servers = McpServers()
    for name in names:
        servers.add_mcp_server(
            name,
            _FakeMcpServer(),
            effects=(ActionEffect.EXECUTE,),
        )
    return servers


def _write_fake_mcp_server(path: Path) -> None:
    path.write_text(
        """
import json
import os
import sys

client_version = None
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if "id" not in request:
        continue
    if method == "initialize":
        client_version = request["params"]["clientInfo"]["version"]
        result = {
            "protocolVersion": "2025-03-26",
            "skill_loaders": {"tools": {}},
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
                "client_version": client_version,
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
    )
    disclosure.prepare_skill_index()
    return disclosure

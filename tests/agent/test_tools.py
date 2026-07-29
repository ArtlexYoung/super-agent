import sys
import tempfile
import unittest
from pathlib import Path

from skill.runners.defaults import (
    create_default_skill_runners,
    create_runtime_disclosure_recorder,
)
from skill.runners.registry import SkillLoadRequest
from skill.runners.loaded import (
    SkillAction,
    SkillTool,
    LoadedSkill,
)
from skill.runners.builtins import create_memory_skill_contribution
from core.provider.chat import ToolCall
from core.provider.chat import MockProvider
from skill.task.tools import RuntimeTools, RuntimeToolsContext
from core.config import AgentConfig
from skill.task.run import Run
from core.models import RunIdentity
from core.checks import ActionConfirmationRequired, ActionEffect
from core.state.event_log import RunEventLog
from skill.state.store import RuntimeStore
from core.storage import JsonlStorage
from skill.disclosure import ProgressiveDisclosureCore
from skill.kinds.memory import MiniMemory
from skill.kinds.model import create_direct_provider_profile
from skill.runners.mcp import McpServers, StdioMcpServer


class SkillToolsTests(unittest.TestCase):
    def test_model_discloses_then_activates_one_skill_on_demand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "research")
            session = _create_session(root)
            disclosure = _create_disclosure(session)
            index = session.skill_index
            tools = _create_tool_router(disclosure, index, session)

            listed = tools.run_tool_call(ToolCall("call-1", "list_skills", {}))
            read = tools.run_tool_call(
                ToolCall(
                    "call-2",
                    "disclose_skill_instructions",
                    {"name": "research", "type": "prompt"},
                )
            )
            self.assertEqual([], tools.used_skill_names)
            activated = tools.run_tool_call(
                ToolCall(
                    "call-3",
                    "activate_skill",
                    {"name": "research", "type": "prompt"},
                )
            )

            self.assertEqual("research", listed["skills"][0]["name"])
            self.assertEqual("Research carefully.", read["instructions"])
            self.assertEqual("Research carefully.", activated["model_context"])
            self.assertEqual(["research"], tools.used_skill_names)
            event_types = [
                event.event_type
                for event in session.store.read_run_events(session.run_id)
            ]
            self.assertIn("tool.requested", event_types)
            self.assertIn("action.checked", event_types)
            self.assertIn("action.applied", event_types)
            self.assertIn("tool.completed", event_types)
            self.assertIn("skill.disclosed", event_types)

    def test_reading_a_cached_path_does_not_activate_the_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "research")
            session = _create_session(root)
            disclosure = _create_disclosure(session)
            index = session.skill_index
            cached = disclosure.open_skill(
                "research",
                "prompt",
            ).disclose_instructions()
            tools = _create_tool_router(disclosure, index, session)

            tools.run_tool_call(
                ToolCall(
                    "call-1",
                    "read_disclosed_content",
                    {"cache_path": str(cached.cache_path)},
                )
            )

            evidence = tools.context.session.list_used_skill_evidence()
            self.assertEqual([], evidence)
            self.assertEqual([], tools.used_skill_names)

    def test_unknown_builtin_tool_fails_with_trace_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = _create_session(root)
            disclosure = _create_disclosure(session)
            index = session.skill_index
            tools = _create_tool_router(disclosure, index, session)

            with self.assertRaisesRegex(KeyError, "unknown runtime tool"):
                tools.run_tool_call(ToolCall("bad", "unknown", {}))

            self.assertEqual(
                "tool.failed",
                session.store.read_run_events(session.run_id)[-1].event_type,
            )

    def test_model_uses_explicit_temporary_and_long_term_memory_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = _create_session(root, conversation_id="conversation-a")
            disclosure = _create_disclosure(session)
            index = session.skill_index
            memory = MiniMemory(
                session.store,
                session.identity,
                execute_action=session.execute_action,
            )
            tools = _create_tool_router(disclosure, index, session, memory)

            definitions = {
                item["function"]["name"] for item in tools.get_tool_definitions()
            }
            temporary = tools.run_tool_call(
                ToolCall(
                    "add-temporary",
                    "add_temporary_memory",
                    {"text": "Python note for this conversation.", "scope": "agent"},
                )
            )
            long_term = tools.run_tool_call(
                ToolCall(
                    "add-long-term",
                    "add_long_term_memory",
                    {"text": "User prefers Python.", "scope": "agent"},
                )
            )
            recalled = tools.run_tool_call(
                ToolCall(
                    "recall",
                    "recall_memory",
                    {"query": "Python", "scope": "agent"},
                )
            )
            tools.run_tool_call(
                ToolCall(
                    "forget",
                    "forget_memory",
                    {"item_id": temporary["item"]["item_id"]},
                )
            )

            self.assertTrue(
                {
                    "list_memory_items",
                    "add_temporary_memory",
                    "add_long_term_memory",
                    "recall_memory",
                    "prepare_memory_organization",
                    "apply_memory_organization",
                    "forget_memory",
                    "consolidate_memory",
                }
                <= definitions
            )
            self.assertEqual(2, len(recalled["items"]))
            self.assertEqual(
                ["User prefers Python."],
                [item.text for item in memory.list_memory_items()],
            )
            self.assertEqual("temporary", temporary["item"]["memory_type"])
            self.assertEqual("conversation-a", temporary["item"]["conversation_id"])
            self.assertEqual("long_term", long_term["item"]["memory_type"])
            self.assertEqual(session.run_id, temporary["item"]["source_run_id"])

    def test_standard_policy_blocks_external_tool_before_handler_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = _create_session(root)
            disclosure = _create_disclosure(session)
            index = session.skill_index
            called = False

            def run_external(arguments: dict[str, object]) -> dict[str, object]:
                nonlocal called
                called = True
                return {"ok": True}

            tools = RuntimeTools(
                RuntimeToolsContext(session=session),
                contributions=[
                    LoadedSkill(
                        tools=(
                            SkillTool(
                                "run_external",
                                "Run an external operation.",
                                {},
                                run_external,
                                action=SkillAction(
                                    (ActionEffect.EXECUTE, ActionEffect.NETWORK),
                                    "mcp:untrusted",
                                ),
                            ),
                        )
                    )
                ],
            )

            with self.assertRaises(ActionConfirmationRequired):
                tools.run_tool_call(ToolCall("external", "run_external", {}))

            self.assertFalse(called)
            event_types = [
                event.event_type
                for event in session.store.read_run_events(session.run_id)
            ]
            self.assertIn("action.blocked", event_types)
            self.assertNotIn("action.applied", event_types)

    def test_mcp_skill_cannot_start_process_before_runtime_action_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "mcp-started.txt"
            script = root / "untrusted_mcp.py"
            script.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('started', encoding='utf-8')\n",
                encoding="utf-8",
            )
            _write_mcp_skill(root)
            mcp_servers = McpServers()
            mcp_servers.add_mcp_server(
                "untrusted",
                StdioMcpServer(
                    sys.executable,
                    arguments=(str(script),),
                ),
                effects=(ActionEffect.EXECUTE, ActionEffect.NETWORK),
            )
            session = _create_session(root, mcp_servers=mcp_servers)
            disclosure = _create_disclosure(session)
            index = session.skill_index
            contribution = session.skill_runners.load_skill(
                SkillLoadRequest(
                    disclosure,
                    index.require_skill("untrusted", "mcp").reference,
                    session.store,
                    session.identity,
                    execute_action=session.execute_action,
                )
            )
            tools = RuntimeTools(
                RuntimeToolsContext(session=session),
                contributions=[contribution],
            )

            with self.assertRaises(ActionConfirmationRequired):
                tools.run_tool_call(
                    ToolCall("mcp-call", "mcp_untrusted_list", {})
                )

            self.assertFalse(marker.exists())
            events = session.store.read_run_events(session.run_id)
            self.assertEqual(
                "action.blocked",
                next(
                    event.event_type
                    for event in reversed(events)
                    if event.event_type.startswith("action.")
                ),
            )


def _write_prompt_skill(root: Path, name: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.toml").write_text(
        f"""
schema_version = 3
name = "{name}"
type = "prompt"
description = "Research helper"
version = "0.1.0"
triggers = ["never-match"]

[entry]
instructions = "SKILL.md"
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text("Research carefully.", encoding="utf-8")


def _write_mcp_skill(root: Path) -> None:
    skill_dir = root / "skills" / "mcp" / "untrusted"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("skill.toml").write_text(
        f'''schema_version = 3
name = "untrusted"
type = "mcp"
description = "Untrusted MCP server"
version = "0.1.0"
triggers = ["untrusted"]

[configuration]
server = "untrusted"
'''.strip(),
        encoding="utf-8",
    )


def _create_disclosure(session: Run) -> ProgressiveDisclosureCore:
    return session.skill_disclosure


def _create_tool_router(
    disclosure: ProgressiveDisclosureCore,
    index,
    session: Run,
    memory: MiniMemory | None = None,
) -> RuntimeTools:
    if disclosure is not session.skill_disclosure or index is not session.skill_index:
        raise ValueError("tool router must use the Run Skill snapshot")
    return RuntimeTools(
        RuntimeToolsContext(
            session=session,
        ),
        contributions=(
            []
            if memory is None
            else [create_memory_skill_contribution(memory)]
        ),
    )


def _create_session(
    root: Path,
    conversation_id: str | None = None,
    *,
    mcp_servers: McpServers | None = None,
) -> Run:
    config = AgentConfig.create_default(root)
    provider = MockProvider()
    identity = RunIdentity.create(
        "local",
        config.agent.name,
        conversation_id=conversation_id,
    )
    backend = JsonlStorage(root / "state")
    event_log = RunEventLog(identity, backend=backend)
    store = RuntimeStore(
        backend,
        root / "state",
        "local",
        config.agent.name,
        run_event_log=event_log,
    )
    event_log.start_run("question")
    disclosure = ProgressiveDisclosureCore(
        [root / "skills"],
        recorder=create_runtime_disclosure_recorder(store, identity),
    )
    index = disclosure.prepare_skill_index()
    return Run(
        config=config,
        model_profile=create_direct_provider_profile(),
        provider=provider,
        skill_runners=create_default_skill_runners(mcp_servers),
        identity=identity,
        event_log=event_log,
        store=store,
        skill_disclosure=disclosure,
        skill_index=index,
    )

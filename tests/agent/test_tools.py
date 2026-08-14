import sys
import tempfile
import unittest
from pathlib import Path

from skill.handlers.runtime import (
    create_default_skill_handlers,
    create_runtime_disclosure_recorder,
)
from skill.handlers.runtime import (
    Skills,
    SkillContext,
    SkillAction,
    SkillTool,
    SkillUse,
)
from skill.handlers.builtins import create_memory_skill_contribution
from core.provider import ToolCall
from core.tools import RunTools
from core.config import CommonConfig
from core.runtime import Run
from core.models import RunIdentity, SubagentCallbacks, Task
from core.checks import ActionConfirmationRequired, ActionEffect, ActionRules
from core.records.events import RunEventLog
from core.records.store import EventStore
from adapter.storage_backends.storage import JsonlStorage
from adapter.storage_backends.storage import DisclosureStorage
from skill.discovery.catalog import ProgressiveDisclosureCore
from skill.handlers.memory import Memory
from skill.handlers.mcp import McpServers, StdioMcpServer


class SkillToolsTests(unittest.TestCase):
    def test_model_discloses_then_activates_one_skill_on_demand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "research")
            session = _create_session(root)
            disclosure = _create_disclosure(session)
            index = session.skills.index
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
            self.assertEqual(
                [
                    {
                        "key": "prompt:research",
                        "content": "Research carefully.",
                    }
                ],
                activated["instructions"],
            )
            self.assertEqual(["prompt:research"], tools.used_skill_names)
            event_types = [
                event.event_type
                for event in session.store.read_run_events(session.run_id)
            ]
            self.assertIn("tool.requested", event_types)
            self.assertIn("action.checked", event_types)
            self.assertIn("action.applied", event_types)
            self.assertIn("tool.completed", event_types)
            self.assertIn("content.disclosed", event_types)

    def test_reading_a_cached_path_does_not_activate_the_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "research")
            session = _create_session(root)
            disclosure = _create_disclosure(session)
            index = session.skills.index
            cached = disclosure.open_skill(
                "research",
                "prompt",
            ).disclose_instructions()
            tools = _create_tool_router(disclosure, index, session)

            tools.run_tool_call(
                ToolCall(
                    "call-1",
                    "read_disclosed_content",
                    {"reference": str(cached.cache_path)},
                )
            )

            evidence = tools.run.list_used_skill_evidence()
            self.assertEqual([], evidence)
            self.assertEqual([], tools.used_skill_names)

    def test_unknown_builtin_tool_fails_with_trace_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = _create_session(root)
            disclosure = _create_disclosure(session)
            index = session.skills.index
            tools = _create_tool_router(disclosure, index, session)

            with self.assertRaisesRegex(KeyError, "unknown runtime tool"):
                tools.run_tool_call(ToolCall("bad", "unknown", {}))

            self.assertEqual(
                "tool.failed",
                session.store.read_run_events(session.run_id)[-1].event_type,
            )

    def test_large_tool_output_uses_the_central_disclosure_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = _create_session(root)
            disclosure = _create_disclosure(session)
            tool = SkillTool(
                "run_subagent",
                "Return a large delegated result.",
                {},
                lambda arguments: {"text": "y" * 9_000},
                action=SkillAction((ActionEffect.READ,), "subagent"),
                result_kind="subagent",
            )
            tools = RunTools(
                session,
                contributions=[SkillUse(tools=(tool,))],
            )

            result = tools.run_tool_call(ToolCall("delegated-1", "run_subagent", {}))

            page_data = result["progressive_disclosure"]
            self.assertEqual("subagent", page_data["kind"])
            self.assertGreater(page_data["total_chars"], 9_000)
            self.assertEqual(4_000, len(page_data["content"]))
            page = disclosure.read_disclosed_content(page_data["reference"])
            self.assertIn('"text":', page.content)
            completed = [
                event
                for event in session.store.read_run_events(session.run_id)
                if event.event_type == "tool.completed"
            ][-1]
            self.assertNotIn("y" * 9_000, str(completed.data["result"]))

    def test_model_uses_explicit_long_term_memory_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = _create_session(root, conversation_id="conversation-a")
            disclosure = _create_disclosure(session)
            index = session.skills.index
            memory = Memory(
                session.store,
                session.identity,
                execute_action=session.execute_action,
            )
            tools = _create_tool_router(disclosure, index, session, memory)

            definitions = {
                item["function"]["name"] for item in tools.get_tool_definitions()
            }
            long_term = tools.run_tool_call(
                ToolCall(
                    "add-long-term",
                    "remember_long_term",
                    {"text": "User prefers Python.", "scope": "agent"},
                )
            )
            recalled = tools.run_tool_call(
                ToolCall(
                    "recall",
                    "recall_long_term_memory",
                    {"query": "Python", "scope": "agent"},
                )
            )
            tools.run_tool_call(
                ToolCall(
                    "forget",
                    "forget_long_term_memory",
                    {"item_id": long_term["item"]["item_id"]},
                )
            )

            self.assertEqual(
                {
                    "list_long_term_memory",
                    "remember_long_term",
                    "recall_long_term_memory",
                    "organize_long_term_memory",
                    "forget_long_term_memory",
                },
                definitions.intersection(
                    {
                        "list_long_term_memory",
                        "remember_long_term",
                        "recall_long_term_memory",
                        "organize_long_term_memory",
                        "forget_long_term_memory",
                    }
                ),
            )
            self.assertEqual(1, len(recalled["items"]))
            self.assertEqual([], memory.list_long_term())
            self.assertEqual(session.run_id, long_term["item"]["source_run_id"])

    def test_standard_policy_blocks_external_tool_before_handler_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = _create_session(root)
            disclosure = _create_disclosure(session)
            index = session.skills.index
            called = False

            def run_external(arguments: dict[str, object]) -> dict[str, object]:
                nonlocal called
                called = True
                return {"ok": True}

            tools = RunTools(
                session,
                contributions=[
                    SkillUse(
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
            index = session.skills.index
            contribution = session.skills.handlers.handle(
                SkillContext(
                    disclosure,
                    index.require_skill("untrusted", "mcp").reference,
                    store=session.store,
                    identity=session.identity,
                    execute_action=session.execute_action,
                )
            )
            tools = RunTools(
                session,
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
type = "prompt"
description = "Research helper"

""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text("Research carefully.", encoding="utf-8")


def _write_mcp_skill(root: Path) -> None:
    skill_dir = root / "skills" / "mcp" / "untrusted"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("skill.toml").write_text(
        f'''type = "mcp"
description = "Untrusted MCP server"

[configuration]
server = "untrusted"
'''.strip(),
        encoding="utf-8",
    )


def _create_disclosure(session: Run) -> ProgressiveDisclosureCore:
    return session.skills.disclosure


def _create_tool_router(
    disclosure: ProgressiveDisclosureCore,
    index,
    session: Run,
    memory: Memory | None = None,
) -> RunTools:
    if disclosure is not session.skills.disclosure or index is not session.skills.index:
        raise ValueError("tool router must use the Run Skill snapshot")
    return RunTools(
        session,
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
    config = CommonConfig.create_default(root)
    identity = RunIdentity.create(
        "local",
        config.agent.name,
        conversation_id=conversation_id,
    )
    backend = JsonlStorage(root / "state")
    event_log = RunEventLog(identity, backend=backend)
    store = EventStore(
        backend,
        root / "state",
        "local",
        config.agent.name,
        run_event_log=event_log,
        disclosure_factory=lambda cache_root, selected_store: DisclosureStorage(
            cache_root,
            selected_store,
        ),
    )
    event_log.start_run("question")
    disclosure = ProgressiveDisclosureCore(
        [root / "skills"],
        recorder=create_runtime_disclosure_recorder(store, identity),
    )
    return Run(
        config=config,
        task=_create_task(),
        skills=Skills(disclosure, create_default_skill_handlers(mcp_servers)),
        identity=identity,
        event_log=event_log,
        store=store,
        create_action_rules=ActionRules,
    )


def _create_task() -> Task:
    return Task(
        prompt="question",
        messages=[],
        include_subagents=False,
        warning_messages=[],
        subagents=SubagentCallbacks(lambda: [], _unexpected_subagent_run),
    )


def _unexpected_subagent_run(*args, **kwargs) -> dict[str, object]:
    raise AssertionError("subagent callback should not run")

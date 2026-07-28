import json
import sys
import tempfile
import unittest
from pathlib import Path

from capability.defaults import create_default_capability_registry
from capability.registry import SkillLoadRequest
from capability.skill_contributions import (
    CapabilityAction,
    CapabilityTool,
    SkillContribution,
)
from capability.skill_executors import create_memory_skill_contribution
from provider.chat import ToolCall
from provider.chat import MockProvider
from runtime.tools import RuntimeTools, RuntimeToolsContext
from runtime.config import AgentConfig
from runtime.identity import RunIdentity
from runtime.session import RuntimeSession
from runtime.safety import ActionConfirmationRequired, ActionEffect
from runtime.store import create_local_runtime_store
from skill.disclosure import ProgressiveDisclosureCore
from skill.kinds.memory import MiniMemory
from skill.kinds.model import discover_environment_model_profiles


class SkillToolsTests(unittest.TestCase):
    def test_model_can_list_and_read_one_skill_on_demand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "research")
            session = _create_session(root)
            disclosure = _create_disclosure(root, session)
            index = disclosure.prepare_skill_index()
            tools = _create_tool_router(disclosure, index, session)

            listed = tools.run_tool_call(ToolCall("call-1", "list_skills", {}))
            read = tools.run_tool_call(
                ToolCall(
                    "call-2",
                    "read_skill_instructions",
                    {"name": "research", "capability": "prompt"},
                )
            )

            self.assertEqual("research", listed["skills"][0]["name"])
            self.assertEqual("Research carefully.", read["instructions"])
            self.assertEqual(["research"], tools.used_skill_names)
            event_types = [
                event.event_type
                for event in session.store.read_run_events(session.run_id)
            ]
            self.assertIn("tool.requested", event_types)
            self.assertIn("action.checked", event_types)
            self.assertIn("action.completed", event_types)
            self.assertIn("tool.completed", event_types)
            self.assertIn("skill.disclosed", event_types)

    def test_reading_a_cached_path_records_the_skill_as_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "research")
            session = _create_session(root)
            disclosure = _create_disclosure(root, session)
            index = disclosure.prepare_skill_index()
            cached = disclosure.open_skill("research", "prompt").read_instructions()
            tools = _create_tool_router(disclosure, index, session)

            tools.run_tool_call(
                ToolCall(
                    "call-1",
                    "read_disclosed_content",
                    {"cache_path": str(cached.cache_path)},
                )
            )

            revisions = tools.context.session.list_used_skill_revisions()
            self.assertEqual(["prompt:research"], [item.key for item in revisions])

    def test_unknown_builtin_tool_fails_with_trace_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = _create_session(root)
            disclosure = _create_disclosure(root, session)
            index = disclosure.prepare_skill_index()
            tools = _create_tool_router(disclosure, index, session)

            with self.assertRaisesRegex(KeyError, "unknown runtime tool"):
                tools.run_tool_call(ToolCall("bad", "unknown", {}))

            self.assertEqual(
                "tool.failed",
                session.store.read_run_events(session.run_id)[-1].event_type,
            )

    def test_model_can_add_recall_and_forget_memory_with_runtime_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = _create_session(root)
            disclosure = _create_disclosure(root, session)
            index = disclosure.prepare_skill_index()
            memory = MiniMemory(
                session.store,
                session.identity,
                execute_action=session.execute_action,
            )
            tools = _create_tool_router(disclosure, index, session, memory)

            definitions = {
                item["function"]["name"] for item in tools.get_tool_definitions()
            }
            added = tools.run_tool_call(
                ToolCall(
                    "add",
                    "add_memory_item",
                    {"text": "Remember Python.", "scope": "agent"},
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
                ToolCall("forget", "forget_memory", {"item_id": added["item"]["item_id"]})
            )

            self.assertTrue(
                {
                    "list_memory_items",
                    "add_memory_item",
                    "recall_memory",
                    "forget_memory",
                    "consolidate_memory",
                }
                <= definitions
            )
            self.assertEqual("Remember Python.", recalled["items"][0]["text"])
            self.assertEqual([], memory.list_memory_items())
            self.assertEqual(session.run_id, added["item"]["source_run_id"])

    def test_standard_policy_blocks_external_tool_before_handler_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = _create_session(root)
            disclosure = _create_disclosure(root, session)
            index = disclosure.prepare_skill_index()
            called = False

            def run_external(arguments: dict[str, object]) -> dict[str, object]:
                nonlocal called
                called = True
                return {"ok": True}

            session.set_skill_disclosure(disclosure, index)
            tools = RuntimeTools(
                RuntimeToolsContext(session=session),
                contributions=[
                    SkillContribution(
                        tools=(
                            CapabilityTool(
                                "run_external",
                                "Run an external operation.",
                                {},
                                run_external,
                                action=CapabilityAction(
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
            self.assertNotIn("action.completed", event_types)

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
            _write_mcp_skill(root, script)
            session = _create_session(root)
            disclosure = _create_disclosure(root, session)
            index = disclosure.prepare_skill_index()
            contribution = session.capability_registry.load_skill(
                SkillLoadRequest(
                    disclosure,
                    index.require_skill("untrusted", "mcp").reference,
                    session.store,
                    session.identity,
                    execute_action=session.execute_action,
                )
            )
            session.set_skill_disclosure(disclosure, index)
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
schema_version = 2
name = "{name}"
capability = "prompt"
description = "Research helper"
version = "0.1.0"
triggers = ["never-match"]

[entry]
instructions = "SKILL.md"
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text("Research carefully.", encoding="utf-8")


def _write_mcp_skill(root: Path, script: Path) -> None:
    skill_dir = root / "skills" / "mcp" / "untrusted"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("skill.toml").write_text(
        f'''schema_version = 2
name = "untrusted"
capability = "mcp"
description = "Untrusted MCP command"
version = "0.1.0"
triggers = ["untrusted"]

[configuration]
transport = "stdio"
command = {json.dumps(sys.executable)}
args = [{json.dumps(str(script))}]
'''.strip(),
        encoding="utf-8",
    )


def _create_disclosure(root: Path, session: RuntimeSession) -> ProgressiveDisclosureCore:
    return ProgressiveDisclosureCore(
        [root / "skills"],
        session.store,
        identity=session.identity,
    )


def _create_tool_router(
    disclosure: ProgressiveDisclosureCore,
    index,
    session: RuntimeSession,
    memory: MiniMemory | None = None,
) -> RuntimeTools:
    session.set_skill_disclosure(disclosure, index)
    return RuntimeTools(
        RuntimeToolsContext(
            session=session,
        ),
        contributions=(
            []
            if memory is None
            else [create_memory_skill_contribution(memory, session.run_id)]
        ),
    )


def _create_session(root: Path) -> RuntimeSession:
    config = AgentConfig.create_default(root)
    provider = MockProvider()
    identity = RunIdentity.create("local", config.agent.name)
    store = create_local_runtime_store(root / "state", agent_name=config.agent.name)
    store.start_run(identity, "question")
    return RuntimeSession(
        config=config,
        model_profile=discover_environment_model_profiles({})[0],
        provider=provider,
        capability_registry=create_default_capability_registry(),
        identity=identity,
        store=store,
    )

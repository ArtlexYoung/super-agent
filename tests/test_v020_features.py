import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import URLError

from adapter.process import ProcessSettings, ProcessTools
from adapter.storage import MemoryStorage
from adapter.tools import CodeWorkspace, ToolPolicy, WorkspaceSettings
from core.event import RunIdentity
from core.model import ModelEvent, Tool
from core.provider import MockModel, ModelPricing
from core.records import EventStore, compact_child_result
from core.run import FatalToolError, RunSession
from skill.evolution import (
    SkillEvidence,
    SkillEvolution,
    SkillTestCase,
    calculate_freshness,
)
from skill.library import SkillLibrary
from skill.memory import Memory
from skill.organization import AgentMemberSettings, AgentTreeSettings, agent_group_node
from skill.organization_runtime import AgentTreeRuntime
from super_agent import Agent


def write_skill(root: Path, name: str = "demo", body: str = "Follow the demo method.", *, created_by: str = "user") -> Path:
    path = root / "prompt" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "+++\n"
        f'name = "{name}"\n'
        'type = "prompt"\n'
        'description = "A demo method"\n'
        'version = "1.0.0"\n'
        f'created_by = "{created_by}"\n'
        f'agent_can_update = {str(created_by == "agent").lower()}\n'
        'categories = ["demo"]\n'
        '+++\n'
        + body
        + "\n",
        encoding="utf-8",
    )
    return path


class SkillLibraryTests(unittest.TestCase):
    def test_index_pages_cache_history_and_activation_share_one_library(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skills"
            write_skill(root, body="A" * 30)
            library = SkillLibrary((root,), cache_root=Path(directory) / "cache")
            page = library.list_skills(page=1, page_size=1)
            self.assertEqual("prompt:demo", page.items[0]["key"])
            first = library.disclose("prompt:demo", max_characters=10)
            self.assertEqual(10, len(first.content))
            self.assertIsNotNone(first.next_offset)
            cached = library.read_disclosed(first.cache_path, max_characters=10)
            self.assertEqual(first.content, cached.content)
            self.assertEqual(2, len(library.history()))

            session = RunSession(RunIdentity(), [], [], {}, values={"available_tools": {}})
            activated = library.activate("demo", session)
            self.assertEqual(("prompt:demo",), activated)
            self.assertIn("AAAAAAAA", session.instructions[0])

    def test_memory_cache_path_can_replay_disclosed_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skills"
            write_skill(root)
            library = SkillLibrary((root,))
            disclosed = library.disclose("prompt:demo")
            self.assertTrue(disclosed.cache_path.startswith("memory://"))
            self.assertEqual(disclosed, library.read_disclosed(disclosed.cache_path))

    def test_disclosure_history_and_memory_cache_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skills"
            write_skill(root, body="abcdef")
            write_skill(root, name="other", body="uvwxyz")
            library = SkillLibrary((root,), cache_entries=1)
            first = library.disclose("prompt:demo", offset=0, max_characters=2)
            second = library.disclose("prompt:other", offset=0, max_characters=2)
            self.assertEqual(second.cache_path, library.history()[0]["cache_path"])
            with self.assertRaises(KeyError):
                library.read_disclosed(first.cache_path)

    def test_disabled_skill_is_hidden_and_cannot_be_activated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skills"
            write_skill(root)
            library = SkillLibrary((root,), disabled_references=("prompt:demo",))
            self.assertEqual(0, library.list_skills().total)
            with self.assertRaises(KeyError):
                library.find("prompt:demo")
            session = RunSession(
                RunIdentity(),
                [],
                [],
                {},
                values={"available_tools": {}},
            )
            with self.assertRaises(KeyError):
                library.activate("prompt:demo", session)
            self.assertEqual([], session.instructions)

    def test_failed_activation_restores_run_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skills"
            library = SkillLibrary((), writable_root=root)
            skill = library.create(
                "blocked",
                "Needs two tools.",
                description="Cannot activate partially",
                requires=("available", "missing"),
            )
            available = Tool("available", "Available tool", lambda _args, _context: {})
            session = RunSession(
                RunIdentity(),
                [],
                ["existing"],
                {},
                values={"available_tools": {"available": available}},
                context_characters=8,
            )
            with self.assertRaises(RuntimeError):
                library.activate(skill.key, session)
            self.assertEqual({}, session.tools)
            self.assertEqual(["existing"], session.instructions)
            self.assertEqual([], session.active_skills)
            self.assertEqual(8, session.context_characters)

    def test_optional_skill_tools_mount_only_when_registered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skills"
            library = SkillLibrary((), writable_root=root)
            skill = library.create(
                "optional",
                "Use available tools.",
                description="Optional tools",
                requires=("required",),
                optional_tools=("optional", "not_registered"),
            )
            required = Tool("required", "Required tool", lambda _args, _context: {})
            optional = Tool("optional", "Optional tool", lambda _args, _context: {})
            session = RunSession(
                RunIdentity(),
                [],
                [],
                {},
                values={"available_tools": {"required": required, "optional": optional}},
            )
            library.activate(skill.key, session)
            self.assertEqual({"required", "optional"}, set(session.tools))
            self.assertEqual(
                ["optional", "not_registered"],
                library.list_skills().items[0]["optional_tools"],
            )

    def test_agent_owned_skill_has_explicit_update_permission_and_hash_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skills"
            library = SkillLibrary((), writable_root=root)
            created = library.create("self", "initial", description="Self skill", actor="agent")
            self.assertTrue(created.agent_can_update)
            changed = library.update("prompt:self", "updated", expected_sha256=created.sha256, actor="agent")
            self.assertEqual("0.1.1", changed.version)
            with self.assertRaises(RuntimeError):
                library.update("prompt:self", "stale", expected_sha256=created.sha256, actor="agent")

            user_skill = library.create("user", "owned", description="User skill")
            with self.assertRaises(PermissionError):
                library.update(user_skill.key, "blocked", expected_sha256=user_skill.sha256, actor="agent")

    def test_skill_evolution_requires_test_before_apply_and_can_undo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skills"
            library = SkillLibrary((), writable_root=root)
            skill = library.create("self", "old method", description="Self skill", actor="agent")
            runner = lambda body, _prompt: body
            evolution = SkillEvolution(library, runner=runner)
            change = evolution.propose(skill.key, "new method", reason="better", actor="agent")
            with self.assertRaises(ValueError):
                evolution.apply(change.change_id)
            tested = evolution.test(change.change_id, [SkillTestCase("contains", "use", required_text=("new",))])
            self.assertTrue(tested.report["passed"])
            applied = evolution.apply(change.change_id)
            self.assertEqual("new method", library.find(skill.key).body)
            undone = evolution.undo(applied.change_id)
            self.assertEqual("old method", library.find(skill.key).body)
            self.assertEqual("undone", undone.status)
            evolution.record_evidence(SkillEvidence(skill.key, 1.0, True))
            evolution.record_evidence(SkillEvidence(skill.key, 0.2, False))
            self.assertEqual((2, 1), evolution.count_skill_evidence(skill.key))

    def test_persisted_skill_change_stores_bodies_once_and_rebuilds_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skills"
            library = SkillLibrary((), writable_root=root)
            skill = library.create("self", "old method", description="Self skill", actor="agent")
            store = EventStore(MemoryStorage(), "alice", "agent")
            evolution = SkillEvolution(library, store=store, runner=lambda body, _prompt: body)
            proposed = evolution.propose(skill.key, "new method", reason="measured improvement")
            tested = evolution.test(
                proposed.change_id,
                [SkillTestCase("contains", "use", required_text=("new",))],
            )
            evolution.apply(tested.change_id)
            records = store.read("skill_change", proposed.change_id)
            self.assertIn("candidate_body", records[0].data)
            self.assertTrue(all("candidate_body" not in item.data for item in records[1:]))
            rebuilt = SkillEvolution(library, store=store, runner=lambda body, _prompt: body)
            self.assertEqual("undone", rebuilt.undo(proposed.change_id).status)

    def test_skill_change_tool_event_is_linked_to_the_run_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skills"
            write_skill(root, name="self", created_by="agent")
            builtin = Path(__file__).resolve().parents[1] / "src" / "skill" / "builtin"
            library = SkillLibrary((root, builtin), writable_root=Path(directory) / "owned")
            model = MockModel(
                responses=(
                    (
                        ModelEvent.call(
                            "change-1",
                            "propose_skill_update",
                            {
                                "skill": "prompt:self",
                                "candidate_body": "Improved method.",
                                "reason": "Measured improvement.",
                            },
                        ),
                        ModelEvent.done(),
                    ),
                    "Proposal recorded.",
                )
            )
            agent = Agent(model)
            agent.use_skill_library(library)
            agent.enable_skill_evolution()
            agent.use_storage(MemoryStorage())
            result = agent.run("Improve the Skill", skill="evolution:self-update")
            insight = agent.for_user("local").runs.explain(result.run_id)
            self.assertEqual(
                ["skill_change.proposed"],
                [item["event_type"] for item in insight["evolution"]],
            )
            self.assertTrue(insight["evolution"][0]["reason"]["redacted"])

    def test_freshness_is_deterministic_and_multidimensional(self):
        now = datetime.now(UTC)
        evidence = [
            SkillEvidence("prompt:demo", 1.0, True, input_tokens=10, output_tokens=5, used_at=(now - timedelta(days=1)).isoformat()),
            SkillEvidence("prompt:demo", 0.0, False, input_tokens=10_000, output_tokens=10_000, replacement_calls=2, used_at=(now - timedelta(days=60)).isoformat()),
        ]
        first = calculate_freshness(evidence, now=now)
        second = calculate_freshness(evidence, now=now)
        self.assertEqual(first, second)
        self.assertEqual(2, first.sample_count)
        self.assertLess(first.quality, 1)
        self.assertLess(first.efficiency, 1)


class MemoryAndAgentTreeTests(unittest.TestCase):
    def test_summary_record_removes_child_events_but_keeps_counts(self):
        compacted = compact_child_result(
            {
                "text": "answer",
                "events": [
                    {"event_type": "model.text.delta", "data": {"delta": "answer"}},
                    {"event_type": "run.completed", "data": {}},
                ],
                "usage": {"input_tokens": 2},
            },
            mode="summary",
        )
        self.assertNotIn("events", compacted)
        self.assertEqual(2, compacted["event_count"])
        self.assertEqual(1, compacted["event_types"]["model.text.delta"])

    def test_temporary_memory_stays_in_conversation_and_can_be_promoted(self):
        memory = Memory()
        temporary = memory.remember_temporary("current file is open", conversation_id="conversation-1")
        long_term = memory.remember_long_term("user prefers concise answers", labels=("preference",))
        self.assertEqual([temporary], [item for item in memory.recall("file", conversation_id="conversation-1") if item.lifetime == "temporary"])
        self.assertEqual([], [item for item in memory.recall("file", conversation_id="conversation-2") if item.lifetime == "temporary"])
        promoted = memory.promote_temporary(temporary.memory_id, "The user is working on an open file", reason="stable project context")
        self.assertEqual("long_term", promoted.lifetime)
        forgotten = memory.forget(long_term.memory_id)
        self.assertEqual("forgotten", forgotten.status)
        self.assertEqual([promoted.memory_id], [item.memory_id for item in memory.list_items(lifetime="long_term")])

    def test_existing_subtree_attaches_under_groups_with_derived_levels(self):
        root = Agent(MockModel("root"), name="root")
        lead = Agent(MockModel("lead"), name="lead")
        worker = Agent(MockModel("worker"), name="worker")
        lead.add_subagent(worker, name="implementer")
        engineering = root.add_group("engineering")
        engineering.add_subagent(lead, name="platform")

        tree = root.list_agent_tree()["root"]
        self.assertEqual(1, tree["level"])
        self.assertEqual(2, tree["children"][0]["level"])
        platform = tree["children"][0]["children"][0]
        self.assertEqual(("platform", 3), (platform["name"], platform["level"]))
        self.assertEqual("implementer", platform["children"][0]["name"])
        self.assertEqual(4, platform["children"][0]["level"])

    def test_sibling_agents_exchange_notes_through_the_parent_board(self):
        root = Agent(MockModel("root"), name="root")
        team = root.add_group("team")
        writer = Agent(MockModel("writer"), name="writer")
        reviewer = Agent(MockModel("reviewer"), name="reviewer")
        team.add_subagent(writer, name="writer")
        team.add_subagent(reviewer, name="reviewer")
        runtime = AgentTreeRuntime(agent_group_node(root).root())

        note = runtime.post_note(
            group_id=agent_group_node(writer).group_id,
            title="result",
            content="measured result",
            board="parent",
        )
        wake = runtime.wait_for_notes(
            group_id=agent_group_node(reviewer).group_id,
            board="parent",
            timeout_seconds=0,
        )
        listed = runtime.list_notes(
            group_id=agent_group_node(reviewer).group_id,
            board="parent",
        )
        replayed = runtime.disclosures.read(note.cache_path)
        self.assertEqual("shared_note_posted", wake["reason"])
        self.assertEqual(note.note_id, listed["notes"][0]["note_id"])
        self.assertEqual("measured result", replayed.content)

    def test_tasks_keep_configured_order_and_wait_without_model_polling(self):
        root = Agent(MockModel("root"), name="root")
        root.add_subagent(Agent(MockModel("alpha result")), name="alpha")
        root.add_subagent(Agent(MockModel("zulu result")), name="zulu")
        runtime = AgentTreeRuntime(
            agent_group_node(root).root(), AgentTreeSettings(max_wait_seconds=2)
        )
        task = runtime.create_task(
            "one", source_group_id=agent_group_node(root).group_id
        )
        dispatched = runtime.dispatch_task(
            task.task_id, source_group_id=agent_group_node(root).group_id
        )
        wake = runtime.wait_for_tasks(
            "all_tasks_finished",
            group_id=agent_group_node(root).group_id,
            timeout_seconds=2,
        )
        self.assertEqual("alpha", dispatched.agent_name)
        self.assertEqual("all_tasks_finished", wake["reason"])
        self.assertTrue(wake["all_tasks_finished"])

    def test_task_ownership_is_isolated_between_sibling_groups(self):
        root = Agent(MockModel("root"), name="root")
        alpha = root.add_group("alpha")
        beta = root.add_group("beta")
        alpha.add_subagent(Agent(MockModel("alpha result")), name="worker-alpha")
        beta.add_subagent(Agent(MockModel("beta result")), name="worker-beta")
        runtime = AgentTreeRuntime(agent_group_node(root).root())
        task = runtime.create_task("private", source_group_id=alpha.group_id)

        self.assertEqual([], runtime.list_tasks(beta.group_id))
        with self.assertRaises(PermissionError):
            runtime.dispatch_task(task.task_id, source_group_id=beta.group_id)
        with self.assertRaises(PermissionError):
            runtime.wait_for_tasks(
                "selected_tasks_finished",
                group_id=beta.group_id,
                timeout_seconds=0,
                task_ids=(task.task_id,),
            )

    def test_weight_and_price_select_the_cheaper_stronger_agent(self):
        root = Agent(MockModel("root"), name="root")
        expensive = AgentMemberSettings(
            weight=1,
            pricing=ModelPricing(100, 100, 100, 100),
        )
        preferred = AgentMemberSettings(
            weight=2,
            pricing=ModelPricing(0, 0, 0, 0),
        )
        root.add_subagent(
            Agent(MockModel("expensive")), name="expensive", settings=expensive
        )
        root.add_subagent(
            Agent(MockModel("preferred")), name="preferred", settings=preferred
        )
        runtime = AgentTreeRuntime(agent_group_node(root).root())
        task = runtime.create_task(
            "choose", source_group_id=agent_group_node(root).group_id
        )
        dispatched = runtime.dispatch_task(
            task.task_id, source_group_id=agent_group_node(root).group_id
        )
        self.assertEqual("preferred", dispatched.agent_name)

    def test_adaptive_records_compress_later_child_runs(self):
        root = Agent(MockModel("root"), name="root")
        root.add_subagent(Agent(MockModel("result")), name="worker")
        runtime = AgentTreeRuntime(
            agent_group_node(root).root(),
            AgentTreeSettings(
                max_wait_seconds=2,
                compress_after_tasks=1,
                summary_characters=20,
            ),
        )
        for prompt in ("first", "second"):
            task = runtime.create_task(
                prompt, source_group_id=agent_group_node(root).group_id
            )
            runtime.dispatch_task(
                task.task_id, source_group_id=agent_group_node(root).group_id
            )
            runtime.wait_for_tasks(
                "selected_tasks_finished",
                group_id=agent_group_node(root).group_id,
                timeout_seconds=2,
                task_ids=(task.task_id,),
            )
        results = [item["result"] for item in runtime.list_tasks(agent_group_node(root).group_id)]
        self.assertIn("events", results[0])
        self.assertNotIn("events", results[1])
        self.assertGreater(results[1]["event_count"], 0)

    def test_temporary_failure_opens_circuit_and_retries_after_sleep(self):
        root = Agent(MockModel("root"), name="root")
        worker_model = MockModel(responses=(URLError("offline"), "recovered"))
        root.add_subagent(Agent(worker_model), name="worker")
        runtime = AgentTreeRuntime(
            agent_group_node(root).root(),
            AgentTreeSettings(
                max_wait_seconds=2,
                circuit_wait_seconds=0,
                retry_unavailable_times=1,
            ),
        )
        task = runtime.create_task(
            "retry", source_group_id=agent_group_node(root).group_id
        )
        runtime.dispatch_task(
            task.task_id, source_group_id=agent_group_node(root).group_id
        )
        runtime.wait_for_tasks(
            "all_tasks_finished",
            group_id=agent_group_node(root).group_id,
            timeout_seconds=2,
        )
        completed = runtime.list_tasks(agent_group_node(root).group_id)[0]
        self.assertEqual("completed", completed["status"])
        self.assertEqual((2, 1), (completed["attempts"], completed["fallback_count"]))

    def test_agent_decision_uses_quorum_and_different_models(self):
        root = Agent(MockModel("root"), name="root")
        response = json.dumps({"decision": "support", "confidence": 0.9})
        for index in range(3):
            root.add_subagent(
                Agent(MockModel(response)),
                name=f"worker{index}",
                settings=AgentMemberSettings(
                    purpose="optimize", model_name=f"model{index}"
                ),
            )
        runtime = AgentTreeRuntime(
            agent_group_node(root).root(),
            AgentTreeSettings(max_wait_seconds=2),
        )
        decision = runtime.create_decision(
            "find an optimization",
            group_id=agent_group_node(root).group_id,
            purpose="optimize",
        )
        queued_tasks = runtime.list_tasks(agent_group_node(root).group_id)
        self.assertEqual(
            set(decision.task_ids), {str(task["task_id"]) for task in queued_tasks}
        )
        completed = runtime.wait_for_decision(
            decision.decision_id,
            group_id=agent_group_node(root).group_id,
            timeout_seconds=2,
        )
        self.assertEqual("completed", completed.status)
        self.assertEqual("support", completed.result)
        self.assertEqual(2, len(completed.decisions))
        self.assertEqual(3, len(set(completed.worker_names)))
        task_statuses = {
            str(task["task_id"]): task["status"]
            for task in runtime.list_tasks(agent_group_node(root).group_id)
        }
        self.assertEqual(
            ["completed", "completed", "cancelled"],
            [task_statuses[task_id] for task_id in decision.task_ids],
        )

    def test_tree_tools_are_available_only_after_agents_are_added(self):
        root_model = MockModel("ready")
        root = Agent(root_model, name="root")
        root.run("before")
        self.assertNotIn("list_agent_tree", [tool.name for tool in root_model.requests[-1].tools])
        root.add_subagent(Agent(MockModel("child")), name="child")
        root.run("after")
        self.assertIn("list_agent_tree", [tool.name for tool in root_model.requests[-1].tools])

    def test_agent_names_and_cycle_warnings_are_explicit(self):
        main = Agent(MockModel("ok"), name="main")
        child = Agent(MockModel("child"), name="child")
        self.assertEqual("subagent01", main.add_subagent(child))
        self.assertEqual("subagent02", main.add_subagent(Agent(MockModel("child2"))))
        child.add_subagent(main, name="back")
        result = main.run("check")
        self.assertTrue(any("cycle" in warning.lower() for warning in result.warning_messages))

    def test_maximum_tree_level_is_checked_before_execution(self):
        root = Agent(MockModel("root"), name="root")
        group = root.add_group("department")
        group.add_subagent(Agent(MockModel("worker")), name="worker")
        root.configure_agent_tree(
            AgentTreeSettings(warn_level=2, max_level=2)
        )
        with self.assertRaisesRegex(RuntimeError, "level 3"):
            root.run("blocked")


class WorkspaceTests(unittest.TestCase):
    def test_workspace_writes_require_hash_and_policy_can_block_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "note.txt"
            target.write_text("old", encoding="utf-8")
            workspace = CodeWorkspace(WorkspaceSettings(root, allow_write=True))
            read = next(tool for tool in workspace.tools() if tool.name == "read_file")
            write = next(tool for tool in workspace.tools() if tool.name == "write_file")
            value = read.handler({"path": "note.txt"}, object())
            self.assertEqual("old", value["content"])
            with self.assertRaises(ValueError):
                write.handler({"path": "note.txt", "content": "new"}, object())
            result = write.handler({"path": "note.txt", "content": "new", "expected_sha256": value["sha256"]}, object())
            self.assertEqual("new", target.read_text(encoding="utf-8"))
            protected = ToolPolicy().protect(write)
            with self.assertRaises(FatalToolError):
                protected.handler(
                    {
                        "path": "note.txt",
                        "content": "again",
                        "expected_sha256": result["sha256"],
                    },
                    object(),
                )

    def test_process_commands_must_match_declared_arguments_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = ProcessTools(
                ProcessSettings(Path(directory), (("printf", "ok"),))
            ).tools()
            run_check = next(tool for tool in tools if tool.name == "run_check")
            listed = next(tool for tool in tools if tool.name == "list_process_commands")
            self.assertEqual(
                [["printf", "ok"]],
                listed.handler({}, object())["commands"],
            )
            with self.assertRaises(PermissionError):
                run_check.handler({"command": ["printf", "ok", "extra"]}, object())


if __name__ == "__main__":
    unittest.main()

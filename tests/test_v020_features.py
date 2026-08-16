import json
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.event import RunIdentity, RunResult
from core.model import ModelEvent, Tool
from core.records import EventStore, compact_child_result
from adapter.process import ProcessSettings, ProcessTools
from adapter.storage import MemoryStorage
from adapter.tools import CodeWorkspace, ToolPolicy, WorkspaceSettings
from skill.evolution import SkillEvidence, SkillEvolution, SkillTestCase, calculate_freshness
from skill.groups import AgentGroups, GroupSettings
from skill.library import SkillLibrary
from skill.memory import Memory
from skill.team import AgentWorker, TaskQueue, TaskQueueSettings
from core.run import RunSession, ToolContext
from super_agent import Agent
from core.provider import MockModel


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


class MemoryAndTeamTests(unittest.TestCase):
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

    def test_task_queue_waits_and_runs_one_worker_at_a_time(self):
        def run(prompt, _parent, _shared):
            return RunResult(prompt.upper(), "child-run", "done", ())

        queue = TaskQueue(
            [AgentWorker("worker", run, purpose="code", features=("text",), model_name="small")],
            TaskQueueSettings(max_wait_seconds=2),
        )
        first = queue.create_task("one", purpose="code")
        second = queue.create_task("two", purpose="code")
        queue.dispatch_task(first.task_id)
        queue.dispatch_task(second.task_id)
        wake = queue.wait_for_tasks("all_tasks_finished", timeout_seconds=2)
        self.assertEqual("all_tasks_finished", wake["reason"])
        statuses = {item["status"] for item in queue.list_tasks()}
        self.assertEqual({"completed"}, statuses)

    def test_equal_worker_scores_keep_the_configured_order(self):
        run = lambda prompt, _parent, _shared: RunResult(prompt, "child", "done", ())
        queue = TaskQueue(
            [AgentWorker("alpha", run), AgentWorker("zulu", run)],
            TaskQueueSettings(max_wait_seconds=2),
        )
        task = queue.create_task("one")
        dispatched = queue.dispatch_task(task.task_id)
        self.assertEqual("alpha", dispatched.agent_name)
        queue.wait_for_tasks("all_tasks_finished", timeout_seconds=2)

    def test_decision_group_uses_quorum_and_different_models(self):
        def decide(_prompt, _parent, _shared):
            return RunResult(json.dumps({"decision": "support", "confidence": 0.9}), "child", "done", ())

        workers = [
            AgentWorker(f"worker{i}", decide, purpose="optimize", model_name=f"model{i}")
            for i in range(3)
        ]
        queue = TaskQueue(workers, TaskQueueSettings(max_wait_seconds=2))
        groups = AgentGroups(queue, GroupSettings(default_members=3, quorum=2, max_members=3))
        group = groups.create_group("find an optimization", purpose="optimize")
        completed = groups.wait_for_group(group.group_id, timeout_seconds=2)
        self.assertEqual("completed", completed.status)
        self.assertEqual("support", completed.result)
        self.assertEqual(2, len(completed.decisions))

    def test_agent_names_and_cycle_warnings_are_explicit(self):
        main = Agent(MockModel("ok"), name="main")
        child = Agent(MockModel("child"), name="child")
        self.assertEqual("subagent01", main.add_subagent(child))
        self.assertEqual("subagent02", main.add_subagent(Agent(MockModel("child2"))))
        child.add_subagent(main, name="back")
        result = main.run("check")
        self.assertTrue(any("cycle" in warning.lower() for warning in result.warning_messages))


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
            with self.assertRaises(Exception):
                protected.handler({"path": "note.txt", "content": "again", "expected_sha256": result["sha256"]}, object())

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

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from adapter.process import ProcessSettings, ProcessTools
from adapter.storage import MemoryStorage
from adapter.tools import CodeWorkspace, ToolPolicy, WorkspaceSettings
from core.event import RunIdentity
from core.provider import MockModel, ModelPricing
from core.records import compact_child_result
from core.run import FatalToolError
from skill.memory import Memory
from skill.organization import TeamMemberSettings, TeamSettings, team_node
from skill.organization_runtime import TeamRuntime
from super_agent import Agent


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
        runtime = TeamRuntime(team_node(root).root())

        note = runtime.post_note(
            group_id=team_node(writer).group_id,
            title="result",
            content="measured result",
            board="parent",
        )
        wake = runtime.wait_for_notes(
            group_id=team_node(reviewer).group_id,
            board="parent",
            timeout_seconds=0,
        )
        listed = runtime.list_notes(
            group_id=team_node(reviewer).group_id,
            board="parent",
        )
        replayed = runtime.resources.read_cached_resource(note.cache_path)
        self.assertEqual("shared_note_posted", wake["reason"])
        self.assertEqual(note.note_id, listed["notes"][0]["note_id"])
        self.assertEqual("measured result", replayed.content)

    def test_tasks_keep_configured_order_and_wait_without_model_polling(self):
        root = Agent(MockModel("root"), name="root")
        root.add_subagent(Agent(MockModel("alpha result")), name="alpha")
        root.add_subagent(Agent(MockModel("zulu result")), name="zulu")
        runtime = TeamRuntime(
            team_node(root).root(), TeamSettings(max_wait_seconds=2)
        )
        task = runtime.create_task(
            "one", source_group_id=team_node(root).group_id
        )
        dispatched = runtime.dispatch_task(
            task.task_id, source_group_id=team_node(root).group_id
        )
        wake = runtime.wait_for_tasks(
            "all_tasks_finished",
            group_id=team_node(root).group_id,
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
        runtime = TeamRuntime(team_node(root).root())
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
        expensive = TeamMemberSettings(
            weight=1,
            pricing=ModelPricing(100, 100, 100, 100),
        )
        preferred = TeamMemberSettings(
            weight=2,
            pricing=ModelPricing(0, 0, 0, 0),
        )
        root.add_subagent(
            Agent(MockModel("expensive")), name="expensive", settings=expensive
        )
        root.add_subagent(
            Agent(MockModel("preferred")), name="preferred", settings=preferred
        )
        runtime = TeamRuntime(team_node(root).root())
        task = runtime.create_task(
            "choose", source_group_id=team_node(root).group_id
        )
        dispatched = runtime.dispatch_task(
            task.task_id, source_group_id=team_node(root).group_id
        )
        self.assertEqual("preferred", dispatched.agent_name)

    def test_adaptive_records_compress_later_child_runs(self):
        root = Agent(MockModel("root"), name="root")
        root.add_subagent(Agent(MockModel("result")), name="worker")
        runtime = TeamRuntime(
            team_node(root).root(),
            TeamSettings(
                max_wait_seconds=2,
                compress_after_tasks=1,
                summary_characters=20,
            ),
        )
        for prompt in ("first", "second"):
            task = runtime.create_task(
                prompt, source_group_id=team_node(root).group_id
            )
            runtime.dispatch_task(
                task.task_id, source_group_id=team_node(root).group_id
            )
            runtime.wait_for_tasks(
                "selected_tasks_finished",
                group_id=team_node(root).group_id,
                timeout_seconds=2,
                task_ids=(task.task_id,),
            )
        results = [item["result"] for item in runtime.list_tasks(team_node(root).group_id)]
        self.assertIn("events", results[0])
        self.assertNotIn("events", results[1])
        self.assertGreater(results[1]["event_count"], 0)

    def test_temporary_failure_opens_circuit_and_retries_after_sleep(self):
        root = Agent(MockModel("root"), name="root")
        worker_model = MockModel(responses=(URLError("offline"), "recovered"))
        root.add_subagent(Agent(worker_model), name="worker")
        runtime = TeamRuntime(
            team_node(root).root(),
            TeamSettings(
                max_wait_seconds=2,
                circuit_wait_seconds=0,
                retry_unavailable_times=1,
            ),
        )
        task = runtime.create_task(
            "retry", source_group_id=team_node(root).group_id
        )
        runtime.dispatch_task(
            task.task_id, source_group_id=team_node(root).group_id
        )
        runtime.wait_for_tasks(
            "all_tasks_finished",
            group_id=team_node(root).group_id,
            timeout_seconds=2,
        )
        completed = runtime.list_tasks(team_node(root).group_id)[0]
        self.assertEqual("completed", completed["status"])
        self.assertEqual((2, 1), (completed["attempts"], completed["fallback_count"]))

    def test_agent_decision_uses_quorum_and_different_models(self):
        root = Agent(MockModel("root"), name="root")
        response = json.dumps({"decision": "support", "confidence": 0.9})
        for index in range(3):
            root.add_subagent(
                Agent(MockModel(response)),
                name=f"worker{index}",
                settings=TeamMemberSettings(
                    purpose="optimize", model_name=f"model{index}"
                ),
            )
        runtime = TeamRuntime(
            team_node(root).root(),
            TeamSettings(max_wait_seconds=2),
        )
        decision = runtime.create_decision(
            "find an optimization",
            group_id=team_node(root).group_id,
            purpose="optimize",
        )
        queued_tasks = runtime.list_tasks(team_node(root).group_id)
        self.assertEqual(
            set(decision.task_ids), {str(task["task_id"]) for task in queued_tasks}
        )
        completed = runtime.wait_for_decision(
            decision.decision_id,
            group_id=team_node(root).group_id,
            timeout_seconds=2,
        )
        self.assertEqual("completed", completed.status)
        self.assertEqual("support", completed.result)
        self.assertEqual(2, len(completed.decisions))
        self.assertEqual(3, len(set(completed.worker_names)))
        task_statuses = {
            str(task["task_id"]): task["status"]
            for task in runtime.list_tasks(team_node(root).group_id)
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
        root.configure_team(
            TeamSettings(warn_level=2, max_level=2)
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

"""检查任务样本的真产物、反例，以及当前队列和副作用边界。"""

import hashlib
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from eval.task_baseline.baseline import (
    TASKS, acceptance_is_current, evaluate, file_hashes, prepare_workspace,
    read_json, run_baseline, run_case, scripted_model,
)
from super_agent import Agent
from super_agent.adapter.tools import CodeWorkspace, WorkspaceSettings
from super_agent.core.model import ModelEvent
from super_agent.core.provider import MockModel
from super_agent.core.run import FatalToolError, RunInput, collect_run, stream_run
from super_agent.skill.organization import TaskCheck, TeamSettings, team_node
from super_agent.skill.organization_runtime import TeamRuntime
from super_agent.skill.organization_tasks import TaskQueue


class TaskBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_paths_record_real_acceptance_and_single_agent_plugin(self) -> None:
        report = run_baseline(self.root / "report")
        self.assertEqual(6, len(report["runs"]))
        for run in report["runs"]:
            with self.subTest(task=run["task"], variant=run["variant"]):
                # v0.2.114 起完整 code 插件不再要求团队工具，六条路径都应真正装配并产出可验收结果。
                self.assertEqual("", run["execution_error"])
                self.assertTrue(run["execution_checks_passed"])
                self.assertTrue(run["acceptance"]["accepted"], run["acceptance"])
                self.assertEqual(0, run["live_model_calls"])
                self.assertGreater(run["request_bytes"], 0)
                self.assertEqual(bool(run["loaded_skills"]), run["variant"] != "plain")
                actions = [item for item in run["actions"] if item["call_id"] == "call-2"]
                self.assertEqual(["action.checked", "tool.started", "tool.completed"],
                                 [item["event"] for item in actions])
                self.assertEqual(1, len({item["run_id"] for item in actions}))
                self.assertTrue(actions[0]["run_id"])
        self.assertTrue(report["passed"])
        # 反例不删除：原缺陷仍未修复时，同一检查器必须拒绝，产物不能算通过。
        workspace = self.root / "unfixed"
        prepare_workspace("bug-fix", workspace)
        self.assertFalse(evaluate("bug-fix", workspace)["accepted"])

    def test_existing_report_directory_is_never_overwritten(self) -> None:
        existing = self.root / "report"
        existing.mkdir()
        marker = existing / "keep.txt"
        marker.write_text("existing", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            run_baseline(existing)
        self.assertEqual("existing", marker.read_text(encoding="utf-8"))

    def test_original_bug_and_absent_research_are_rejected(self) -> None:
        for task in TASKS:
            workspace = self.root / task
            prepare_workspace(task, workspace)
            self.assertFalse(evaluate(task, workspace)["accepted"])

    def test_expired_artifact_and_criteria_invalidate_local_acceptance(self) -> None:
        workspace = self.root / "work"
        acceptance = run_case("bug-fix", "plain", workspace)["acceptance"]
        self.assertTrue(acceptance_is_current(acceptance, workspace))
        self.assertFalse(acceptance_is_current({**acceptance, "criteria_sha256": "old"}, workspace))
        (workspace / "event_window.py").write_text("broken", encoding="utf-8")
        self.assertFalse(acceptance_is_current(acceptance, workspace))
        self.assertFalse(evaluate("bug-fix", workspace)["accepted"])

    def test_source_edits_and_extra_files_are_out_of_scope(self) -> None:
        workspace = self.root / "work"
        run_case("research", "plain", workspace)
        source = workspace / "sources.json"
        original = source.read_bytes()
        source.write_text("{}", encoding="utf-8")
        self.assertFalse(evaluate("research", workspace)["checks"]["scope_unchanged"])
        source.write_bytes(original)
        (workspace / "fake_test.py").write_text("pass", encoding="utf-8")
        self.assertFalse(evaluate("research", workspace)["checks"]["scope_unchanged"])

    def test_wrong_missing_duplicate_and_malformed_citations_are_rejected(self) -> None:
        workspace = self.root / "work"
        run_case("research", "plain", workspace)
        artifact = workspace / "decision.json"
        valid = read_json(artifact)
        invalid = [
            [], {**valid, "choice": "jsonl"}, {**valid, "claims": []},
            {**valid, "claims": [*valid["claims"], valid["claims"][0]]},
            {**valid, "claims": [{"fact": "json_requires_framing", "source": "sqlite-wal"}, *valid["claims"][1:]]},
            {**valid, "not_claimed": []}, {**valid, "claims": [None]},
        ]
        for value in invalid:
            with self.subTest(value=value):
                artifact.write_text(json.dumps(value), encoding="utf-8")
                self.assertFalse(evaluate("research", workspace)["accepted"])

    def test_artifact_symlink_is_rejected_without_reading_target(self) -> None:
        workspace = self.root / "work"
        prepare_workspace("research", workspace)
        (workspace / "decision.json").symlink_to(self.root / "absent")
        result = evaluate("research", workspace)
        self.assertFalse(result["accepted"])
        self.assertIn("symlink is not allowed", result["detail"])

    def test_denied_write_has_no_effect_and_records_call_identity(self) -> None:
        workspace = self.root / "work"
        prepare_workspace("bug-fix", workspace)
        before = file_hashes(workspace)
        model = MockModel(responses=[(ModelEvent.call("denied", "write_file", {
            "path": "new.txt", "content": "must not appear"}), ModelEvent.done())])
        events = []
        with self.assertRaises(FatalToolError):
            collect_run(stream_run(RunInput(
                "write", model,
                tools=CodeWorkspace(WorkspaceSettings(workspace, allow_write=True)).tools(),
                listeners=(events.append,), tool_decider=lambda _tool, _arguments: "deny",
            )))
        self.assertEqual(before, file_hashes(workspace))
        blocked = next(event for event in events if event.event_type == "action.blocked")
        self.assertEqual("denied", blocked.data["call_id"])
        self.assertTrue(blocked.run_id)
        self.assertNotIn("tool.started", [event.event_type for event in events])

    def test_stale_model_write_preserves_concurrent_user_edit(self) -> None:
        workspace = self.root / "work"
        task = prepare_workspace("bug-fix", workspace)
        model = scripted_model(task, workspace)
        artifact = workspace / "event_window.py"
        edited = artifact.read_text(encoding="utf-8") + "\n# 用户同期修改\n"
        artifact.write_text(edited, encoding="utf-8")
        with Agent(model) as agent:
            for tool in CodeWorkspace(WorkspaceSettings(workspace, allow_write=True)).tools():
                agent.add_tool(tool)
            result = agent.run(task["prompt"])
        self.assertEqual(edited, artifact.read_text(encoding="utf-8"))
        failure = next(event for event in result.events if event.event_type == "tool.failed")
        self.assertIn("file changed since it was read", str(failure.data))
        self.assertFalse(evaluate("bug-fix", workspace)["accepted"])

    def test_queue_completed_does_not_mean_artifact_accepted(self) -> None:
        workspace = self.root / "work"
        task_input = prepare_workspace("bug-fix", workspace)
        with Agent(MockModel("root")) as root:
            root.add_subagent(Agent(MockModel("已经修好并测试通过。")), name="worker")
            group = team_node(root).group_id
            runtime = TeamRuntime(team_node(root).root(), TeamSettings(max_wait_seconds=5))
            task = runtime.create_task(task_input["prompt"], source_group_id=group)
            runtime.dispatch_task(task.task_id, source_group_id=group)
            result = runtime.wait_for_task(task.task_id, source_group_id=group, timeout_seconds=5)
            self.assertEqual("completed", result["task"]["status"])
            self.assertFalse(evaluate("bug-fix", workspace)["accepted"])

    def test_cancel_before_dispatch_never_calls_model(self) -> None:
        model = MockModel("should not run")
        with Agent(MockModel("root")) as root:
            root.add_subagent(Agent(model), name="worker")
            group = team_node(root).group_id
            runtime = TeamRuntime(team_node(root).root())
            task = runtime.create_task("work", source_group_id=group)
            runtime.cancel_task(task.task_id, source_group_id=group)
            with self.assertRaisesRegex(ValueError, "cancelled -> queued"):
                runtime.dispatch_task(task.task_id, source_group_id=group)
            self.assertEqual([], model.requests)

    def test_cancel_queued_work_and_request_stop_for_running_work(self) -> None:
        entered, release, cancelled_worker_exited = Event(), Event(), Event()

        class HeldModel(MockModel):
            def stream(self, request):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test did not release the worker")
                yield from super().stream(request)

        model = HeldModel("done")
        with Agent(MockModel("root")) as root:
            root.add_subagent(Agent(model), name="worker")
            group = team_node(root).group_id
            runtime = TeamRuntime(team_node(root).root(), TeamSettings(max_wait_seconds=5))
            first = runtime.create_task("first", source_group_id=group)
            second = runtime.create_task("second", source_group_id=group)
            consume = runtime._consume

            def observe_consumer(task_id, worker) -> None:
                try:
                    consume(task_id, worker)
                finally:
                    if task_id == second.task_id:
                        cancelled_worker_exited.set()

            runtime.dispatch_task(first.task_id, source_group_id=group)
            try:
                self.assertTrue(entered.wait(5))
                # 在跑的任务不能靠改一个状态就算取消：先记录停止请求，等运行真正退出。
                with self.assertRaisesRegex(ValueError, "not a confirmed stop"):
                    runtime.cancel_task(first.task_id, source_group_id=group)
                stopping = runtime.request_task_cancellation(
                    first.task_id, "user stopped the run", source_group_id=group)
                # status 仍是执行事实（还在跑），stage 表示取消走到"停止中"。
                self.assertEqual("running", stopping.status)
                self.assertEqual("stopping", stopping.stage)
                self.assertEqual("user stopped the run", stopping.cancel_reason)
                self.assertIsNotNone(stopping.cancel_requested_at)
                self.assertTrue(runtime._cancel_check(first.task_id)())
                # 等待被取消的消费者真正退出，避免在其尚未检查状态时误判通过。
                with patch.object(runtime, "_consume", side_effect=observe_consumer):
                    runtime.dispatch_task(second.task_id, source_group_id=group, agent_name="worker")
                self.assertEqual("queued", runtime.get_task(second.task_id)["status"])
                cancelled = runtime.cancel_task(second.task_id, source_group_id=group)
                self.assertEqual("cancelled", cancelled.status)
                self.assertEqual("stopped", cancelled.stage)
            finally:
                release.set()
                finished = runtime.wait_for_task(first.task_id, source_group_id=group, timeout_seconds=5)
            # 停止请求后运行确实退出：第一阶段被记为已停止，而不是完成。
            self.assertEqual("cancelled", finished["task"]["status"])
            self.assertEqual("stopped", finished["task"]["stage"])
            self.assertIsNone(finished["task"]["stop_unknown_reason"])
            self.assertTrue(cancelled_worker_exited.wait(5))
            self.assertEqual(1, len(model.requests))

    def test_late_result_after_cancellation_is_never_delivered(self) -> None:
        # 执行体在停止请求后仍然返回结果时，这份结果不能算完成，也不能交付。
        agent = Agent(MockModel("已完成的回答"))
        self.addCleanup(agent.close)
        queue = TaskQueue(scope="local")
        task = queue.create_task("work", source_group_id="local")
        queue.configure_task_check(
            task.task_id,
            TaskCheck("text", "1", lambda: {"answer": hashlib.sha256(b"x").hexdigest()},
                      lambda: {"passed": True}),
            source_group_id="local",
        )

        def ignoring_executor(prompt: str):
            queue.request_task_cancellation(task.task_id, "user pressed stop", source_group_id="local")
            return agent.run(prompt)

        result = queue.execute_task(task.task_id, ignoring_executor, source_group_id="local")
        # 结果迟到：既不算完成也不算"已确认停止"，明确标为停止未确认。
        self.assertEqual("failed", result.status)
        self.assertEqual("stop_unknown", result.stage)
        self.assertIn("after the cancellation request", result.stop_unknown_reason)
        self.assertIsNone(result.result)
        # 结果未知时不能提交、不能交付。
        with self.assertRaisesRegex(ValueError, "completed, undelivered attempt"):
            queue.submit_task_result(task.task_id, request_id="late", source_group_id="local")


if __name__ == "__main__":
    unittest.main()

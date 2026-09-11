"""验证依赖绑定已验收产物版本、返工影响下游，以及交接与多人检视的边界。"""

import tempfile
import unittest
from pathlib import Path

from super_agent import Agent
from super_agent.core.provider import MockModel
from super_agent.skill.organization import TaskCheck
from super_agent.skill.organization_tasks import TaskQueue
from super_agent.adapter.tools import CodeWorkspace


class DependencyAndHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.first_file = self.root / "first.txt"
        self.second_file = self.root / "second.txt"
        self.first_file.write_text("draft")
        self.second_file.write_text("downstream")
        self.scope = "user/project"
        self.queue = TaskQueue(scope=self.scope)
        self.agent = Agent(MockModel("done"))
        self.addCleanup(self.agent.close)

    def check(self, path: Path, expected: str, version: str = "1") -> TaskCheck:
        return TaskCheck(f"file-{path.name}", version, CodeWorkspace(self.root).snapshot_files,
                         lambda: {"passed": path.read_text() == expected})

    def make(self, prompt: str, *, depends_on: tuple[str, ...] = ()):
        return self.queue.create_task(prompt, source_group_id=self.scope, depends_on=depends_on)

    def accept(self, task, check: TaskCheck, content: str, path: Path) -> None:
        path.write_text(content)
        self.queue.configure_task_check(task.task_id, check, source_group_id=self.scope)
        self.queue.execute_task(task.task_id, self.agent.run, source_group_id=self.scope)
        self.queue.submit_task_result(task.task_id, request_id=f"sub-{task.task_id}",
                                      source_group_id=self.scope)
        self.queue.check_task_result(task.task_id, source_group_id=self.scope)

    def test_downstream_cannot_start_before_dependency_is_accepted(self) -> None:
        upstream = self.make("prepare input")
        downstream = self.make("use input", depends_on=(upstream.task_id,))
        # 前置只运行完不构成依赖满足：必须被验收通过。
        self.queue.configure_task_check(upstream.task_id, self.check(self.first_file, "ready"),
                                        source_group_id=self.scope)
        self.queue.execute_task(upstream.task_id, self.agent.run, source_group_id=self.scope)
        self.assertEqual("completed", self.queue.get_task(upstream.task_id)["status"])
        status = self.queue.dependency_status(downstream.task_id, source_group_id=self.scope)
        self.assertFalse(status["satisfied"])
        self.assertEqual([upstream.task_id], status["blocked_by"])
        with self.assertRaisesRegex(ValueError, "dependency is not accepted yet"):
            self.queue.execute_task(downstream.task_id, self.agent.run, source_group_id=self.scope)
        # 验收通过后依赖满足，下游可以开始。
        self.first_file.write_text("ready")
        self.queue.submit_task_result(upstream.task_id, request_id="u1", source_group_id=self.scope)
        self.queue.check_task_result(upstream.task_id, source_group_id=self.scope)
        self.assertTrue(self.queue.dependency_status(downstream.task_id, source_group_id=self.scope)["satisfied"])
        self.queue.execute_task(downstream.task_id, self.agent.run, source_group_id=self.scope)

    def test_dependency_binds_the_accepted_version_and_rework_breaks_it(self) -> None:
        upstream = self.make("prepare input")
        downstream = self.make("use input", depends_on=(upstream.task_id,))
        self.accept(upstream, self.check(self.first_file, "ready"), "ready", self.first_file)
        satisfied = self.queue.dependency_status(downstream.task_id, source_group_id=self.scope)
        self.assertTrue(satisfied["satisfied"])
        bound = satisfied["dependencies"][0]
        self.assertIsNotNone(bound["accepted_submission_id"])
        self.assertIsNotNone(bound["accepted_artifact_sha256"])

        # 前置返工：同一任务新尝试，旧的已验收版本不再成立，下游重新阻塞。
        self.queue.return_task_for_changes(upstream.task_id, "input changed", source_group_id=self.scope)
        broken = self.queue.dependency_status(downstream.task_id, source_group_id=self.scope)
        self.assertFalse(broken["satisfied"])
        self.assertEqual("dependency was returned for changes", broken["dependencies"][0]["reason"])
        with self.assertRaisesRegex(ValueError, "dependency is not accepted yet"):
            self.queue.execute_task(downstream.task_id, self.agent.run, source_group_id=self.scope)

        # 重新验收后依赖恢复，但绑定的是新版本，不再是返工前那份提交。
        self.first_file.write_text("ready-2")
        self.queue.configure_task_check(upstream.task_id, self.check(self.first_file, "ready-2", "2"),
                                        source_group_id=self.scope)
        self.queue.execute_task(upstream.task_id, self.agent.run, source_group_id=self.scope)
        self.queue.submit_task_result(upstream.task_id, request_id="u2", source_group_id=self.scope)
        self.queue.check_task_result(upstream.task_id, source_group_id=self.scope)
        reaccepted = self.queue.dependency_status(downstream.task_id, source_group_id=self.scope)
        self.assertTrue(reaccepted["satisfied"])
        self.assertNotEqual(bound["accepted_submission_id"],
                            reaccepted["dependencies"][0]["accepted_submission_id"])
        self.assertNotEqual(bound["accepted_artifact_sha256"],
                            reaccepted["dependencies"][0]["accepted_artifact_sha256"])
        self.assertEqual("ready-2", self.first_file.read_text())

    def test_dependency_cycle_reports_the_blocking_chain(self) -> None:
        # 依赖只在建单时设定，因此"新任务自己出现在前置的祖先链里"才构成环。
        first = self.make("a")
        second = self.make("b", depends_on=(first.task_id,))
        # 把 first 当作新任务 ID：从 second 出发能到达 first，说明会成环并给出完整链条。
        cycle = self.queue._blocking_chain(second, first.task_id)
        self.assertEqual([second.task_id, first.task_id], cycle)
        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            self.queue._resolve_dependencies((second.task_id,), self.scope, new_task_id=first.task_id)
        # 全新 ID 不可能被任何已有任务依赖，因此真实的建单流程不会误报环。
        fresh = self.make("c", depends_on=(second.task_id,))
        self.assertEqual(1, len(self.queue.get_task(fresh.task_id)["dependencies"]))
        self.assertEqual([], self.queue._blocking_chain(second, fresh.task_id))

    def test_duplicate_and_foreign_dependencies_are_rejected(self) -> None:
        task = self.make("a")
        # 同一前置重复列出只记一次。
        repeated = self.make("b", depends_on=(task.task_id, task.task_id))
        self.assertEqual(1, len(repeated.dependencies))
        # 别的队列/作用域的任务本队列根本看不到，不能拿它当前置。
        other = TaskQueue(scope="someone/else")
        foreign = other.create_task("x", source_group_id="someone/else")
        with self.assertRaises(KeyError):
            self.queue.create_task("y", source_group_id=self.scope, depends_on=(foreign.task_id,))
        with self.assertRaises(KeyError):
            self.queue.create_task("z", source_group_id=self.scope, depends_on=("task-missing",))
        # 同一作用域但未登记的 ID 同样是未知前置。
        with self.assertRaises(PermissionError):
            self.queue.create_task("w", source_group_id="other/group", depends_on=(task.task_id,))

    def test_handoff_is_traceable_and_does_not_change_acceptance(self) -> None:
        task = self.make("work")
        self.accept(task, self.check(self.second_file, "downstream"), "downstream", self.second_file)
        delivered = self.queue.deliver_task_result(task.task_id, source_group_id=self.scope)
        moved = self.queue.record_assignment(task.task_id, source_group_id=self.scope,
                                             agent_name="reviewer", reason="second opinion")
        self.assertEqual("reviewer", moved.agent_name)
        self.assertEqual(1, len(moved.assignments))
        self.assertEqual("second opinion", moved.assignments[0]["reason"])
        # 交接不重新验收，也不撤销已有交付。
        self.assertEqual(delivered.delivery, moved.delivery)
        self.assertTrue(self.queue.get_task(task.task_id, source_group_id=self.scope)["acceptance_current"])

    def test_independent_findings_are_recorded_but_never_replace_acceptance(self) -> None:
        task = self.make("work")
        self.queue.configure_task_check(task.task_id, self.check(self.second_file, "correct"),
                                        source_group_id=self.scope)
        self.queue.execute_task(task.task_id, self.agent.run, source_group_id=self.scope)
        self.queue.submit_task_result(task.task_id, request_id="s1", source_group_id=self.scope)
        finding = self.queue.record_review_finding(task.task_id, source_group_id=self.scope,
                                                   reviewer="agent-b", finding="边界未覆盖",
                                                   location="second.txt:3")
        submission = finding.submissions[-1]
        self.assertEqual(1, len(submission["findings"]))
        self.assertEqual("边界未覆盖", submission["findings"][0]["finding"])
        # 检视意见不产生 reviews 结论：没有宿主检查就不能交付。
        self.assertEqual([], submission["reviews"])
        with self.assertRaisesRegex(ValueError, "current accepted"):
            self.queue.deliver_task_result(task.task_id, source_group_id=self.scope)
        failed = self.queue.check_task_result(task.task_id, source_group_id=self.scope)
        self.assertEqual("failed", failed.submissions[-1]["reviews"][-1]["outcome"])
        self.assertEqual(1, len(failed.submissions[-1]["findings"]))

    def test_review_finding_requires_a_submission(self) -> None:
        task = self.make("work")
        with self.assertRaisesRegex(ValueError, "requires a submission"):
            self.queue.record_review_finding(task.task_id, source_group_id=self.scope,
                                             reviewer="agent-b", finding="nothing to review")

    def test_model_tools_expose_dependency_state_and_findings_only(self) -> None:
        from super_agent.core.event import RunIdentity
        from super_agent.core.run import ToolContext
        from super_agent.skill.organization_tools import task_tools as build_task_tools

        upstream = self.make("prepare input")
        downstream = self.make("use input", depends_on=(upstream.task_id,))
        tools = {tool.name: tool for tool in build_task_tools(self.queue, self.scope, downstream.task_id)}
        self.assertIn("read_agent_task_dependencies", tools)
        self.assertIn("record_agent_task_finding", tools)
        # 验收与交付仍然没有模型工具。
        for forbidden in ("check_agent_task", "deliver_agent_task", "accept_agent_task"):
            self.assertNotIn(forbidden, tools)

        class _Session:
            identity = RunIdentity(agent_name="reviewer")

        context = ToolContext(_Session(), lambda _kind, _data: None)
        dependencies = tools["read_agent_task_dependencies"].handler({}, context)
        self.assertFalse(dependencies["satisfied"])
        self.assertEqual([upstream.task_id], dependencies["blocked_by"])

        self.accept(upstream, self.check(self.first_file, "ready"), "ready", self.first_file)
        self.queue.configure_task_check(downstream.task_id, self.check(self.second_file, "done"),
                                        source_group_id=self.scope)
        self.queue.execute_task(downstream.task_id, self.agent.run, source_group_id=self.scope)
        self.queue.submit_task_result(downstream.task_id, request_id="d1", source_group_id=self.scope)
        # 依赖已满足，且模型可以记录检视意见——但这不产生通过结论。
        self.assertTrue(tools["read_agent_task_dependencies"].handler({}, context)["satisfied"])
        recorded = tools["record_agent_task_finding"].handler({"finding": "缺少边界用例"}, context)
        submission = recorded["submissions"][-1]
        self.assertEqual("reviewer", submission["findings"][0]["reviewer"])
        self.assertEqual([], submission["reviews"])
        with self.assertRaisesRegex(ValueError, "current accepted"):
            self.queue.deliver_task_result(downstream.task_id, source_group_id=self.scope)

    def test_wait_and_queue_ownership_are_unchanged(self) -> None:
        # 依赖不新增调度器：还是同一个 TaskQueue，等待仍由同一版本条件变量驱动。
        task = self.make("work")
        self.assertIsInstance(self.queue, TaskQueue)
        created = self.queue.get_task(task.task_id)
        self.assertEqual(self.queue.version, created["version"])
        self.assertEqual([], created["dependencies"])
        self.assertNotIn("dependencies_satisfied", created)


if __name__ == "__main__":
    unittest.main()

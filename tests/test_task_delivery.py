"""以真实文件和外部检查验证任务交付，不使用模型自报的通过结论。"""

from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from super_agent import Agent
from super_agent.core.provider import MockModel
from super_agent.skill.organization import TaskCheck
from super_agent.skill.organization_tasks import TaskQueue
from super_agent.skill.organization_tools import task_tools as build_task_tools
from super_agent.adapter.cli import CliConfig, _task_command
from super_agent.adapter.tools import CodeWorkspace


class _ToolSession:
    """模型工具只需要运行身份；这里只提供真实接口用到的那部分。"""

    def __init__(self) -> None:
        from super_agent.core.event import RunIdentity

        self.identity = RunIdentity(agent_name="super-agent")


def _tool_context(_agent: Agent) -> object:
    from super_agent.core.run import ToolContext

    return ToolContext(_ToolSession(), lambda _kind, _data: None)


class TaskDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / "answer.txt"
        self.file.write_text("wrong")
        self.queue = TaskQueue(scope="user/project")
        self.scope = {"source_group_id": "user/project"}
        self.task = self.queue.create_task("fix", **self.scope)
        self.check = TaskCheck("answer", "1", CodeWorkspace(self.root).snapshot_files,
                               lambda: {"passed": self.file.read_text() == "correct"})
        self.queue.configure_task_check(self.task.task_id, self.check, **self.scope)
        self.agent = Agent(MockModel("I finished"))

    def execute_submit(self, request_id="first"):
        self.queue.execute_task(self.task.task_id, self.agent.run, **self.scope)
        return self.queue.submit_task_result(self.task.task_id, request_id=request_id, **self.scope)

    def test_false_completion_rework_then_delivery(self) -> None:
        task = self.execute_submit()
        self.assertEqual("completed", task.status)
        failed = self.queue.check_task_result(task.task_id, **self.scope)
        self.assertEqual("needs_attention", failed.stage)
        with self.assertRaisesRegex(ValueError, "current accepted"):
            self.queue.deliver_task_result(task.task_id, **self.scope)
        self.queue.return_task_for_changes(task.task_id, "still wrong", **self.scope)
        self.file.write_text("correct")
        second = self.execute_submit("second")
        self.assertNotEqual(task.attempt_id, second.attempt_id)
        self.assertEqual(2, len(second.submissions))
        self.queue.check_task_result(task.task_id, **self.scope)
        delivered = self.queue.deliver_task_result(task.task_id, **self.scope)
        self.assertEqual("delivered", delivered.stage)
        self.assertEqual(delivered, self.queue.deliver_task_result(task.task_id, **self.scope))

    def test_changed_artifact_or_criteria_invalidates_acceptance(self) -> None:
        self.file.write_text("correct")
        self.execute_submit()
        self.queue.check_task_result(self.task.task_id, **self.scope)
        self.file.write_text("changed")
        self.assertFalse(self.queue.get_task(self.task.task_id, **self.scope)["acceptance_current"])
        with self.assertRaises(ValueError):
            self.queue.deliver_task_result(self.task.task_id, **self.scope)
        self.file.write_text("correct")
        # 通过结论未退回前不能改标准，否则提交与标准失去对应关系。
        with self.assertRaisesRegex(ValueError, "returning the accepted attempt"):
            self.queue.configure_task_check(self.task.task_id, replace(self.check, version="2"), **self.scope)
        self.queue.return_task_for_changes(self.task.task_id, "raise the bar", **self.scope)
        self.queue.configure_task_check(self.task.task_id, replace(self.check, version="2"), **self.scope)
        self.assertEqual(["answer", "2"], self.queue.get_task(self.task.task_id, **self.scope)["criteria"])
        # 已退回尝试的提交保留为历史，不能被拿来顶替当前尝试的验收。
        with self.assertRaisesRegex(ValueError, "current attempt"):
            self.queue.check_task_result(self.task.task_id, **self.scope)
        self.execute_submit("v2")
        # 旧标准下通过的提交不能为当前标准交付；当前提交按新标准重新验收后可以交付。
        checked = self.queue.check_task_result(self.task.task_id, **self.scope)
        self.assertEqual("accepted", checked.stage)
        self.assertEqual(["answer", "2"], checked.submissions[-1]["criteria"])
        self.assertEqual("delivered", self.queue.deliver_task_result(self.task.task_id, **self.scope).stage)

    def test_one_submission_per_attempt_and_copies_are_isolated(self) -> None:
        first = self.execute_submit()
        # 同一次尝试只留一份提交：重复提交被拒绝，不会产生第二份候选成果或第二次效果。
        with self.assertRaisesRegex(ValueError, "already submitted"):
            self.queue.submit_task_result(self.task.task_id, request_id="first", **self.scope)
        self.assertEqual(1, len(self.queue.get_task(self.task.task_id, **self.scope)["submissions"]))
        # 返回给调用方的副本可自由修改，不影响队列内的业务状态。
        first.submissions[-1]["artifacts"]["answer.txt"] = "forged"
        self.assertNotEqual("forged", self.queue.get_task(self.task.task_id)["submissions"][-1]["artifacts"]["answer.txt"])
        self.file.write_text("new")
        self.queue.return_task_for_changes(self.task.task_id, "redo", **self.scope)
        second = self.execute_submit("second")
        self.assertEqual(2, len(second.submissions))

    def test_scope_empty_check_and_pre_execution_submission_rejected(self) -> None:
        with self.assertRaises(PermissionError):
            self.queue.configure_task_check(self.task.task_id, self.check, source_group_id="other")
        with self.assertRaises(ValueError):
            self.queue.submit_task_result(self.task.task_id, request_id="early", **self.scope)
        self.queue.configure_task_check(self.task.task_id, replace(self.check, version="2", snapshot=lambda: {}), **self.scope)
        with self.assertRaises(ValueError):
            self.execute_submit()

    def test_evaluator_failure_is_not_a_success(self) -> None:
        def broken():
            raise TimeoutError("test unavailable")
        self.queue.configure_task_check(self.task.task_id, replace(self.check, version="2", check=broken), **self.scope)
        self.execute_submit()
        task = self.queue.check_task_result(self.task.task_id, **self.scope)
        self.assertEqual("unverified", task.submissions[-1]["reviews"][-1]["outcome"])

    def test_mutation_during_check_and_concurrent_task_update_are_rejected(self) -> None:
        def mutate():
            self.file.write_text("changed during check")
            return {"passed": True}
        self.queue.configure_task_check(self.task.task_id, replace(self.check, version="2", check=mutate), **self.scope)
        self.execute_submit()
        result = self.queue.check_task_result(self.task.task_id, **self.scope)
        self.assertEqual("stale", result.submissions[-1]["reviews"][-1]["outcome"])

        # 检查执行期间任务被并发改动时，不得用过期版本覆盖当前结论。
        def change_task():
            self.queue.return_task_for_changes(self.task.task_id, "new input", **self.scope)
            return {"passed": True}
        self.queue.configure_task_check(self.task.task_id, replace(self.check, version="3", check=change_task), **self.scope)
        self.queue.return_task_for_changes(self.task.task_id, "restart for concurrency check", **self.scope)
        self.execute_submit("second")
        with self.assertRaisesRegex(RuntimeError, "changed concurrently"):
            self.queue.check_task_result(self.task.task_id, **self.scope)

    def test_record_failure_does_not_publish_state(self) -> None:
        def fail(_kind, _data):
            raise OSError("disk full")
        self.queue.record_event = fail
        before = self.queue.version
        with self.assertRaises(OSError):
            self.queue.execute_task(self.task.task_id, self.agent.run, **self.scope)
        self.assertEqual(before, self.queue.version)
        self.assertEqual([], self.agent.model.requests)

    def test_text_result_can_be_checked_without_files(self) -> None:
        queue = TaskQueue()
        task = queue.create_task("answer", source_group_id="local")
        result = "answer"
        check = TaskCheck("text", "1", lambda: {"answer": hashlib.sha256(result.encode()).hexdigest()},
                          lambda: {"passed": result == "answer"})
        queue.configure_task_check(task.task_id, check, source_group_id="local")
        queue.execute_task(task.task_id, self.agent.run, source_group_id="local")
        queue.submit_task_result(task.task_id, request_id="1", source_group_id="local")
        queue.check_task_result(task.task_id, source_group_id="local")
        self.assertEqual("delivered", queue.deliver_task_result(task.task_id, source_group_id="local").stage)

    def test_model_tools_cannot_self_accept_or_deliver(self) -> None:
        tools = {tool.name: tool for tool in build_task_tools(self.queue, self.scope["source_group_id"], self.task.task_id)}
        # 模型侧只暴露读取、提交和请求退回；验收与交付没有对应工具。
        self.assertEqual({"read_agent_task", "submit_agent_task_result", "return_agent_task_for_changes"}, set(tools))
        self.queue.execute_task(self.task.task_id, self.agent.run, **self.scope)
        submitted = tools["submit_agent_task_result"].handler({}, _tool_context(self.agent))
        self.assertEqual("awaiting_review", submitted["stage"])
        self.assertEqual(1, len(submitted["submissions"]))
        # 模型自报成功不产生通过结论，交付仍被拒绝。
        with self.assertRaisesRegex(ValueError, "current accepted"):
            self.queue.deliver_task_result(self.task.task_id, **self.scope)
        returned = tools["return_agent_task_for_changes"].handler({"reason": "blocked"}, _tool_context(self.agent))
        self.assertEqual("pending", returned["stage"])
        self.assertEqual(["blocked"], returned["return_reasons"])

    def test_cli_uses_declared_check_and_same_queue(self) -> None:
        config = self.root / "code.toml"
        command = [sys.executable, "-I", "-B", "-c", "from pathlib import Path; assert Path('answer.txt').read_text() == 'correct'"]
        config.write_text('[workspace]\nroot = "."\n[actions]\nexecute = "allow"\n[verification]\ncommands = [' + json.dumps(command) + ']\n')
        cli = CliConfig(user_id="user/project", code_config=str(config), output="json")
        queue = TaskQueue(scope=cli.user_id)
        with redirect_stdout(io.StringIO()):
            _task_command(self.agent, cli, "/task new fix", queue)
            _task_command(self.agent, cli, "/task run", queue)
            self.file.write_text("correct")
            _task_command(self.agent, cli, "/task submit", queue)
            _task_command(self.agent, cli, "/task check", queue)
            _task_command(self.agent, cli, "/task deliver", queue)
        self.assertEqual("delivered", queue.list_tasks(cli.user_id)[0]["stage"])

    def test_cli_rejects_false_completion_then_delivers_the_real_fix(self) -> None:
        """T1 正向关口：声明命令的宿主检查决定能否交付，模型自报成功不算数。"""
        config = self.root / "code.toml"
        command = [sys.executable, "-I", "-B", "-c",
                   "from pathlib import Path; assert Path('answer.txt').read_text() == 'correct'"]
        config.write_text('[workspace]\nroot = "."\n[actions]\nexecute = "allow"\n[verification]\ncommands = ['
                          + json.dumps(command) + ']\n')
        cli = CliConfig(user_id="user/project", code_config=str(config), output="json")
        queue = TaskQueue(scope=cli.user_id)
        with redirect_stdout(io.StringIO()):
            _task_command(self.agent, cli, "/task new 修复 answer.txt", queue)
            _task_command(self.agent, cli, "/task run", queue)
            # 模型说“完成了”，但原文件没改：检查失败，不能交付。
            _task_command(self.agent, cli, "/task submit", queue)
            _task_command(self.agent, cli, "/task check", queue)
            task_id = queue.list_tasks(cli.user_id)[0]["task_id"]
            self.assertEqual("needs_attention", queue.list_tasks(cli.user_id)[0]["stage"])
            with self.assertRaisesRegex(ValueError, "current accepted"):
                _task_command(self.agent, cli, "/task deliver", queue)
            # 退回后真正修好，第二次尝试通过检查并交付。
            self.file.write_text("correct")
            _task_command(self.agent, cli, f"/task return {task_id} 文件未修改", queue)
            _task_command(self.agent, cli, "/task run", queue)
            _task_command(self.agent, cli, "/task submit", queue)
            _task_command(self.agent, cli, "/task check", queue)
            _task_command(self.agent, cli, "/task deliver", queue)
        task = queue.list_tasks(cli.user_id)[0]
        self.assertEqual("delivered", task["stage"])
        self.assertEqual("completed", task["status"])
        self.assertEqual(2, len(task["submissions"]))
        self.assertEqual(2, task["attempts"])
        self.assertEqual("failed", task["submissions"][0]["reviews"][-1]["outcome"])
        self.assertEqual("passed", task["submissions"][1]["reviews"][-1]["outcome"])


if __name__ == "__main__":
    unittest.main()

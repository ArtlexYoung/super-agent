import unittest

from core.event import RunIdentity
from core.provider import MockModel
from core.run import RunSession, RuntimeLifecycle, ToolContext
from skill.organization import AgentTreeSettings, agent_group_node
from skill.organization_runtime import AgentTreeRuntime
from super_agent import Agent


class RuntimeLifecycleTests(unittest.TestCase):
    def test_single_run_exposes_a_completed_runtime_snapshot(self):
        result = Agent(MockModel("answer"), name="main").run("hello")

        snapshot = result.runtime_lifecycle
        self.assertEqual(result.run_id, snapshot["root_run_id"])
        self.assertEqual(1, snapshot["run_count"])
        self.assertEqual(0, snapshot["active_runs"])
        self.assertEqual("completed", snapshot["runs"][0]["status"])
        self.assertNotIn("answer", repr(snapshot))

    def test_create_task_tool_uses_the_calling_runtime_lifecycle(self):
        root = Agent(MockModel("root"), name="root")
        runtime = AgentTreeRuntime(agent_group_node(root).root())
        group_id = agent_group_node(root).group_id
        identity = RunIdentity(agent_name="root", run_id="parent-run")
        lifecycle = RuntimeLifecycle(identity.run_id)
        session = RunSession(
            identity,
            [],
            [],
            {},
            runtime_lifecycle=lifecycle,
        )
        context = ToolContext(session, lambda _event, _data: None)  # type: ignore[arg-type]
        tool = next(
            item for item in runtime.tools(group_id) if item.name == "create_agent_task"
        )

        created = tool.handler({"prompt": "child work"}, context)

        snapshot = lifecycle.snapshot()
        self.assertEqual(created["task_id"], snapshot["tasks"][0]["task_id"])
        self.assertEqual("created", snapshot["tasks"][0]["status"])

    def test_child_task_and_run_share_the_parent_lifecycle(self):
        root = Agent(MockModel("root"), name="root")
        root.add_subagent(Agent(MockModel("child result")), name="child")
        runtime = AgentTreeRuntime(
            agent_group_node(root).root(), AgentTreeSettings(max_wait_seconds=2)
        )
        group_id = agent_group_node(root).group_id
        parent = RunIdentity(agent_name="root", run_id="parent-run")
        lifecycle = RuntimeLifecycle(parent.run_id)
        lifecycle.record_run_event(parent, "run.started")

        task = runtime.create_task(
            "child work",
            source_group_id=group_id,
            runtime_lifecycle=lifecycle,
        )
        runtime.dispatch_task(
            task.task_id,
            source_group_id=group_id,
            parent_identity=parent,
            runtime_lifecycle=lifecycle,
        )
        runtime.wait_for_tasks(
            "selected_tasks_finished",
            group_id=group_id,
            timeout_seconds=2,
            task_ids=(task.task_id,),
        )

        snapshot = lifecycle.snapshot()
        self.assertEqual("parent-run", snapshot["root_run_id"])
        self.assertEqual(2, snapshot["run_count"])
        self.assertEqual(
            {"parent-run", next(item["run_id"] for item in snapshot["runs"] if item["run_id"] != "parent-run")},
            {item["run_id"] for item in snapshot["runs"]},
        )
        child_run = next(item for item in snapshot["runs"] if item["run_id"] != "parent-run")
        self.assertEqual("parent-run", child_run["parent_run_id"])
        self.assertEqual("completed", child_run["status"])
        self.assertEqual("completed", snapshot["tasks"][0]["status"])
        self.assertEqual(0, snapshot["active_tasks"])


if __name__ == "__main__":
    unittest.main()

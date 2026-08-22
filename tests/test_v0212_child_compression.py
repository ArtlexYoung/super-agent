import unittest

from core.provider import MockModel
from skill.organization import AgentTreeSettings, agent_group_node
from skill.organization_runtime import AgentTreeRuntime
from super_agent import Agent


class ChildCompressionTests(unittest.TestCase):
    def _run_task(self, settings: AgentTreeSettings, response: str) -> dict[str, object]:
        root = Agent(MockModel("root"), name="root")
        root.add_subagent(Agent(MockModel(response)), name="worker")
        runtime = AgentTreeRuntime(agent_group_node(root).root(), settings)
        group_id = agent_group_node(root).group_id
        task = runtime.create_task("work", source_group_id=group_id)
        runtime.dispatch_task(task.task_id, source_group_id=group_id)
        runtime.wait_for_tasks(
            "selected_tasks_finished",
            group_id=group_id,
            timeout_seconds=2,
            task_ids=(task.task_id,),
        )
        return runtime.list_tasks(group_id)[0]["result"]

    def test_adaptive_mode_compresses_a_large_first_result(self):
        result = self._run_task(
            AgentTreeSettings(
                max_wait_seconds=2,
                max_full_result_characters=100,
                summary_characters=20,
            ),
            "x" * 1_000,
        )

        self.assertEqual("summary", result["record_mode"])
        self.assertEqual("adaptive_result_size", result["compression_reason"])
        self.assertGreater(result["text_characters"], 100)
        self.assertNotIn("events", result)

    def test_explicit_full_mode_is_not_silently_downgraded(self):
        result = self._run_task(
            AgentTreeSettings(
                max_wait_seconds=2,
                record_mode="full",
                max_full_result_characters=1,
            ),
            "x" * 1_000,
        )

        self.assertEqual("full", result["record_mode"])
        self.assertEqual("configured", result["compression_reason"])
        self.assertIn("events", result)


if __name__ == "__main__":
    unittest.main()

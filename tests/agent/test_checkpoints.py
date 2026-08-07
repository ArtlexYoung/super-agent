import tempfile
import unittest

from core.config import CommonConfig
from core.runtime.run import create_checkpoint_data
from core.provider.chat import MockProvider
from super_agent import Agent


class CheckpointTests(unittest.TestCase):
    def test_checkpoint_data_keeps_only_hashes_and_keys(self) -> None:
        data = create_checkpoint_data(
            "run-1",
            "model-step",
            {"step": 2, "secret_text": "model output must not be stored"},
        )

        self.assertEqual("run-1", data["run_id"])
        self.assertEqual(["secret_text", "step"], data["state_keys"])
        self.assertNotIn("model output", str(data))
        self.assertEqual(64, len(data["state_sha256"]))

    def test_user_can_list_and_resume_from_a_recorded_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = MockProvider("finished")
            agent = Agent(
                CommonConfig.create_default(tmp),
                provider=provider,
                use_storage=True,
            )
            first = agent.run("Inspect the project")
            user = agent.for_user("local")

            checkpoints = user.runs.list_checkpoints(first.run_id)
            resumed = user.runs.resume(
                first.run_id,
                "Continue from the latest verified checkpoint",
                checkpoint_id=str(checkpoints[-1]["checkpoint_id"]),
            )
            events = agent._create_event_store().read_run_events(
                resumed.run_id,
                include_sensitive=True,
            )

            self.assertGreaterEqual(len(checkpoints), 2)
            self.assertEqual(first.run_id, checkpoints[-1]["run_id"])
            self.assertEqual("finished", resumed.text)
            resumed_event = next(
                event for event in events if event.event_type == "run.resumed"
            )
            self.assertEqual(first.run_id, resumed_event.data["source_run_id"])
            self.assertEqual(
                checkpoints[-1]["checkpoint_id"],
                resumed_event.data["checkpoint_id"],
            )
            checkpoint_events = [
                event for event in events if event.event_type == "run.checkpoint.created"
            ]
            self.assertNotIn("Inspect the project", str(checkpoint_events))
            self.assertIn(
                str(checkpoints[-1]["checkpoint_id"]),
                provider.last_messages[0]["content"],
            )

    def test_resume_rejects_an_unknown_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                CommonConfig.create_default(tmp),
                provider=MockProvider("finished"),
                use_storage=True,
            )
            result = agent.run("hello")

            with self.assertRaisesRegex(KeyError, "checkpoint not found"):
                agent.for_user("local").runs.resume(
                    result.run_id,
                    "continue",
                    checkpoint_id="checkpoint-missing",
                )


if __name__ == "__main__":
    unittest.main()

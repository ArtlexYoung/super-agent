import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from super_agent import Agent
from core.config import CommonConfig
from core.models import AgentRunOptions
from core.provider import MockProvider


class StatelessRuntimeTests(unittest.TestCase):
    def test_user_binding_does_not_import_optional_management_domains(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        script = """
import json
import sys
from super_agent import Agent

user = Agent().for_user("alice")
assert user.user_id == "alice"
blocked = (
    "skill.learning",
    "core.state.memory",
    "skill.learning.update",
    "skill.runtime.files.models",
)
print(json.dumps(sorted(name for name in sys.modules if name.startswith(blocked))))
"""
        completed = _run_fresh_process(repository_root, script)

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual([], json.loads(completed.stdout))

    def test_persistent_event_log_does_not_require_optional_domain_state(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        script = """
import json
import sys
import tempfile
from pathlib import Path
from super_agent import Agent
from core.config import CommonConfig
from core.models import AgentRunOptions
from core.provider import MockProvider

with tempfile.TemporaryDirectory() as temporary_directory:
    config = CommonConfig.create_default(Path(temporary_directory))
    agent = Agent(config, provider=MockProvider("finished"), use_storage=True)
    result = agent.run("hello")
assert result.text == "finished"
blocked = (
    "skill.learning.records",
    "skill.learning.runs",
    "core.state.memory",
    "skill.learning.update",
    "core.state.memory_service",
)
print(json.dumps(sorted(name for name in sys.modules if name.startswith(blocked))))
"""
        completed = _run_fresh_process(repository_root, script)

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual([], json.loads(completed.stdout))

    def test_stateless_run_does_not_import_optional_runtime_layers(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        script = """
import json
import sys
import tempfile
from pathlib import Path
from super_agent import Agent
from core.config import CommonConfig
from core.provider import MockProvider

with tempfile.TemporaryDirectory() as temporary_directory:
    config = CommonConfig.create_default(Path(temporary_directory))
    result = Agent(config, provider=MockProvider("finished")).run("hello")
assert result.text == "finished"
blocked = (
    "skill.learning.records",
    "skill.learning.runs",
    "core.state.memory",
    "core.state.store",
    "adapter.storage",
    "skill.learning.update",
    "core.state.memory_service",
)
print(json.dumps(sorted(name for name in sys.modules if name.startswith(blocked))))
"""
        completed = _run_fresh_process(repository_root, script)

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual([], json.loads(completed.stdout))

    def test_stateless_run_uses_no_backend_and_returns_ordered_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = CommonConfig.create_default(Path(tmp))
            received = []
            agent = Agent(config, provider=MockProvider("finished"))

            result = agent.run(
                "hello",
                run_options=AgentRunOptions(event_listener=received.append),
            )

            self.assertEqual("finished", result.text)
            self.assertEqual("model-loop", result.workflow)
            self.assertFalse(config.storage.path.exists())
            self.assertEqual(received, result.events)
            self.assertEqual(
                list(range(1, len(result.events) + 1)),
                [event.sequence for event in result.events],
            )
            self.assertEqual("run.started", result.events[0].event_type)
            self.assertEqual("run.completed", result.events[-1].event_type)
            scheduled = next(
                event.data
                for event in result.events
                if event.event_type == "task.scheduled"
            )
            self.assertEqual("model_loop", scheduled["selection"])
            self.assertEqual([], scheduled["skills"])
            self.assertNotIn("runtime.locked", [event.event_type for event in result.events])

    def test_pure_model_run_does_not_create_action_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = CommonConfig.create_default(Path(tmp))
            agent = Agent(config, provider=MockProvider("finished"), use_storage=False)

            result = agent.run("hello")

            self.assertEqual("finished", result.text)
            self.assertIsNone(agent._action_rules_value)

    def test_stateless_conversation_fails_instead_of_creating_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = CommonConfig.create_default(Path(tmp))
            agent = Agent(config, provider=MockProvider(), use_storage=False)

            with self.assertRaisesRegex(
                RuntimeError,
                "conversation history requires Runtime storage",
            ):
                agent.run("hello", conversation_id="conversation-1")

            self.assertFalse(config.storage.path.exists())

    def test_task_skill_runs_without_storage_or_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = CommonConfig.create_default(Path(tmp))
            agent = Agent(config, provider=MockProvider("ok"), use_storage=False)

            result = agent.run("hello", skill="common")

            self.assertEqual(["task:common"], result.skills)
            self.assertFalse(config.storage.path.exists())

    def test_explicit_backend_conflicts_with_disabled_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = CommonConfig.create_default(Path(tmp))
            stateful = Agent(config, provider=MockProvider(), use_storage=True)
            _ = stateful.runtime

            with self.assertRaisesRegex(
                ValueError,
                "storage cannot be combined with use_storage=False",
            ):
                Agent(
                    config,
                    provider=MockProvider(),
                    storage=stateful._storage,
                    use_storage=False,
                )


def _run_fresh_process(
    repository_root: Path,
    script: str,
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(repository_root / "src"),
        }
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from agents.agent import Agent
from cli import main
from runtime.config import AgentConfig
from runtime.evaluation import (
    EvaluationResult,
    EvaluationSource,
    EvaluationTarget,
    EvaluationTokenUsage,
    create_evaluation_record,
)
from runtime.evolution.scheduler import (
    AutonomousEvolutionScheduler,
    EvolutionScheduleTarget,
)


class EvolutionCliTests(unittest.TestCase):
    def test_cli_lists_shows_and_dismisses_one_users_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_config(Path(tmp))
            agent = Agent(AgentConfig.load_from_file(config_path))
            target = EvaluationTarget(
                target_type="skill",
                key="prompt:writer",
                name="writer",
                version="0.1.0",
                content_sha256="a" * 64,
                function_group="writing",
            )
            store = agent.runtime.create_store("alpha")
            store.append_evaluation_records(
                [
                    create_evaluation_record(
                        target,
                        EvaluationSource(source_type="agent_run", run_id="run-1"),
                        EvaluationResult(
                            success=False,
                            score=0.0,
                            token_usage=EvaluationTokenUsage(10, 0),
                            latency_ms=20,
                            error_type="RuntimeError",
                            checks=["fail"],
                        ),
                        created_at=datetime(2026, 7, 1, tzinfo=UTC),
                    )
                ]
            )
            schedule = AutonomousEvolutionScheduler(store).review_evolution_targets(
                [
                    EvolutionScheduleTarget(
                        target=target,
                        agent_created=True,
                        agent_can_update=True,
                        supports_evolution=True,
                        freshness=40,
                    )
                ]
            )[0]

            listed = _run_json_cli(
                [
                    "evolution",
                    "list",
                    "--config",
                    str(config_path),
                    "--user-id",
                    "alpha",
                    "--output",
                    "json",
                ]
            )
            isolated = _run_json_cli(
                [
                    "evolution",
                    "list",
                    "--config",
                    str(config_path),
                    "--user-id",
                    "beta",
                    "--output",
                    "json",
                ]
            )
            shown = _run_json_cli(
                [
                    "evolution",
                    "show",
                    "--config",
                    str(config_path),
                    "--user-id",
                    "alpha",
                    "--schedule-id",
                    schedule.schedule_id,
                    "--output",
                    "json",
                ]
            )
            dismissed = _run_json_cli(
                [
                    "evolution",
                    "dismiss",
                    "--config",
                    str(config_path),
                    "--user-id",
                    "alpha",
                    "--schedule-id",
                    schedule.schedule_id,
                    "--reason",
                    "manual review",
                    "--output",
                    "json",
                ]
            )

            self.assertEqual(schedule.schedule_id, listed["schedules"][0]["schedule_id"])
            self.assertEqual([], isolated["schedules"])
            self.assertEqual(schedule.schedule_id, shown["schedule_id"])
            self.assertEqual("dismissed", dismissed["decision"])


def _run_json_cli(arguments: list[str]) -> dict[str, object]:
    output = StringIO()
    with redirect_stdout(output):
        result = main(arguments)
    if result != 0:
        raise AssertionError(f"CLI returned {result}: {arguments}")
    value = json.loads(output.getvalue())
    if not isinstance(value, dict):
        raise AssertionError("CLI did not return a JSON object")
    return value


def _write_config(root: Path) -> Path:
    config_path = root / "agent.toml"
    config_path.write_text(
        """
[agent]
name = "evolution-cli"
system = "Test system."
workflow = "direct"
memory = "default"
skills = []

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return config_path

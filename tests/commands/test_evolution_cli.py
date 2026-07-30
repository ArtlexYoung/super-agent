from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from super_agent import Agent
from cli import main
from core.config import AgentConfig
from skill.evolution.records import (
    EvaluationResult,
    EvaluationSource,
    EvaluationTokenUsage,
    append_evaluation_records,
    create_evaluation_record,
)
from skill.evolution.recommendations import recommend_skill_revisions
from skill.evolution.values import SkillRevision
from support import load_default_evolution_policy


class EvolutionCliTests(unittest.TestCase):
    def test_cli_lists_and_shows_one_users_automatic_evolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_config(Path(tmp))
            agent = Agent(AgentConfig.load_from_file(config_path), use_storage=True)
            revision = SkillRevision(
                key="prompt:writer",
                skill_type="prompt",
                name="writer",
                version="0.1.0",
                content_sha256="a" * 64,
                function_group="writing",
                agent_created=True,
                agent_can_update=True,
                evolution_supported=True,
                freshness=40,
            )
            store = agent.runtime.create_event_store("alpha")
            append_evaluation_records(
                store,
                [
                    create_evaluation_record(
                        revision,
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
            evolution = recommend_skill_revisions(
                store,
                [revision],
                load_default_evolution_policy(Path(tmp)),
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
                    "--evolution-id",
                    evolution.evolution_id,
                    "--output",
                    "json",
                ]
            )
            self.assertEqual(3, listed["schema_version"])
            self.assertEqual(
                evolution.evolution_id,
                listed["evolutions"][0]["evolution_id"],
            )
            self.assertEqual([], isolated["evolutions"])
            self.assertEqual(evolution.evolution_id, shown["evolution_id"])
            self.assertEqual("candidate_recommended", shown["status"])


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
skills = ["workflow:direct", "memory:default"]

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
""".strip(),
        encoding="utf-8",
    )
    return config_path

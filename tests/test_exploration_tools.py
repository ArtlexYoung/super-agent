"""Native Skill activation, central references and existing Runtime integration."""

import json
from pathlib import Path
import tempfile
import unittest

from examples.exploration_research import run_example
from super_agent import Agent
from super_agent.adapter.tools import CodeWorkspace, WorkspaceSettings
from super_agent.core.model import ModelEvent
from super_agent.core.provider import MockModel
from super_agent.skill.exploration import ExplorationGraph
from super_agent.skill.exploration_tools import EXPLORATION_TOOLS, ExplorationTools

BUILTIN = Path(__file__).resolve().parents[1] / "src/super_agent/skill/builtin"
SKILL = "skill:super-agent/research/exploration"
CREATE = {"action": "create", "expected_version": 0, "request_id": "create",
          "data": {"origin": "Question", "goals": [{"id": "answer", "statement": "Answer", "acceptance": "Host oracle"}]}}


def call(identifier: str, name: str, arguments: dict[str, object]) -> tuple[ModelEvent, ...]:
    return (ModelEvent.call(identifier, name, arguments), ModelEvent.done())


class ExplorationIntegrationTests(unittest.TestCase):
    def agent(self, responses: list[object]) -> Agent:
        agent = Agent(MockModel(responses=responses))
        self.addCleanup(agent.close)
        agent.add_library_path(BUILTIN)
        return agent

    def test_tools_appear_only_after_activation_and_run_through_existing_runtime(self) -> None:
        agent = self.agent([
            call("activate", "activate_skill", {"skill": SKILL}),
            call("create", "exploration_update", CREATE),
            call("read", "exploration_read", {"view": "overview"}),
            "Report remaining verification.",
        ])
        result = agent.run("Explore the question")
        self.assertFalse(EXPLORATION_TOOLS & {tool.name for tool in agent.model.requests[0].tools})
        self.assertTrue(EXPLORATION_TOOLS <= {tool.name for tool in agent.model.requests[1].tools})
        self.assertIn(SKILL, result.skills)
        self.assertEqual(1, sum(e.event_type == "exploration.changed" for e in result.events))
        self.assertFalse([e for e in result.events if e.event_type == "tool.failed"])

    def test_create_retries_do_not_duplicate_or_raise_host_budget(self) -> None:
        too_large = {**CREATE, "data": {**CREATE["data"], "max_steps": 100}}
        agent = self.agent([
            call("denied", "exploration_update", too_large),
            call("first", "exploration_update", CREATE),
            call("repeat", "exploration_update", CREATE),
            "Record only once.",
        ])
        result = agent.run("Explore", skill=SKILL)
        failures = [e for e in result.events if e.event_type == "tool.failed"]
        self.assertEqual(1, len(failures))
        self.assertIn("host limit", str(failures[0].data))
        self.assertEqual(1, sum(e.event_type == "exploration.changed" for e in result.events))

    def test_run_local_state_does_not_leak_into_next_run(self) -> None:
        agent = self.agent([call("create", "exploration_update", CREATE), "First."])
        first = agent.run("First task", skill=SKILL)
        agent.model = MockModel(responses=[call("read", "exploration_read", {}), "Second."])
        second = agent.run("Different task", skill=SKILL)
        self.assertNotEqual(first.run_id, second.run_id)
        payload = json.dumps(agent.model.requests[-1].to_openai(), ensure_ascii=False)
        self.assertIn("not_initialized", payload)

    def test_explicit_host_graph_is_used_and_completion_is_not_model_writable(self) -> None:
        graph = ExplorationGraph("host-study", "Question", CREATE["data"]["goals"], max_steps=1)
        agent = self.agent([
            call("bind", "exploration_update", {"action": "bind_goal", "data": {}, "expected_version": 0, "request_id": "bad"}),
            call("direction", "exploration_update", {
                "action": "add_direction", "data": {"id": "try", "statement": "Hypothesis", "goal_ids": ["answer"],
                                                         "parents": ["origin"], "rationale": "Why"},
                "expected_version": 0, "request_id": "direction"}),
            "Still needs host acceptance.",
        ])
        agent.add_tools_for_skills(ExplorationTools("host-study", graph=graph).tools())
        result = agent.run("Explore", skill=SKILL)
        self.assertEqual(1, graph.version)
        self.assertEqual("unverified", graph.read()["goals"][0]["status"])
        self.assertEqual(1, sum(e.event_type == "tool.failed" for e in result.events))

    def test_partial_host_graph_tool_registration_fails_explicitly(self) -> None:
        agent = self.agent(["Should not run."])
        agent.add_tools_for_skills(ExplorationTools("host-study").tools()[:1])
        with self.assertRaisesRegex(ValueError, "registered together"):
            agent.run("Explore", skill=SKILL)

    def test_domain_scenes_share_one_canonical_exploration_method(self) -> None:
        for skill in ("research", "research/algorithms", "research/quantitative", "code/deep-optimization"):
            with self.subTest(skill=skill), tempfile.TemporaryDirectory() as directory:
                agent = self.agent(["Prepared method."])
                if skill.startswith("code/"):
                    agent.add_tools_for_skills(CodeWorkspace(WorkspaceSettings(Path(directory))).tools())
                result = agent.run("Prepare bounded research", skill=f"skill:super-agent/{skill}")
                self.assertEqual(1, result.skills.count(SKILL))
                self.assertTrue(EXPLORATION_TOOLS <= {t.name for t in agent.model.requests[0].tools})
                self.assertFalse(agent._team_runtimes)
                if skill == "code/deep-optimization":
                    self.assertNotIn("skill:super-agent/team/multi-agent", result.skills)

    def test_reference_is_readable_through_central_skill_resource_tool(self) -> None:
        agent = self.agent([
            call("reference", "read_skill_resource", {"skill": SKILL, "path": "references/graph-protocol.md"}),
            "Read the exact protocol.",
        ])
        result = agent.run("Prepare exploration", skill=SKILL)
        failures = [e for e in result.events if e.event_type == "tool.failed"]
        self.assertFalse(failures, str(failures))
        payload = json.dumps(agent.model.requests[-1].to_openai())
        self.assertIn("graph-protocol.md", payload)
        self.assertIn("sha256", payload)

    def test_actual_offline_study_delivers_and_restores_with_negative_branch(self) -> None:
        report = run_example()
        self.assertEqual("completed", report["status"])
        self.assertEqual("completed", report["restored_status"])
        self.assertEqual(["accepted", "delivered"], report["task_stages"])
        self.assertEqual(3, report["spent_steps"])
        self.assertEqual(882, report["oracle"]["cases"])
        self.assertEqual([], report["oracle"]["failures"])
        self.assertNotEqual(report["counterexample"]["linear"], report["counterexample"]["binary"])
        self.assertEqual("negative", report["retained_negative"])
        self.assertIn("spawns", report["mermaid"])


if __name__ == "__main__":
    unittest.main()

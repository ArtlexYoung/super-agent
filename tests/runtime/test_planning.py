import json
import unittest

from capability.skill_contributions import PlanningPolicy
from runtime.planning import (
    create_direct_task_plan,
    decide_task_planning,
    read_task_plan,
)


class TaskPlanningContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = PlanningPolicy(
            name="default",
            instruction="Create a plan.",
            max_steps=3,
            minimum_prompt_characters=100,
            planning_terms=("step by step",),
        )

    def test_explicit_term_and_long_prompt_enable_planning(self) -> None:
        explicit = decide_task_planning(
            self.policy,
            "Solve this step by step",
            workflow_mode="direct",
            required_features=("text",),
        )
        long_prompt = decide_task_planning(
            self.policy,
            "x" * 100,
            workflow_mode="direct",
            required_features=("text",),
        )

        self.assertTrue(explicit.should_plan)
        self.assertTrue(long_prompt.should_plan)

    def test_direct_task_is_one_step_plan(self) -> None:
        plan = create_direct_task_plan("Answer this", "answer", ("text",))

        self.assertEqual("direct", plan.origin)
        self.assertEqual(1, len(plan.steps))
        self.assertEqual("Answer this", plan.steps[0].instruction)
        self.assertEqual(("text",), plan.steps[0].required_features)

    def test_plan_parser_rejects_unknown_subagent_and_too_many_steps(self) -> None:
        step = {
            "instruction": "Do work",
            "purpose": "analysis",
            "required_features": ["text"],
            "subagent": "missing",
        }
        with self.assertRaisesRegex(ValueError, "unknown subagent"):
            read_task_plan(
                json.dumps({"steps": [step]}),
                self.policy,
                {"known"},
            )
        step["subagent"] = None
        with self.assertRaisesRegex(ValueError, "maximum is 3"):
            read_task_plan(
                json.dumps({"steps": [step, step, step, step]}),
                self.policy,
                set(),
            )

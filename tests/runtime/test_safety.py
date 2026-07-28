import tempfile
import unittest
from pathlib import Path

from skill.runners.defaults import create_default_skill_runners
from skill.runners.loaded import SkillAction
from core.provider.chat import MockProvider
from core.config import AgentConfig
from core.identity import RunIdentity
from core.actions import (
    ActionConfirmationRequired,
    ActionEffect,
    ActionNotAllowedError,
    ActionRequest,
    ActionRunner,
    ActionRules,
)
from core.session import RuntimeSession
from core.state.store import create_local_runtime_store
from skill.kinds.model import create_direct_provider_profile


class RuntimeSafetyTests(unittest.TestCase):
    def test_audit_policy_records_action_without_changing_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = _create_session(
                Path(tmp),
                ActionRules.from_name("audit"),
            )

            result = session.execute_action(
                ActionRequest(
                    "action-1",
                    "tool:list_skills",
                    "skill:index",
                    (ActionEffect.READ,),
                    ("query",),
                ),
                lambda: {"ok": True},
            )

            events = session.store.read_run_events(session.run_id)
            checked = next(event for event in events if event.event_type == "action.checked")
            self.assertEqual({"ok": True}, result)
            self.assertEqual("allow", checked.data["decision"])
            self.assertFalse(checked.data["enforced"])
            self.assertEqual(["query"], checked.data["argument_names"])
            self.assertNotIn("arguments", checked.data)
            self.assertEqual("action.completed", events[-1].event_type)

    def test_action_contract_rejects_unknown_or_duplicate_effects(self) -> None:
        with self.assertRaises(ValueError):
            ActionRequest("one", "tool:test", "runtime", ("unknown",))  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "duplicates"):
            ActionRequest(
                "one",
                "tool:test",
                "runtime",
                (ActionEffect.READ, ActionEffect.READ),
            )
        with self.assertRaisesRegex(ValueError, "at least one effect"):
            SkillAction((), "skill:test")
        with self.assertRaisesRegex(ValueError, "resource cannot be empty"):
            SkillAction((ActionEffect.READ,), " ")

    def test_policy_can_emit_enforced_allow_decision(self) -> None:
        decision = ActionRules().check_action(
            ActionRequest(
                "one",
                "tool:test",
                "runtime",
                (ActionEffect.EXECUTE,),
            )
        )

        self.assertEqual("require_confirmation", decision.decision.value)
        self.assertTrue(decision.enforced)

    def test_standard_policy_allows_internal_memory_but_blocks_mcp(self) -> None:
        policy = ActionRules()

        memory = policy.check_action(
            ActionRequest.create(
                "tool:add_memory_item",
                "memory:active:agent",
                (ActionEffect.CREATE,),
            )
        )
        mcp = policy.check_action(
            ActionRequest.create(
                "tool:run_skill",
                "mcp:github",
                (ActionEffect.EXECUTE, ActionEffect.NETWORK),
            )
        )

        self.assertEqual("allow", memory.decision.value)
        self.assertEqual("require_confirmation", mcp.decision.value)

    def test_read_only_policy_denies_mutations_before_execution(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        called = False

        def mutate() -> None:
            nonlocal called
            called = True

        executor = ActionRunner(
            ActionRules.from_name("read_only"),
            lambda event_type, data: events.append((event_type, data)),
        )
        with self.assertRaises(ActionNotAllowedError):
            executor.execute_action(
                ActionRequest.create(
                    "user:memory",
                    "memory:active",
                    (ActionEffect.CREATE,),
                ),
                mutate,
            )

        self.assertFalse(called)
        self.assertEqual(["action.checked", "action.blocked"], [item[0] for item in events])

    def test_standard_policy_exposes_confirmation_request(self) -> None:
        executor = ActionRunner(ActionRules(), lambda *_: None)
        request = ActionRequest.create(
            "tool:run_skill",
            "mcp:remote",
            (ActionEffect.EXECUTE, ActionEffect.NETWORK),
        )

        with self.assertRaises(ActionConfirmationRequired) as raised:
            executor.execute_action(request, lambda: None)

        self.assertEqual(request, raised.exception.request)


def _create_session(
    root: Path,
    action_rules: ActionRules | None = None,
) -> RuntimeSession:
    config = AgentConfig.create_default(root)
    identity = RunIdentity.create("local", config.agent.name)
    store = create_local_runtime_store(root / "state", agent_name=config.agent.name)
    store.start_run(identity, "question")
    return RuntimeSession(
        config=config,
        model_profile=create_direct_provider_profile(),
        provider=MockProvider(),
        skill_runners=create_default_skill_runners(),
        identity=identity,
        store=store,
        action_rules=action_rules or ActionRules(),
    )

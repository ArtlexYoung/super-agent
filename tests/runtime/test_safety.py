import tempfile
import unittest
from pathlib import Path

from capability.defaults import create_default_capability_registry
from provider.chat import MockProvider
from runtime.config import AgentConfig
from runtime.identity import RunIdentity
from runtime.safety import ActionEffect, ActionRequest, SafetyPolicy
from runtime.session import RuntimeSession
from runtime.store import create_local_runtime_store
from skill.kinds.model import discover_environment_model_profiles


class RuntimeSafetyTests(unittest.TestCase):
    def test_audit_policy_records_action_without_changing_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = _create_session(Path(tmp))

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

    def test_policy_can_emit_enforced_allow_decision(self) -> None:
        decision = SafetyPolicy(audit_only=False).check_action(
            ActionRequest(
                "one",
                "tool:test",
                "runtime",
                (ActionEffect.EXECUTE,),
            )
        )

        self.assertEqual("allow", decision.decision.value)
        self.assertTrue(decision.enforced)


def _create_session(root: Path) -> RuntimeSession:
    config = AgentConfig.create_default(root)
    identity = RunIdentity.create("local", config.agent.name)
    store = create_local_runtime_store(root / "state", agent_name=config.agent.name)
    store.start_run(identity, "question")
    return RuntimeSession(
        config=config,
        model_profile=discover_environment_model_profiles({})[0],
        provider=MockProvider(),
        capability_registry=create_default_capability_registry(),
        identity=identity,
        store=store,
    )

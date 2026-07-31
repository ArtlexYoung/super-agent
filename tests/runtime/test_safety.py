import tempfile
import unittest
from pathlib import Path

from core.skill_use.defaults import create_default_skill_handlers
from core.skill_use.handlers import SkillAction, SkillCollection
from core.provider.chat import MockProvider
from core.config import AgentConfig
from core.runtime.run import Run
from core.models import RunIdentity
from core.checks import (
    ActionConfirmationRequired,
    ActionEffect,
    ActionNotAllowedError,
    ActionRequest,
    ActionRunner,
    ActionRules,
)
from core.state.event_log import RunEventLog
from core.state.events import EventStore
from adapter.storage import JsonlStorage
from skill.disclosure import ProgressiveDisclosureCore
from core.skill_use.models import create_direct_provider_profile


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
            self.assertEqual("action.applied", events[-1].event_type)
            self.assertNotIn(
                "action.prepared",
                [event.event_type for event in events],
            )

    def test_state_change_is_prepared_before_it_is_applied(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        changed = False
        runner = ActionRunner(
            ActionRules.from_name("autonomous"),
            lambda event_type, data: events.append((event_type, data)),
        )
        request = ActionRequest.create(
            "tool:update",
            "memory:long_term",
            (ActionEffect.UPDATE,),
            action_id="change-1",
        )

        prepared = runner.prepare_action(request)
        self.assertFalse(changed)

        def apply_change() -> str:
            nonlocal changed
            changed = True
            return "updated"

        result = runner.apply_action(prepared, apply_change)

        self.assertTrue(changed)
        self.assertEqual("updated", result)
        self.assertEqual(
            ["action.checked", "action.prepared", "action.applying", "action.applied"],
            [event_type for event_type, _ in events],
        )
        with self.assertRaisesRegex(ValueError, "already applied"):
            runner.apply_action(prepared, apply_change)

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
                "tool:remember_long_term",
                "memory:long_term:agent",
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

    def test_read_only_action_does_not_require_action_checker(self) -> None:
        session = _create_session_without_action_checker()

        result = session.execute_action(
            ActionRequest.create(
                "tool:list_skills",
                "skill:index",
                (ActionEffect.READ,),
            ),
            lambda: {"skills": []},
        )

        self.assertEqual({"skills": []}, result)

    def test_state_change_requires_action_checker(self) -> None:
        session = _create_session_without_action_checker()

        with self.assertRaisesRegex(
            RuntimeError,
            "action checker is required for effects: execute, network",
        ):
            session.execute_action(
                ActionRequest.create(
                    "tool:remote",
                    "mcp:remote",
                    (ActionEffect.EXECUTE, ActionEffect.NETWORK),
                ),
                lambda: None,
            )


def _create_session(
    root: Path,
    action_rules: ActionRules | None = None,
) -> Run:
    config = AgentConfig.create_default(root)
    identity = RunIdentity.create("local", config.agent.name)
    backend = JsonlStorage(root / "state")
    event_log = RunEventLog(identity, backend=backend)
    store = EventStore(
        backend,
        root / "state",
        "local",
        config.agent.name,
        run_event_log=event_log,
    )
    event_log.start_run("question")
    disclosure = ProgressiveDisclosureCore([])
    return Run(
        config=config,
        model_profile=create_direct_provider_profile(),
        provider=MockProvider(),
        skills=SkillCollection(disclosure, create_default_skill_handlers()),
        identity=identity,
        event_log=event_log,
        store=store,
        create_action_rules=lambda: action_rules or ActionRules(),
    )


def _create_session_without_action_checker() -> Run:
    config = AgentConfig.create_default(Path("."))
    identity = RunIdentity.create("local", config.agent.name)
    event_log = RunEventLog(identity)
    event_log.start_run("question")
    return Run(
        config=config,
        model_profile=create_direct_provider_profile(),
        provider=MockProvider(),
        skills=SkillCollection(
            ProgressiveDisclosureCore([]),
            create_default_skill_handlers(),
        ),
        identity=identity,
        event_log=event_log,
        store=None,
        create_action_rules=None,
    )

"""Stable report projection for the v0.0.61 unified Runtime proof."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from agents.agent import Agent
from capability.defaults import create_progressive_skill_disclosure
from runtime.config import AgentConfig
from runtime.storage import StorageEvent, StorageEventQuery
from runtime.store import RuntimeStore
from skill.kinds.memory import MiniMemory

from proof_v0_0_61.fixtures import (
    ALICE,
    ALICE_CONVERSATION_ID,
    ALICE_MEMORY,
    ALICE_RUN_ID,
    ALICE_SECRET,
    API_KEY_ENV,
    BOB,
    BOB_CONVERSATION_ID,
    BOB_MEMORY,
    BOB_RUN_ID,
    BOB_SECRET,
    CANDIDATE_INSTRUCTIONS,
    PARENT_INSTRUCTIONS,
    VERSION,
    ExternalCallProbe,
    ModelRequestRecord,
    ProofModelServer,
    proof_input_sha256,
)


@dataclass(frozen=True)
class ProofInputs:
    root: Path
    agent: Agent
    model_server: ProofModelServer
    probe: ExternalCallProbe
    secret_requests: list[tuple[str, str]]
    alice_memory_before: list[str]
    bob_result_text: str
    alice_error_type: str


def build_report(inputs: ProofInputs) -> dict[str, object]:
    proof = _build_proof_result(inputs)
    checks = _build_checks(proof)
    return {
        "schema_version": 1,
        "version": VERSION,
        "input_sha256": proof_input_sha256(),
        "checks": checks,
        **proof,
        "all_checks_passed": all(checks.values()),
    }


def _build_proof_result(inputs: ProofInputs) -> dict[str, object]:
    agent = inputs.agent
    alice_store = agent.runtime.create_store(ALICE)
    bob_store = agent.runtime.create_store(BOB)
    alice_events = _read_user_events(alice_store)
    bob_events = _read_user_events(bob_store)
    alice_lock = _require_runtime_lock(alice_store, ALICE_RUN_ID)
    bob_lock = _require_runtime_lock(bob_store, BOB_RUN_ID)
    alice_skill = _read_effective_skill(alice_store, agent.config)
    bob_skill = _read_effective_skill(bob_store, agent.config)
    alice_evolutions = agent.for_user(ALICE).skills.list_evolutions()
    bob_evolutions = agent.for_user(BOB).skills.list_evolutions()
    evolution = alice_evolutions[0] if alice_evolutions else None
    histories = {
        ALICE: alice_store.disclosure.read_history(),
        BOB: bob_store.disclosure.read_history(),
    }
    secrets_absent = _secrets_are_absent(
        inputs.root,
        alice_events + bob_events,
        [alice_lock, bob_lock],
    )
    return {
        "scheduling": _build_scheduling_result(
            inputs.model_server.requests,
            alice_lock,
            bob_lock,
        ),
        "isolation": _build_isolation_result(
            inputs,
            alice_store,
            bob_store,
            alice_events,
            bob_events,
        ),
        "disclosure": _build_disclosure_result(
            alice_store,
            bob_store,
            histories,
        ),
        "memory": _build_memory_result(inputs, alice_store, bob_store),
        "safety": _build_safety_result(inputs, alice_store),
        "evolution": _build_evolution_result(
            inputs,
            evolution,
            bob_evolutions,
            alice_skill,
            bob_skill,
        ),
        "runtime": {
            "alice_terminal_event": alice_store.read_run_events(ALICE_RUN_ID)[-1].event_type,
            "bob_terminal_event": bob_store.read_run_events(BOB_RUN_ID)[-1].event_type,
            "alice_error_type": inputs.alice_error_type,
            "bob_result": inputs.bob_result_text,
            "locks_present": True,
            "locks_report_models_ready": bool(
                alice_lock["model"]["ready"] and bob_lock["model"]["ready"]
            ),
            "secrets_absent_from_events_locks_and_files": secrets_absent,
        },
    }


def _build_scheduling_result(
    requests: list[ModelRequestRecord],
    alice_lock: dict[str, object],
    bob_lock: dict[str, object],
) -> dict[str, object]:
    return {
        ALICE: {
            "purpose": alice_lock["task_schedule"]["purpose"],
            "selected_profile": alice_lock["task_schedule"]["models"][0]["key"],
            "selected_model": alice_lock["model"]["model"],
            "model_requests": _user_model_requests(requests, ALICE),
        },
        BOB: {
            "purpose": bob_lock["task_schedule"]["purpose"],
            "selected_profile": bob_lock["task_schedule"]["models"][0]["key"],
            "selected_model": bob_lock["model"]["model"],
            "model_requests": _user_model_requests(requests, BOB),
        },
    }


def _user_model_requests(
    requests: list[ModelRequestRecord],
    user_id: str,
) -> list[dict[str, str]]:
    return [
        {"model": item.model, "route": item.route}
        for item in requests
        if item.user_id == user_id
    ]


def _build_isolation_result(
    inputs: ProofInputs,
    alice_store: RuntimeStore,
    bob_store: RuntimeStore,
    alice_events: list[StorageEvent],
    bob_events: list[StorageEvent],
) -> dict[str, object]:
    profiles = {
        user_id: {
            profile.key: profile.model
            for profile in inputs.agent.runtime.read_model_profiles(user_id)
        }
        for user_id in (ALICE, BOB)
    }
    secret_names = {
        user_id: sorted(
            {
                name
                for requested_user, name in inputs.secret_requests
                if requested_user == user_id
            }
        )
        for user_id in (ALICE, BOB)
    }
    conversations = {
        ALICE: _conversation_contents(alice_store, ALICE_CONVERSATION_ID),
        BOB: _conversation_contents(bob_store, BOB_CONVERSATION_ID),
    }
    return {
        "model_profiles": profiles,
        "secret_variable_requests": secret_names,
        "endpoint_users": [item.user_id for item in inputs.model_server.requests],
        "conversations": conversations,
        "private_roots_distinct": alice_store.private_root != bob_store.private_root,
        "event_user_ids": {
            ALICE: sorted({event.user_id for event in alice_events}),
            BOB: sorted({event.user_id for event in bob_events}),
        },
        "agent_default_model_unchanged": inputs.agent.model_profile.model == "mock",
    }


def _conversation_contents(
    store: RuntimeStore,
    conversation_id: str,
) -> list[str]:
    return [
        message.content
        for message in store.read_conversation(conversation_id).messages
    ]


def _build_disclosure_result(
    alice_store: RuntimeStore,
    bob_store: RuntimeStore,
    histories: dict[str, list[dict[str, object]]],
) -> dict[str, object]:
    return {
        "alice_cache_reused": any(item["cache_hit"] for item in histories[ALICE]),
        "bob_cache_reused": any(item["cache_hit"] for item in histories[BOB]),
        "alice_paths_scoped": _history_paths_are_scoped(
            histories[ALICE],
            alice_store.disclosure.cache_root,
        ),
        "bob_paths_scoped": _history_paths_are_scoped(
            histories[BOB],
            bob_store.disclosure.cache_root,
        ),
        "cache_roots_distinct": (
            alice_store.disclosure.cache_root != bob_store.disclosure.cache_root
        ),
        "alice_history_run_scope": sorted(
            {str(item["run_id"]) for item in histories[ALICE]}
        ),
        "bob_history_run_scope": sorted(
            {str(item["run_id"]) for item in histories[BOB]}
        ),
    }


def _build_memory_result(
    inputs: ProofInputs,
    alice_store: RuntimeStore,
    bob_store: RuntimeStore,
) -> dict[str, object]:
    memory_events = alice_store.backend.read_events(
        StorageEventQuery(
            user_id=ALICE,
            agent_name=alice_store.agent_name,
            stream_type="memory",
        )
    )
    return {
        "alice_before": inputs.alice_memory_before,
        "alice_after": _memory_texts(alice_store),
        "bob_after": _memory_texts(bob_store),
        "alice_organization_events": [
            event.event_type
            for event in memory_events
            if event.event_type.startswith("memory.organization")
            or event.event_type == "memory.forgotten"
        ],
    }


def _memory_texts(store: RuntimeStore) -> list[str]:
    return sorted(item.text for item in MiniMemory(store).list_memory_items())


def _build_safety_result(
    inputs: ProofInputs,
    alice_store: RuntimeStore,
) -> dict[str, object]:
    action_events = [
        event
        for event in alice_store.read_run_events(ALICE_RUN_ID)
        if event.data.get("action_id") == "alice-external-call"
    ]
    blocked = next(
        (event for event in action_events if event.event_type == "action.blocked"),
        None,
    )
    return {
        "handler_calls": inputs.probe.calls,
        "action_events": [event.event_type for event in action_events],
        "decision": "" if blocked is None else blocked.data["decision"],
        "effects": [] if blocked is None else blocked.data["effects"],
        "resource": "" if blocked is None else blocked.data["resource"],
    }


def _build_evolution_result(
    inputs: ProofInputs,
    evolution: object,
    bob_evolutions: list[object],
    alice_skill: dict[str, object],
    bob_skill: dict[str, object],
) -> dict[str, object]:
    state = evolution
    candidate = None if state is None else state.candidate_revision
    event_types = (
        []
        if state is None
        else [
            event.event_type
            for event in inputs.agent.runtime.create_store(
                ALICE
            ).read_skill_evolution_events(state.evolution_id)
        ]
    )
    return {
        "alice_status": "" if state is None else state.status,
        "alice_origin": "" if state is None else state.origin,
        "alice_reason_codes": [] if state is None else list(state.reason_codes),
        "source_version": "" if state is None else state.source_revision.version,
        "candidate_version": "" if candidate is None else candidate.version,
        "event_types": event_types,
        "alice_effective_source": alice_skill["source"],
        "alice_effective_version": alice_skill["version"],
        "alice_instructions_improved": (
            alice_skill["instructions"] == CANDIDATE_INSTRUCTIONS.strip()
        ),
        "bob_evolution_count": len(bob_evolutions),
        "bob_effective_source": bob_skill["source"],
        "bob_effective_version": bob_skill["version"],
        "bob_instructions_unchanged": (
            bob_skill["instructions"] == PARENT_INSTRUCTIONS.strip()
        ),
        "project_skill_unchanged": inputs.root.joinpath(
            "skills/external/protected/SKILL.md"
        ).read_text(encoding="utf-8")
        == PARENT_INSTRUCTIONS,
    }


def _build_checks(proof: dict[str, object]) -> dict[str, bool]:
    return {
        "models_routes_and_credentials_are_user_scoped": _check_user_isolation(proof),
        "runtime_schedules_models_from_declared_traits": _check_scheduling(proof),
        "progressive_disclosure_cache_is_user_scoped_and_reused": (
            _check_disclosure(proof)
        ),
        "memory_is_organized_forgotten_and_isolated": _check_memory(proof),
        "safety_blocks_external_side_effect_before_handler": _check_safety(proof),
        "failed_owned_skill_evolves_only_for_its_user": _check_evolution(proof),
        "canonical_events_and_locks_complete_without_secrets": _check_runtime(proof),
    }


def _check_user_isolation(proof: dict[str, object]) -> bool:
    scheduling = proof["scheduling"]
    isolation = proof["isolation"]
    actual = {
        "models": {
            ALICE: scheduling[ALICE]["selected_model"],
            BOB: scheduling[BOB]["selected_model"],
        },
        "secret_variables": isolation["secret_variable_requests"],
        "event_users": isolation["event_user_ids"],
    }
    expected = {
        "models": {
            ALICE: "alice-specialist-model",
            BOB: "bob-general-model",
        },
        "secret_variables": {ALICE: [API_KEY_ENV], BOB: [API_KEY_ENV]},
        "event_users": {ALICE: [ALICE], BOB: [BOB]},
    }
    return actual == expected and bool(isolation["agent_default_model_unchanged"])


def _check_scheduling(proof: dict[str, object]) -> bool:
    scheduling = proof["scheduling"]
    actual = {
        user_id: {
            "purpose": scheduling[user_id]["purpose"],
            "profile": scheduling[user_id]["selected_profile"],
        }
        for user_id in (ALICE, BOB)
    }
    return actual == {
        ALICE: {"purpose": "external_operation", "profile": "model:specialist"},
        BOB: {"purpose": "answer", "profile": "model:general"},
    }


def _check_disclosure(proof: dict[str, object]) -> bool:
    disclosure = proof["disclosure"]
    names = (
        "alice_cache_reused",
        "bob_cache_reused",
        "alice_paths_scoped",
        "bob_paths_scoped",
        "cache_roots_distinct",
    )
    return all(disclosure[name] for name in names)


def _check_memory(proof: dict[str, object]) -> bool:
    memory = proof["memory"]
    return memory == {
        "alice_before": sorted(ALICE_MEMORY),
        "alice_after": [ALICE_MEMORY[0]],
        "bob_after": [BOB_MEMORY],
        "alice_organization_events": [
            "memory.organization.started",
            "memory.forgotten",
            "memory.organization.completed",
        ],
    }


def _check_safety(proof: dict[str, object]) -> bool:
    safety = proof["safety"]
    expected = {
        "handler_calls": 0,
        "action_events": ["action.checked", "action.blocked"],
        "decision": "require_confirmation",
        "effects": ["execute", "network"],
        "resource": "external:proof-service",
    }
    return safety == expected


def _check_evolution(proof: dict[str, object]) -> bool:
    evolution = proof["evolution"]
    expected = {
        "alice_status": "promoted",
        "alice_origin": "automatic",
        "alice_reason_codes": ["failures"],
        "source_version": "0.1.0",
        "candidate_version": "0.1.1",
        "alice_effective_source": "user",
        "alice_effective_version": "0.1.1",
        "bob_evolution_count": 0,
        "bob_effective_source": "project",
        "bob_effective_version": "0.1.0",
    }
    actual = {name: evolution[name] for name in expected}
    booleans = (
        "alice_instructions_improved",
        "bob_instructions_unchanged",
        "project_skill_unchanged",
    )
    return actual == expected and all(evolution[name] for name in booleans)


def _check_runtime(proof: dict[str, object]) -> bool:
    runtime = proof["runtime"]
    expected = {
        "alice_terminal_event": "run.failed",
        "bob_terminal_event": "run.completed",
        "alice_error_type": "ActionConfirmationRequired",
        "bob_result": "Bob isolated answer.",
    }
    actual = {name: runtime[name] for name in expected}
    booleans = (
        "locks_present",
        "locks_report_models_ready",
        "secrets_absent_from_events_locks_and_files",
    )
    return actual == expected and all(runtime[name] for name in booleans)


def _read_effective_skill(
    store: RuntimeStore,
    config: AgentConfig,
) -> dict[str, object]:
    disclosure = create_progressive_skill_disclosure(config, store=store)
    entry = disclosure.prepare_skill_index().require_skill(
        "protected",
        "external",
    )
    opened = disclosure.open_skill("protected", "external")
    return {
        "source": entry.source,
        "version": entry.version,
        "instructions": opened.read_instructions().content,
    }


def _read_user_events(store: RuntimeStore) -> list[StorageEvent]:
    return store.backend.read_events(
        StorageEventQuery(user_id=store.user_id, agent_name=store.agent_name)
    )


def _require_runtime_lock(
    store: RuntimeStore,
    run_id: str,
) -> dict[str, object]:
    runtime_lock = store.read_runtime_lock(run_id)
    if runtime_lock is None:
        raise AssertionError(f"runtime lock not found: {run_id}")
    return runtime_lock


def _history_paths_are_scoped(
    history: list[dict[str, object]],
    cache_root: Path,
) -> bool:
    root = cache_root.resolve()
    return bool(history) and all(
        Path(str(item["cache_path"])).resolve().is_relative_to(root)
        for item in history
    )


def _secrets_are_absent(
    root: Path,
    events: list[StorageEvent],
    runtime_locks: list[dict[str, object]],
) -> bool:
    serialized = json.dumps(
        {
            "events": [asdict(event) for event in events],
            "runtime_locks": runtime_locks,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    persisted = b"".join(
        path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ).decode("utf-8", errors="ignore")
    return all(
        secret not in serialized and secret not in persisted
        for secret in (ALICE_SECRET, BOB_SECRET)
    )

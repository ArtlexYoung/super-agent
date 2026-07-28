"""Generate the deterministic v0.0.53 Runtime boundary proof."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import tempfile
import threading
import tomllib
from dataclasses import dataclass
from pathlib import Path

from core.agent import Agent
from adapter.ag_ui_adapter.server import AGUIHTTPServer, create_ag_ui_server
from skill.runners.loaded import (
    SkillAction,
    SkillTool,
    LoadedSkill,
)
from skill.runners.registry import SkillLoadRequest
from core.provider.chat import Message, ModelResponse, ToolCall, ToolDefinition
from core.config import AgentConfig
from core.actions import ActionEffect
from core.storage import StorageEventQuery
from skill.kinds.memory import MiniMemory
from skill.manifest import Skill


VERSION = "0.0.53"
USER_ID = "proof-user"
THREAD_ID = "proof-thread"
RUN_ID = "proof-run"
PROMPT = "Use the current project external service."
PARENT_INSTRUCTIONS = "Call the external operation when the task requests it.\n"
CANDIDATE_INSTRUCTIONS = (
    "Request explicit approval before calling the external operation.\n"
)


@dataclass
class ExternalCallProbe:
    calls: int = 0

    def run_external(self, arguments: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        return {"executed": True}


class ExternalSkillRunner:
    """Expose one risky Tool only through an explicitly registered SkillRunner."""

    name = "external-operation"
    version = "1"
    skill_type = "external"
    adds_model_context = True

    def __init__(self, probe: ExternalCallProbe) -> None:
        self.probe = probe

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        opened = request.disclosure.open_skill(
            request.reference.name,
            self.skill_type,
        )
        return LoadedSkill(
            model_context=Skill(
                opened.read_manifest(),
                opened.read_instructions().content,
            ),
            tools=(
                SkillTool(
                    "run_external",
                    "Run the external operation declared by this Skill.",
                    {},
                    self.probe.run_external,
                    action=SkillAction(
                        (ActionEffect.EXECUTE, ActionEffect.NETWORK),
                        "external:proof-service",
                    ),
                ),
            ),
        )

class ProofProvider:
    """Route deterministic responses by the Runtime-owned model purpose prompt."""

    def __init__(self) -> None:
        self.routes: list[str] = []
        self.forgotten_memory_id = ""

    def send_chat_messages(self, messages: list[Message], model: str) -> str:
        system = str(messages[0].get("content", ""))
        if "Return only JSON with an operations array" in system:
            self.routes.append("memory_organization")
            payload = json.loads(str(messages[-1]["content"]))
            stale = next(
                item
                for item in payload["candidates"]
                if "legacy" in str(item["text"]).lower()
            )
            self.forgotten_memory_id = str(stale["item_id"])
            return json.dumps(
                {
                    "operations": [
                        {
                            "type": "forget",
                            "source_item_ids": [self.forgotten_memory_id],
                            "reason": "superseded project endpoint",
                        }
                    ]
                }
            )
        if "Create or improve one complete Agent Skill directory" in system:
            self.routes.append("skill_evolution")
            return json.dumps(
                {
                    "write_files": {"SKILL.md": CANDIDATE_INSTRUCTIONS},
                    "delete_files": [],
                }
            )
        self.routes.append("skill_evaluation")
        return "Candidate handles the external operation safely."

    def send_chat_messages_with_tools(
        self,
        messages: list[Message],
        model: str,
        tools: list[ToolDefinition],
    ) -> ModelResponse:
        names = {
            str(tool.get("function", {}).get("name", ""))
            for tool in tools
        }
        if "run_external" not in names:
            raise AssertionError("external Skill tool was not loaded")
        self.routes.append("task")
        return ModelResponse(
            text="",
            tool_calls=[ToolCall("external-call", "run_external", {})],
            stop_reason="tool_calls",
        )


@dataclass(frozen=True)
class _ProofSetup:
    root: Path
    agent: Agent
    provider: ProofProvider
    probe: ExternalCallProbe
    memory: MiniMemory
    memory_before: list[str]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="docs/experiments/v0.0.53.json",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary_directory:
        proof = run_end_to_end_proof(Path(temporary_directory))
    checks = {
        "recall_organizes_and_forgets_memory": (
            proof["memory"]["before"]
            == [
                "Current project external service endpoint.",
                "Legacy project external service endpoint.",
            ]
            and proof["memory"]["after"]
            == ["Current project external service endpoint."]
            and proof["memory"]["organization_events"]
            == [
                "memory.organization.started",
                "memory.forgotten",
                "memory.organization.completed",
            ]
        ),
        "safety_blocks_handler_before_execution": (
            proof["safety"]["handler_calls"] == 0
            and proof["safety"]["action_events"]
            == ["action.checked", "action.blocked"]
            and proof["safety"]["effects"] == ["execute", "network"]
        ),
        "ag_ui_streams_canonical_error": (
            proof["ag_ui"]["status"] == 200
            and proof["ag_ui"]["content_type"]
            == "text/event-stream; charset=utf-8"
            and proof["ag_ui"]["terminal_event"]
            == {
                "type": "RUN_ERROR",
                "code": "ActionConfirmationRequired",
            }
            and set(proof["ag_ui"]["custom_events"])
            >= {
                "run.started",
                "tool.requested",
                "action.blocked",
                "tool.failed",
                "run.failed",
            }
        ),
        "failed_owned_skill_is_promoted": (
            proof["evolution"]["skill_key"] == "external:protected"
            and proof["evolution"]["origin"] == "automatic"
            and proof["evolution"]["status"] == "promoted"
            and proof["evolution"]["reason_codes"] == ["failures"]
            and proof["evolution"]["source_version"] == "0.1.0"
            and proof["evolution"]["candidate_version"] == "0.1.1"
            and proof["evolution"]["active_version"] == "0.1.1"
            and proof["evolution"]["instructions_improved"]
        ),
        "one_provider_serves_each_runtime_purpose": proof["provider_routes"]
        == [
            "memory_organization",
            "task",
            "skill_evolution",
            "skill_evaluation",
        ],
    }
    report = {
        "schema_version": 1,
        "version": VERSION,
        "input_sha256": proof_input_sha256(),
        "checks": checks,
        **proof,
        "all_checks_passed": all(checks.values()),
    }
    if not report["all_checks_passed"]:
        raise AssertionError("v0.0.53 proof checks did not all pass")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote v{VERSION} proof: {output}")
    return 0


def run_end_to_end_proof(root: Path) -> dict[str, object]:
    write_project(root)
    config = AgentConfig.load_from_file(root / "agent.toml")
    provider = ProofProvider()
    probe = ExternalCallProbe()
    agent = Agent(
        config,
        provider=provider,
        skill_runners=[ExternalSkillRunner(probe)],
    )
    store = agent.runtime.create_store(USER_ID)
    memory = MiniMemory(store)
    memory.add_long_term_memory("Legacy project external service endpoint.")
    memory.add_long_term_memory("Current project external service endpoint.")
    memory_before = sorted(item.text for item in memory.list_memory_items())

    response = run_ag_ui_request(agent)
    return build_proof_result(
        _ProofSetup(root, agent, provider, probe, memory, memory_before),
        response,
    )


def run_ag_ui_request(
    agent: Agent,
) -> tuple[int, dict[str, str], bytes]:
    server = create_ag_ui_server(
        agent,
        port=0,
        user_id=USER_ID,
        static_root=None,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        return post_ag_ui_run(server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def build_proof_result(
    setup: _ProofSetup,
    response: tuple[int, dict[str, str], bytes],
) -> dict[str, object]:
    status, headers, body = response
    config = setup.agent.config
    store = setup.agent.runtime.create_store(USER_ID)

    sse_events = read_sse_events(body)
    run_events = store.read_run_events(RUN_ID)
    memory_events = setup.agent.storage.read_events(
        StorageEventQuery(
            user_id=USER_ID,
            agent_name=config.agent.name,
            stream_type="memory",
            stream_id="memory",
        )
    )
    memory_after = sorted(item.text for item in setup.memory.list_memory_items())
    external_action_events = [
        event
        for event in run_events
        if event.data.get("action_id") == "external-call"
    ]
    blocked = next(
        event for event in external_action_events if event.event_type == "action.blocked"
    )
    evolution = setup.agent.for_user("local").skills.list_evolutions(USER_ID)[0]
    active_manifest = tomllib.loads(
        setup.root.joinpath("skills/external/protected/skill.toml").read_text(
            encoding="utf-8"
        )
    )
    candidate = evolution.candidate_revision
    return {
        "provider_routes": setup.provider.routes,
        "memory": {
            "before": setup.memory_before,
            "after": memory_after,
            "forgotten_item_selected": bool(setup.provider.forgotten_memory_id),
            "organization_events": [
                event.event_type
                for event in memory_events
                if event.event_type.startswith("memory.organization")
                or event.event_type == "memory.forgotten"
            ],
        },
        "safety": {
            "handler_calls": setup.probe.calls,
            "resource": blocked.data["resource"],
            "effects": blocked.data["effects"],
            "decision": blocked.data["decision"],
            "action_events": [event.event_type for event in external_action_events],
        },
        "ag_ui": {
            "status": status,
            "content_type": headers.get("Content-Type", ""),
            "event_types": [event["type"] for event in sse_events],
            "custom_events": [
                event["name"] for event in sse_events if event["type"] == "CUSTOM"
            ],
            "terminal_event": {
                "type": sse_events[-1]["type"],
                "code": sse_events[-1].get("code", ""),
            },
        },
        "evolution": {
            "skill_key": evolution.skill_key,
            "origin": evolution.origin,
            "status": evolution.status,
            "reason_codes": list(evolution.reason_codes),
            "source_version": evolution.source_revision.version,
            "candidate_version": None if candidate is None else candidate.version,
            "active_version": active_manifest["version"],
            "instructions_improved": setup.root.joinpath(
                "skills/external/protected/SKILL.md"
            ).read_text(encoding="utf-8")
            == CANDIDATE_INSTRUCTIONS,
            "event_types": [
                event.event_type
                for event in store.read_skill_evolution_events(
                    evolution.evolution_id
                )
            ],
        },
    }


def post_ag_ui_run(
    server: AGUIHTTPServer,
) -> tuple[int, dict[str, str], bytes]:
    payload = {
        "threadId": THREAD_ID,
        "runId": RUN_ID,
        "state": {},
        "messages": [
            {"id": "proof-message", "role": "user", "content": PROMPT}
        ],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    body = json.dumps(payload).encode("utf-8")
    connection = http.client.HTTPConnection(*server.server_address, timeout=10)
    connection.request(
        "POST",
        "/ag-ui",
        body=body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        },
    )
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def read_sse_events(body: bytes) -> list[dict[str, object]]:
    return [
        json.loads(block.removeprefix("data: "))
        for block in body.decode("utf-8").strip().split("\n\n")
        if block.startswith("data: ")
    ]


def write_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    write_workflow_skill(root)
    write_memory_skill(root)
    write_model_skill(root)
    write_external_skill(root)
    root.joinpath("agent.toml").write_text(
        '''[agent]
name = "v0053-proof"
system = "Complete the requested task."
skills = ["workflow:react", "memory:default", "external:protected"]

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
'''.strip()
        + "\n",
        encoding="utf-8",
    )


def write_workflow_skill(root: Path) -> None:
    path = root / "skills/workflow/react"
    path.mkdir(parents=True)
    path.joinpath("skill.toml").write_text(
        '''schema_version = 3
name = "react"
type = "workflow"
description = "Tool-using proof workflow"
version = "0.1.0"
triggers = []

[configuration]
mode = "react"
max_steps = 2
'''.strip()
        + "\n",
        encoding="utf-8",
    )


def write_memory_skill(root: Path) -> None:
    path = root / "skills/memory/default"
    path.mkdir(parents=True)
    path.joinpath("skill.toml").write_text(
        '''schema_version = 3
name = "default"
type = "memory"
description = "Recall-time organizing proof memory"
version = "0.1.0"
triggers = []

[configuration]
default_scope = "agent"
recall_limit = 20
include_in_prompt = true
include_usage_habits = true
organize_on_recall = true
'''.strip()
        + "\n",
        encoding="utf-8",
    )


def write_model_skill(root: Path) -> None:
    path = root / "skills/model/proof"
    path.mkdir(parents=True)
    path.joinpath("skill.toml").write_text(
        '''schema_version = 3
name = "proof"
type = "model"
description = "Deterministic proof model"
version = "0.1.0"
triggers = []

[configuration]
provider = "mock"
model = "proof-model"
supports = ["text", "tools"]
purposes = ["answer", "memory_organization", "skill_evolution", "skill_evaluation"]
strengths = ["proof"]
default = true
'''.strip()
        + "\n",
        encoding="utf-8",
    )


def write_external_skill(root: Path) -> None:
    path = root / "skills/external/protected"
    path.mkdir(parents=True)
    path.joinpath("skill.toml").write_text(
        '''schema_version = 3
name = "protected"
type = "external"
description = "Agent-owned external operation"
version = "0.1.0"
triggers = []
agent_created = true
agent_can_update = true
function_group = "external-operation"

[entry]
instructions = "SKILL.md"
'''.strip()
        + "\n",
        encoding="utf-8",
    )
    path.joinpath("SKILL.md").write_text(
        PARENT_INSTRUCTIONS,
        encoding="utf-8",
    )


def proof_input_sha256() -> str:
    values = {
        "version": VERSION,
        "prompt": PROMPT,
        "parent_instructions": PARENT_INSTRUCTIONS,
        "candidate_instructions": CANDIDATE_INSTRUCTIONS,
        "memory": [
            "Legacy project external service endpoint.",
            "Current project external service endpoint.",
        ],
        "action_effects": ["execute", "network"],
        "safety": "standard",
    }
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())

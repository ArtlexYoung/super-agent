"""Deterministic model, Capability, and Skill fixtures for the v0.0.61 proof."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from capability.registry import SkillLoadRequest
from capability.skill_contributions import (
    CapabilityAction,
    CapabilityTool,
    SkillContribution,
)
from runtime.safety import ActionEffect
from skill.manifest import Skill


VERSION = "0.0.61"
ALICE = "alice"
BOB = "bob"
ALICE_RUN_ID = "alice-proof-run"
BOB_RUN_ID = "bob-proof-run"
ALICE_CONVERSATION_ID = "alice-proof-conversation"
BOB_CONVERSATION_ID = "bob-proof-conversation"
API_KEY_ENV = "PROOF_API_KEY"
ALICE_SECRET = "proof-alice-credential"
BOB_SECRET = "proof-bob-credential"
ALICE_PROMPT = "Use the current external operation for Alice."
BOB_PROMPT = "Answer Bob's isolated question."
ALICE_MEMORY = (
    "Current external operation endpoint.",
    "Legacy external operation endpoint.",
)
BOB_MEMORY = "Bob isolated answer context."
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


class ExternalCapability:
    """Expose one risky Tool through trusted code and passive Skill content."""

    name = "external-operation"
    version = "1"
    capability_name = "external"
    adds_model_context = True

    def __init__(self, probe: ExternalCallProbe) -> None:
        self.probe = probe

    def load_skill(self, request: SkillLoadRequest) -> SkillContribution:
        opened = request.disclosure.open_skill(
            request.reference.name,
            self.capability_name,
        )
        return SkillContribution(
            model_context=Skill(
                opened.read_manifest(),
                opened.read_instructions().content,
            ),
            tools=(
                CapabilityTool(
                    "run_external",
                    "Run the external operation declared by this Skill.",
                    {},
                    self.probe.run_external,
                    action=CapabilityAction(
                        (ActionEffect.EXECUTE, ActionEffect.NETWORK),
                        "external:proof-service",
                    ),
                ),
            ),
        )


@dataclass(frozen=True)
class ModelRequestRecord:
    user_id: str
    model: str
    route: str


class ProofModelServer(HTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), ProofModelRequestHandler)
        self.user_by_secret = {
            ALICE_SECRET: ALICE,
            BOB_SECRET: BOB,
        }
        self.requests: list[ModelRequestRecord] = []


class ProofModelRequestHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        try:
            payload = self._read_payload()
            user_id = self._read_user_id()
            model = str(payload.get("model", ""))
            if not model.startswith(f"{user_id}-"):
                raise PermissionError("model does not belong to authenticated user")
            content, tool_calls, route = create_model_response(user_id, payload)
            self.server.requests.append(ModelRequestRecord(user_id, model, route))
            self._write_json(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "content": content,
                                "tool_calls": tool_calls,
                            }
                        }
                    ]
                },
            )
        except Exception as error:
            self._write_json(
                403 if isinstance(error, PermissionError) else 400,
                {"error": type(error).__name__},
            )

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_payload(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("model request must be a JSON object")
        return value

    def _read_user_id(self) -> str:
        authorization = self.headers.get("Authorization", "")
        secret = authorization.removeprefix("Bearer ")
        user_id = self.server.user_by_secret.get(secret)
        if user_id is None:
            raise PermissionError("unknown proof credential")
        return user_id

    def _write_json(self, status: int, value: dict[str, object]) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_model_response(
    user_id: str,
    payload: dict[str, object],
) -> tuple[str, list[dict[str, object]], str]:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("model request messages are required")
    system = str(messages[0].get("content", ""))
    tool_names = _read_tool_names(payload.get("tools", []))
    if "Return only JSON with an operations array" in system:
        return _memory_organization_response(messages)
    if "Create or improve one complete Agent Skill directory" in system:
        return (
            json.dumps(
                {
                    "write_files": {"SKILL.md": CANDIDATE_INSTRUCTIONS},
                    "delete_files": [],
                }
            ),
            [],
            "skill_evolution",
        )
    if "Candidate Skill: external:protected" in system:
        return "Candidate requires explicit approval.", [], "skill_evaluation"
    if user_id == ALICE and "run_external" in tool_names:
        return "", [_external_tool_call()], "task"
    return f"{user_id.title()} isolated answer.", [], "task"


def _memory_organization_response(
    messages: list[object],
) -> tuple[str, list[dict[str, object]], str]:
    last_message = messages[-1]
    if not isinstance(last_message, dict):
        raise ValueError("memory request message must be an object")
    candidates = json.loads(str(last_message["content"]))["candidates"]
    stale = next(
        item for item in candidates if "legacy" in str(item["text"]).lower()
    )
    return (
        json.dumps(
            {
                "operations": [
                    {
                        "type": "forget",
                        "source_item_ids": [stale["item_id"]],
                        "reason": "superseded endpoint",
                    }
                ]
            }
        ),
        [],
        "memory_organization",
    )


def _external_tool_call() -> dict[str, object]:
    return {
        "id": "alice-external-call",
        "type": "function",
        "function": {
            "name": "run_external",
            "arguments": "{}",
        },
    }


def _read_tool_names(value: object) -> set[str]:
    if not isinstance(value, list):
        raise ValueError("model request tools must be an array")
    return {
        str(item.get("function", {}).get("name", ""))
        for item in value
        if isinstance(item, dict) and isinstance(item.get("function"), dict)
    }


def write_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _write_workflow_skill(root)
    _write_memory_skill(root)
    _write_external_skill(root)
    root.joinpath("agent.toml").write_text(
        '''[agent]
name = "v0061-proof"
system = "Complete the requested task."
workflow = "react"
memory = "default"
skills = ["external:protected"]
safety = "standard"

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
'''.strip()
        + "\n",
        encoding="utf-8",
    )


def _write_workflow_skill(root: Path) -> None:
    path = root / "skills/workflow/react"
    path.mkdir(parents=True)
    path.joinpath("skill.toml").write_text(
        '''schema_version = 2
name = "react"
capability = "workflow"
description = "Tool-using unified proof workflow"
version = "0.1.0"
triggers = []

[configuration]
mode = "react"
max_steps = 2
'''.strip()
        + "\n",
        encoding="utf-8",
    )


def _write_memory_skill(root: Path) -> None:
    path = root / "skills/memory/default"
    path.mkdir(parents=True)
    path.joinpath("skill.toml").write_text(
        '''schema_version = 2
name = "default"
capability = "memory"
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


def _write_external_skill(root: Path) -> None:
    path = root / "skills/external/protected"
    path.mkdir(parents=True)
    path.joinpath("skill.toml").write_text(
        '''schema_version = 2
name = "protected"
capability = "external"
description = "Agent-owned protected operation"
version = "0.1.0"
triggers = ["external operation"]
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
        "prompts": {ALICE: ALICE_PROMPT, BOB: BOB_PROMPT},
        "memory": {ALICE: list(ALICE_MEMORY), BOB: [BOB_MEMORY]},
        "skill": {
            "parent": PARENT_INSTRUCTIONS,
            "candidate": CANDIDATE_INSTRUCTIONS,
        },
        "models": {
            "general": ["answer"],
            "specialist": [
                "external_operation",
                "memory_organization",
                "skill_evolution",
                "skill_evaluation",
            ],
        },
        "safety": "standard",
    }
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

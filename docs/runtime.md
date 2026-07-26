# Runtime, Workflows, Tracing, and Multi-Agent

## One Runtime Session

Each `Agent.run(...)` creates one `RuntimeSession` with one `RunIdentity` and one `RuntimeStore`. The Runtime prepares the Skill index once, records a lock from that same index, executes the selected workflow, records every used target, and appends the final evaluation.

## Workflow Skills

A workflow is an ordinary Skill:

```toml
schema_version = 2
name = "react"
capability = "workflow"
description = "Tool-using workflow"
version = "0.1.0"
triggers = []

[configuration]
mode = "react"
max_steps = 8
instruction = "Finish when the task is complete."
```

Built-in modes:

- `direct`: one model request.
- `plan`: one request with a compact planning instruction.
- `react`: the model chooses runtime tools until it finishes.
- `loop`: like react, with an explicit maximum step count.

The workflow defines execution termination. Agent nesting itself is not forcibly stopped.

## Runtime Tools

React and loop workflows may expose:

- Skill index and disclosure tools.
- MCP Skill discovery and calls.
- Memory list, add, recall, forget, and consolidate tools.
- Subagent list and run tools.

Every tool request, completion, and failure is written to the run event stream.

## Run Tracing

Each run receives a unique `run_id`. With the default JSONL backend, all runtime state for one user is appended to one canonical stream:

```text
.super-agent/users/<user-hash>/events.jsonl
```

Events are ordered and append-only. `RuntimeStore.read_run(...)` replays run events into a `RunSnapshot`; no parallel snapshot file can drift from the trace. The `runtime.locked` event fixes:

- Effective Agent configuration.
- Resolved model and Provider adapter.
- Capability names and versions.
- Skill versions, dependencies, and directory hashes.

API-key values are not stored.

```bash
super-agent runs status --config agent.toml
super-agent runs explain --config agent.toml --run-id <run-id>
super-agent runs export --config agent.toml --run-id <run-id> --output run.json
```

Explain and export rebuild the run view and verify the runtime-lock hash before returning its content.

## Streaming Protocol

Desktop apps and other processes can send a JSON request and receive JSONL events:

```bash
printf '%s' '{"prompt":"hello","messages":[]}' \
  | super-agent run --config agent.toml --request-stdin --output jsonl
```

Each output line has a `type` of `event` or `result`.

## Multi-Agent Composition

Each Agent is created independently and may use a different model, Skill tree, memory policy, workflow, and Capability set.

```python
from super_agent import Agent

main = Agent.load_from_config_file("agents/main.toml")
coder = Agent.load_from_config_file("agents/coder.toml")
reviewer = Agent.load_from_config_file("agents/reviewer.toml")

main.add_subagent(
    coder,
    name="coder",
    description="Implements code and tests",
    triggers=["code", "implement"],
)
main.add_subagent(
    reviewer,
    description="Reviews behavior and risks",
    triggers=["review"],
)
```

Omitting `name` generates `subagent01`, `subagent02`, and so on.

Direct and plan workflows run matching subagents before the main model request. React and loop workflows let the model call `list_subagents` and `run_subagent`.

## Nested Agent Warnings

`Agent.check_subagent_links()` reports:

- A complete cycle path such as `main -> coder -> reviewer -> main`.
- A complete path that exceeds configured `max_agent_chain_depth`.

Warnings do not block execution. Omitting the maximum allows unlimited nesting.

## Runtime-Owned State

```text
.super-agent/
  users/<user-hash>/events.jsonl
  users/<user-hash>/agents/<agent-hash>/cache/
  users/<user-hash>/agents/<agent-hash>/evolution/
```

`StorageEvent` is the backend-neutral source of truth for run traces, evaluations, memory, usage habits, and disclosure history. `RuntimeStore` supplies their explicit domain operations. Cache and evolution directories are scoped local artifacts; they are never alternate stores for the same event data.

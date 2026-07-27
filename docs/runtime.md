# Runtime, Workflows, Tracing, and Multi-Agent

## One Runtime Session

Each `Agent.run(...)` creates one internal `TaskRequest` and one `RuntimeSession` with a `RunIdentity` and `RuntimeStore`. `AgentRuntime.run_task(...)` prepares the Skill index once, then one `AdaptiveTaskLoop` selects and executes models, Skills, tools, and subagents. Runtime records a lock before model calls and appends the final evaluation. `Agent.read_task_trace(...)` returns the ordered events emitted by the executed task steps.

## Runtime Conversations

Conversations are event-backed Runtime views, not client-owned message arrays. Create a conversation and reuse its ID:

```python
conversation = agent.create_conversation(user_id="alice")
agent.run("first turn", user_id="alice", conversation_id=conversation.conversation_id)
agent.run("second turn", user_id="alice", conversation_id=conversation.conversation_id)
```

Runtime loads prior messages, appends the current user message, and stores the assistant result with its `run_id` and nested subagent results. `Agent` exposes explicit create, list, read, rename, clear, and delete methods. A `conversation_id` cannot be combined with an explicit `messages` list because that would create two competing history sources.

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

The workflow is passive Skill data. Runtime interprets its instruction and termination settings; Agent nesting itself is not forcibly stopped.

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
- Primary model profile, ordered fallback candidates, selection reasons, Skill hashes, and Provider adapter.
- Capability names and versions.
- Skill versions, dependencies, and directory hashes.

API-key values are not stored.

```bash
super-agent runs status --config agent.toml
super-agent runs explain --config agent.toml --run-id <run-id>
super-agent runs export --config agent.toml --run-id <run-id> --output run.json
```

Explain and export rebuild the run view and verify the runtime-lock hash before returning its content. `runs explain` additionally projects scheduling reasons, ordered model calls, latency, estimated token and cost metrics, learned routing evidence, relevant Skill freshness, and related automatic Skill evolutions. Its `--output json` schema is the same source used by the macOS task tree. A child Agent run can be located by `run_id` across Agent scopes only within the requested user and selected backend.

## Evidence-Learned Model Routing

Model routing is deterministic when no evidence exists. Each real model attempt then records its profile, effective purpose, success or failure, latency, estimated input and output tokens, and estimated cost. Runtime projects those canonical run events into quality and reliability statistics scoped by user, Agent, model Skill, and task purpose.

After evidence exists, the scheduler combines declared model traits with a bounded exploration bonus. A failed primary model is credited only with its failure; a successful fallback receives its own completion evidence.

```python
result = agent.run("Summarize this", user_id="alice")
agent.record_task_feedback(result.run_id, 0.8, "Useful summary", user_id="alice")
stats = agent.list_model_routing_stats(user_id="alice", purpose="summary")
```

Scores are between `0` and `1`. Runtime also detects a small deterministic set of correction and exact-retry signals in stored conversation follow-ups. Explicit feedback takes precedence over implicit feedback and all evidence projection stays local; it does not call a model.

## Streaming Protocol

Desktop apps and other processes can send a JSON request and receive JSONL events:

```bash
printf '%s' '{"prompt":"hello","user_id":"alice","conversation_id":"project-a"}' \
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
  events.sqlite3                  # SQLite backend only
  users/<user-hash>/events.jsonl  # JSONL backend only
  users/<user-hash>/agents/<agent-hash>/cache/
  users/<user-hash>/agents/<agent-hash>/evolution/
```

`StorageEvent` is the backend-neutral source of truth for conversations, run traces, evaluations, evolution recommendations, memory, usage habits, and disclosure history. MySQL and PostgreSQL store that event stream remotely while keeping cache, evolution, and installed Capability directories under the configured local `path`. `RuntimeStore` supplies explicit domain operations, and every operation is scoped by `user_id` and Agent name. Local artifacts are never alternate stores for the same event data.

## Runtime Proof History

The reproducible `v0.0.41` proof under `docs/experiments/` runs two model Skills through the normal scheduler, verifies user and Agent routing isolation, and drives automatic promotion plus regression rollback from real task evidence. Its generator is a standalone proof script, not an installed Runtime command.

The archived `v0.0.34` experiment covers progressive context and storage semantics. Its old benchmark orchestration was removed from the shipped Runtime after the result was recorded, so proof code does not become a second public execution framework.

Storage contract tests still apply the same domain operations to every backend. Remote checks require dedicated test database environments and never fall back to an Agent's production connection URL.

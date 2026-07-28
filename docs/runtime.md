# Runtime, Tasks, and Multi-Agent

## One Task Path

Every `Agent.run(...)` call creates one task request, Runtime session, user-scoped store,
Skill index, and adaptive task loop:

```text
Agent.run(...)
  -> AgentRuntime.run_task(...)
  -> prepare one progressive Skill index
  -> plan one or more task steps
  -> select one model and load selected Skills
  -> run model, tools, and subagents
  -> record events, evaluation, freshness, and evolution review
```

A direct task is a one-step plan. A decomposed task uses the same step executor. There is
no separate controller for workflows, memory, subagents, CLI, or Web requests.

## Optional Parts

The smallest run needs only an Agent and a model source. Other behavior is progressive:

- No conversation ID means no conversation history is loaded or written.
- No conversation ID means temporary memory is unavailable; long-term memory remains
  available when a memory Skill is selected.
- With a conversation ID, long-term organization may inspect relevant temporary memory and
  explicitly promote an abstraction without moving or deleting its temporary sources.
- No selected memory Skill means no memory is recalled or updated.
- No selected workflow Skill means the direct workflow is used.
- The built-in planner may decompose a task when its deterministic rules match.
- No selected MCP or custom tool Skill means no corresponding tools are exposed.

Missing optional parts do not trigger substitutes. A missing model, invalid Skill,
failed memory organization, or failed Provider call is an explicit error.

## Conversations

Conversations are event-backed Runtime views. Create one and reuse its ID:

```python
from super_agent import Agent

agent = Agent()
alice = agent.for_user("alice")
conversation = alice.conversations.create("Project")
alice.run("first turn", conversation_id=conversation.conversation_id)
alice.run("second turn", conversation_id=conversation.conversation_id)
```

Runtime loads prior messages and appends both sides of each completed turn. A
`conversation_id` cannot be combined with an explicit message list because that would
create two history sources. Create, list, read, rename, clear, and delete operations are
explicit.

## Workflow Skills

Pin one workflow in `agent.skills` when direct execution is not enough:

```toml
[agent]
skills = ["workflow:react"]
```

```toml
schema_version = 3
name = "react"
type = "workflow"
description = "Tool-using workflow"
version = "0.1.0"
triggers = []

[configuration]
mode = "react"
max_steps = 8
instruction = "Finish when the task is complete."
```

The supported modes are `direct`, `plan`, `react`, and `loop`. React and loop
workflows let the model use currently loaded disclosure, MCP, memory, and subagent tools.
The workflow's maximum steps and completion conditions provide an explicit exit from tool
and nested work.

## Model Scheduling

Model Skills describe purpose, supported features, expected quality, latency, cost, and
Provider connection metadata. Before a call, Core combines those declared traits with
user-and-Agent-scoped run evidence and a bounded exploration score. The chosen model and
reasons are written to the task schedule.

A Provider failure is recorded with `will_retry = false` and raised. Core does not switch
to another model after a failed call. Choosing another model is a new, visible scheduling
decision before execution, never a hidden fallback.

```python
result = alice.run("Summarize this")
alice.runs.record_feedback(result.run_id, 0.8, "Useful summary")
stats = alice.runs.list_model_routing_stats(purpose="summary")
```

## Run Traces

Each run receives a unique `run_id`. With default JSONL storage, canonical events are
appended to:

```text
.super-agent/users/<user-hash>/events.jsonl
```

The `runtime.locked` event records the effective Agent settings, model profile and
Provider implementation, task schedule, storage backend, SkillRunner hashes, and exact
Skill revisions. Secret values are never stored.

```bash
super-agent runs status --config agent.toml
super-agent runs explain --config agent.toml --run-id <run-id>
super-agent runs export --config agent.toml --run-id <run-id> --output run.json
```

Explain and export rebuild their views from canonical events and verify the Runtime lock
hash. Child runs can be found across the configured subagent tree only inside the same
user and storage backend.

## Multi-Agent Composition

Create each Agent normally, then attach it in code:

```python
from super_agent import Agent

main = Agent("agents/main.toml")
coder = Agent("agents/coder.toml")
reviewer = Agent("agents/reviewer.toml")

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

Omitting `name` creates `subagent01`, `subagent02`, and so on. Each child keeps its
own configuration, Provider set, Skills, storage scope, and child graph.

Before execution, Core reports complete cycle paths such as
`main -> coder -> reviewer -> main` and paths deeper than
`max_agent_chain_depth`. These are warnings, not execution limits. Omitting the setting
allows unlimited depth; workflow completion remains the exit mechanism.

## State and Isolation

One backend-neutral event stream is the source of truth for conversations, run traces,
memory, Skill evidence, disclosure history, and evolution state. JSONL and SQLite are
standard-library backends. MySQL and PostgreSQL are optional extras.

Every event and private workspace is scoped by validated user ID and Agent name. User Skill
overlays, caches, memory, conversations, and usage evidence cannot cross that boundary.
Shared project and built-in Skills remain read-only baselines.

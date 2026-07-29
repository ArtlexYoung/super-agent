# Runtime, Tasks, and Multi-Agent

## One Task Path

Every `Agent.run(...)` call creates one task request, complete `Run`, event log, Skill
index, and adaptive task loop. `AgentRuntime` is the only owner of that construction;
there is no resource wrapper, request wrapper, or second user-model Runtime. The log
stays in memory or writes through the selected
backend; domain state is attached only when storage is enabled:

```text
Agent.run(...)
  -> AgentRuntime.run_task(...)
  -> prepare one progressive Skill index
  -> create one RunPlan with exactly one model decision
  -> preflight the planned Skills, services, tools, Provider, and subagents
  -> plan one or more task steps
  -> load and execute the prepared run
  -> run model, tools, and subagents
  -> emit an optional learning request after task execution
  -> independent subscribers evaluate, refresh evidence, and review evolution
```

A direct task is a one-step plan. A decomposed task uses the same step executor. There is
no separate controller for workflows, memory, subagents, CLI, or Web requests.

`RunPlan` is immutable, serializable, and contains only decisions. It separates all
selected Skills from the smaller model-context Skill set and includes the exact workflow,
optional planner, one `ModelDecision`, features, and subagents. Loaded policies and
callbacks stay in the internal `PreparedRun`; they never enter the plan. Missing,
ambiguous, or incompatible choices fail while the plan is created. Core records the same
plan in `task.scheduled` and the Runtime lock instead of rebuilding decisions for each
consumer.

After scheduling, preflight loads each planned Skill once and aggregates every detected
problem. `task.preflight.completed` is recorded before the first model or subagent call.
On failure, `TaskPreflightError.problems` contains all detected codes, targets, and
messages; Runtime records `task.preflight.failed` and does not lock or partially execute
the run.

## Optional Parts

The smallest run needs an Agent, a model source, and one valid scene. The shipped
`common` scene satisfies the default stateful path without user configuration. When
`use_storage=False`, Runtime evaluates scene service requirements before freezing the
plan. The shipped `stateless` scene is selected because it is the only compatible
candidate; the reason and excluded candidates are recorded. An explicitly requested
incompatible scene fails instead of being replaced. Stateless execution keeps ordered
events in `TaskResult.events` and creates no backend, cache, conversation, memory,
evaluation, or evolution state. Other behavior is progressive:

- No conversation ID means no conversation history is loaded or written.
- No conversation ID means temporary memory is unavailable; long-term memory remains
  available when a memory Skill is selected.
- With a conversation ID, an explicitly prepared long-term organization plan may inspect
  relevant temporary memory and promote an abstraction only after explicit apply.
- No selected memory Skill means no memory is recalled or updated.
- A scene may omit a planner; then no planning model call is available.
- Every scene must select one workflow. A missing or conflicting workflow is an error.
- No selected MCP or custom tool Skill means no corresponding tools are exposed.
- A selected MCP Skill requires a matching code registration before execution; Runtime
  never reads a command, transport, or environment from Skill content.

Missing optional parts do not trigger substitutes. A missing model, invalid Skill,
failed memory plan, or failed Provider call is an explicit error. Recall itself is a pure
ranked read and never starts memory organization.

## Optional Event-Driven Learning

Stateful runs enable post-run learning by default. After task execution, Runtime emits one
immutable `learning.requested` event. Four named subscribers then work independently:
evaluation persists revision evidence, freshness recalculates deterministic scores,
routing evidence projects model outcomes, and evolution reviews eligible Agent-owned
Skills. None of these services is inside the task loop.

Disable all built-in learning for one run without changing task execution:

```python
from super_agent import Agent, AgentRunOptions

result = Agent().run(
    "Answer without updating learned evidence",
    run_options=AgentRunOptions(learn_from_run=False),
)
```

Applications can observe the same immutable events in code:

```python
class AuditEvents:
    name = "audit"

    def handle_event(self, event):
        write_audit_record(event)


agent = Agent()
agent.add_event_subscriber(AuditEvents())
```

Subscriber names are unique and built-in names are reserved. A subscriber failure records
`runtime.subscriber.failed` and raises `RuntimeEventSubscriberError` after preserving the
completed result on `error.result`. Set
`AgentRunOptions(allow_subscriber_failures=True)` only when best-effort post-run work is
explicitly acceptable. `event_listener` remains the streaming interface and is not
treated as a learning subscriber. Stateless runs record `learning.skipped` and create no
evaluation or evolution state.

`RunEventLog` is the only run-event writer in both modes. Streaming listeners,
`TaskResult.events`, subscribers, and persisted replay therefore observe the same order.
Conversation, memory, evaluation, disclosure, and evolution projections load only when
their operation or selected Skill needs them.

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

## Task Scenes

Scene selection happens once before task scheduling. The order is the request's explicit
scene, one configured `scene:*`, one prompt-trigger match, and the unique default. Core
records `scene.selected` with the stable key and reason, then resolves that scene's Skill
references through the central progressive index.

`common` selects general prompt, memory, planner, and direct workflow Skills. `code`
selects repository-specific versions and a bounded tool loop. A project can add scene
Skills under any configured Skill root, and a user can create a private scene. The current
run never refreshes its prepared index after creation.

## Workflow Skills

Pin one workflow in `agent.skills` only when it should replace the workflow supplied by
the selected scene:

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
user-and-Agent-scoped run evidence and a bounded exploration score. Candidate ranking is
a pure input-to-output operation; only the final `ModelDecision` enters the `RunPlan`.

A Provider failure is recorded and raised without a retry marker or another model call.
Choosing another model requires a new, visible `RunPlan` before execution; it is never a
hidden fallback.

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
Provider implementation, RunPlan, storage backend, SkillRunner hashes, code-registered
MCP implementation and settings hashes, declared effects, and exact Skill revisions.
Secret and environment values are never stored.

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
Shared project and shipped scene Skills remain read-only baselines.

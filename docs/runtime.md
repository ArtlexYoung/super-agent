# Runtime, Tasks, and Multi-Agent

## One Task Path

Every `Agent.run(...)` call creates one task request, complete `Run`, event log, Skill
index, and adaptive task loop. `Runtime` is the only owner of that construction;
there is no resource wrapper, request wrapper, or second user-model Runtime. The log
stays in memory or writes through the selected
backend; domain state is attached only when storage is enabled:

```text
Agent.run(...)
  -> Runtime.run_task(...)
  -> prepare one progressive Skill index
  -> load one Scheduler Skill
  -> create one Plan with exactly one model decision
  -> preflight the planned Skills, services, tools, Provider, and subagents
  -> plan one or more task steps
  -> execute one RunContext shared by every step
  -> run model, tools, and subagents
  -> emit an optional learning request after task execution
  -> independent subscribers evaluate, refresh evidence, and review evolution
```

A direct task is a one-step plan. A decomposed task uses the same step executor. There is
no separate controller for workflows, memory, subagents, CLI, or Web requests.

`Plan` is immutable, serializable, and contains only decisions. It separates all
selected Skills from the smaller model-context Skill set and includes the exact workflow,
optional planner, one `ModelDecision`, features, and subagents. The task-local
`RunContext` pairs that Plan with loaded policies and callbacks; those mechanisms never
enter the Plan. Missing,
ambiguous, or incompatible choices fail while the plan is created. Core records the same
plan in `task.scheduled` and the Runtime lock instead of rebuilding decisions for each
consumer.

After scheduling, preflight loads each planned Skill once and aggregates every detected
problem. `task.preflight.completed` is recorded before the first model or subagent call.
On failure, `TaskPreflightError.problems` contains all detected codes, targets, and
messages; Runtime records `task.preflight.failed` and does not lock or partially execute
the run. Preflight also rejects state-changing tools and completion callbacks when an
embedded Runtime has no action checker. Read-only tools may remain available in that
minimal mode. The default Agent creates its standard rules only on the first checked
action, so a pure stateless model call does not initialize the action layer.

## Optional Parts

The smallest run needs only an Agent and a model source. The Python library is storage-free
by default. Runtime evaluates scene service requirements before freezing the Plan. If no
compatible scene exists, the Scheduler explicitly selects direct mode and records
`scene=null`, `workflow=null`, the reason, and excluded candidates. An explicitly requested
or Agent-restricted incompatible scene still fails. Storage-free execution keeps ordered
events in `RunResult.events` and creates no backend, cache, conversation, memory,
evaluation, or evolution state. Other behavior is progressive:

- No conversation ID means no conversation history is loaded or written.
- No conversation ID means temporary memory is unavailable; long-term memory remains
  available when a memory Skill is selected.
- With a conversation ID, an explicitly prepared long-term organization plan may inspect
  relevant temporary memory and promote an abstraction only after explicit apply.
- No selected memory Skill means no memory is recalled or updated.
- A scene may omit a planner; then no planning model call is available.
- A scene may omit a workflow; Runtime then performs one direct model call. Requesting
  tools without a selected tool-using workflow is an error.
- No selected MCP or custom tool Skill means no corresponding tools are exposed.
- A selected MCP Skill requires a matching code registration before execution; Runtime
  never reads a command, transport, or environment from Skill content.

Missing optional parts do not trigger substitutes. A missing model, invalid Skill,
failed memory plan, or failed Provider call is an explicit error. Recall itself is a pure
ranked read and never starts memory organization.

## Explicit Post-Run Learning

Every run stores immutable evidence in its terminal event. It does not update evaluation,
freshness, routing, or evolution state. A stateful caller starts those changes explicitly
after inspecting the task result:

```python
from super_agent import Agent

agent = Agent(use_storage=True)
result = agent.run("Answer this")
learning = agent.learn_from_run(result.run_id)
```

`agent.for_user("alice").runs.learn(run_id)` binds the same operation to a user. The CLI
equivalent is `super-agent runs learn --run-id <run-id>`. Completion is idempotent;
failures record their exact stage and raise instead of returning a partial success.

Applications can observe the same immutable events in code:

```python
class AuditEvents:
    name = "audit"

    def handle_event(self, event):
        write_audit_record(event)


agent = Agent(use_storage=True)
agent.add_event_subscriber(AuditEvents())
```

Subscriber names are unique. A subscriber failure records
`runtime.subscriber.failed` and raises `RuntimeEventSubscriberError` after preserving the
completed result on `error.result`. Set
`AgentRunOptions(allow_subscriber_failures=True)` only when best-effort observation is
explicitly acceptable. `event_listener` remains the streaming interface. Stateless runs
create no evaluation or evolution state, and explicit learning fails because storage is
disabled.

`RunEventLog` is the only run-event writer in both modes. Streaming listeners,
`RunResult.events`, subscribers, and persisted replay therefore observe the same order.
Conversation, memory, evaluation, disclosure, and evolution projections load only when
their operation or selected Skill needs them.

## Conversations

Conversations are event-backed Adapter views. Create one and reuse its ID:

```python
from super_agent import Agent

agent = Agent()
alice = agent.for_user("alice")
conversation = alice.conversations.create("Project")
alice.run("first turn", conversation_id=conversation.conversation_id)
alice.run("second turn", conversation_id=conversation.conversation_id)
```

The conversation Adapter loads prior messages and appends both sides of each completed turn. A
`conversation_id` cannot be combined with an explicit message list because that would
create two history sources. Create, list, read, rename, clear, and delete operations are
explicit.

## Task Scenes

Scene selection happens once before task scheduling. The routing model chooses from the
Agent's code-level allowlist and compact scene descriptions while preserving an explicit
request scene. Core records `scene.selected` with the stable key or `null` and its reason,
then resolves the selected Skill references through the central progressive index.

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

[entry]
instructions = "SKILL.md"

[configuration]
mode = "react"
max_steps = 8
```

The supported modes are `direct`, `plan`, `react`, and `loop`. React and loop
workflows let the model use currently loaded disclosure, MCP, memory, and subagent tools.
`mode`, `max_steps`, and non-empty `SKILL.md` instructions are required. The instructions
own completion conditions, while the step limit provides an explicit exit from tool and
nested work. Runtime does not append mode-specific instructions or a default step limit.

## Model Scheduling

Model Skills describe purpose, supported features, expected quality, latency, cost, and
Provider connection metadata. The selected Scheduler Skill supplies model instructions for
one structured routing call. That call receives compact scene and Skill descriptions,
model traits, scoped run evidence, available subagents, and explicit caller constraints.
It returns the scene, Skills, planning mode, purpose, execution model, and subagents that
enter the immutable `Plan`.

The built-in `scheduler:default` needs no user configuration. Runtime validates every key,
required feature, and explicit selection in the model response. Invalid output is an error;
there is no keyword matcher, local score ranking, retry, or default substitution. One
execution model is fixed for the task before preflight and execution. Custom Scheduler and
model Skills use the same ownership and evolution rules as every other passive Skill.

A Provider failure is recorded and raised without a retry marker or another model call.
Choosing another model requires a new, visible `Plan` before execution; it is never a
hidden fallback.

```python
result = alice.run("Summarize this")
alice.runs.record_feedback(result.run_id, 0.8, "Useful summary")
stats = alice.runs.list_model_routing_stats(purpose="summary")
```

## Run Traces

Each run receives a unique `run_id`. With JSONL storage enabled, canonical events are
appended to:

```text
.super-agent/users/<user-hash>/events.jsonl
```

The `runtime.locked` event records the effective Agent settings, model profile and
Provider implementation, Plan, storage backend, SkillLoader hashes, code-registered
MCP implementation and settings hashes, declared effects, and exact Skill revisions.
Secret and environment values are never stored.

```bash
super-agent runs status --config agent.toml
super-agent runs explain --config agent.toml --run-id <run-id>
super-agent runs export --config agent.toml --run-id <run-id> --output run.json
```

Explain and export read one canonical run stream once, rebuild every view from that same
input, and verify the Runtime lock hash. Child runs can be found across the configured
subagent tree only inside the same user; ambiguous Agent ownership is rejected.

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
)
main.add_subagent(
    reviewer,
    description="Reviews behavior and risks",
)
```

Omitting `name` creates `subagent01`, `subagent02`, and so on. Each child keeps its
own configuration, Provider set, Skills, storage scope, and child graph. The routing model
uses descriptions to choose delegation; Agent names and task text are not matched locally.

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

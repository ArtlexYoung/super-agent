# Runtime

`Agent.run()` is the simple entry point. `core.runtime.runtime.Runtime` owns the full run:
identity, optional storage, event recording, model turns, actions, and completion.

```python
from super_agent import Agent

result = Agent().run("Explain this module")
print(result.text)
print(result.run_id)
```

## Model Loop

The default model is resolved once and must be ready before its first call. Runtime does
not retry another Provider or substitute Mock after failure.

If multiple model Skills are configured, Runtime gives the default model one `use_model`
tool. Its candidates include each non-default model's description, support, strengths, and
readiness. The default model may explicitly delegate a subtask and receives the target
model's final text as a tool result. The delegated call cannot replace the default model,
request its own tools, or fall back after failure.

The model receives:

- The Agent's small system instruction.
- Explicit message history supplied by the caller.
- Instructions from configured Skills.
- A compact central Skill index.
- Tool definitions for disclosure, activation, and registered task actions.

The central disclosure core also tracks one 24,000-character context budget for the run.
Skill instructions, tool results, memory context, subagent results, and explicit reference
reads spend the same budget. Once exhausted, a disclosure result contains only its reference,
hash, total size, and next offset; it is never silently truncated.

It can return final text immediately or request tools until the active workflow's step
limit is reached. Invalid tool names, arguments, Skill references, and action requests fail
the run visibly.

Optional general tools are attached in code and remain absent by default:

```python
from adapter.general import attach_general_tools_to_agent
from super_agent import Agent

agent = Agent()
attach_general_tools_to_agent(agent)
result = agent.run("Calculate the mean of 2, 4, and 6")
```

The bundled general MCP Skill provides only bounded calculation and literal text search.
Network, files, and processes require separately registered tools.

## Stateless and Stateful Runs

`Agent()` is stateless by default. Run events are still returned in `RunResult.events`, but
no files are created.

```python
agent = Agent(use_storage=True)
user = agent.for_user("alice")
```

Storage enables conversation history, long-term memory, disclosure caches, saved run
snapshots, feedback, learning, and user Skill overlays. Requesting one of these without
storage raises `RuntimeError`.

The CLI and Web adapters opt into storage because their user-facing features are expected
to survive process restarts.

## Checkpoints

The Runtime records a compact checkpoint when a task is ready and after each model turn.
Checkpoint events contain an ID, label, step facts, selected Skill names, workflow, and
content hashes only; model text and tool payloads are not checkpoint state. Stateful callers
can list them with `user.runs.list_checkpoints(run_id)` and explicitly resume with
`user.runs.resume(run_id, prompt, checkpoint_id=...)`. Resume starts a new run and records
the relationship. It does not pretend to reconstruct unavailable model context.

## Conversations and Memory

```python
conversation = user.conversations.create("Design")
result = user.run(
    "Continue the design",
    conversation_id=conversation.conversation_id,
)
```

A conversation ID cannot be combined with an explicit message list. Conversation messages
are the only short-term memory and are sent only for that conversation. A memory Skill
provides long-term operations for durable facts, preferences, and abstractions. The model
can inspect the current conversation while deciding what belongs in long-term memory.

## Run Events

Important event families include:

- `run.*`: start, completion, failure, resume, and checkpoint records.
- `task.*`: task start, selected model/context, and result.
- `model.*`: calls and model turns.
- `skill.*` and `skills.*`: disclosure, activation, and use.
- `tool.*` and `action.*`: requested operations and outcomes.
- `subagent.*`: child execution.
- `agent_task.*`: queue creation, dispatch, state changes, waits, and wakeups.
- `learning.*`: explicit post-run learning.

An event listener can stream events without storage by using `AgentRunOptions` from
`core.models`. Subscriber failures are recorded and fail the call by default; callers may
explicitly allow them and inspect `subscriber_failures`.

## Users

`Agent.for_user(user_id)` is the only stateful user boundary. The returned `UserAgent`
offers `run`, `conversations`, `runs`, `skills`, and `configuration`. User IDs come from a
trusted application boundary, not model output.

All stores filter by user and Agent. A run from another scope is rejected instead of being
returned by a broad search.

## Subagents

Subagents are composed in code:

```python
main = Agent()
worker = Agent()
main.add_subagent(
    worker,
    name="worker",
    description="Handles repository changes",
    weight=1.5,
)
```

Omit `name` to receive `subagent01`, `subagent02`, and so on. Weight defaults to `1`; a higher
positive weight increases automatic dispatch priority. The model can list and call
registered subagents through explicit tools. Each child owns its configuration, model,
Skills, and storage scope. Task Skill selection belongs to each run and does not mutate
later runs.

Cycles are allowed. Before execution, Agent can report a cycle chain or a configured depth
warning; these are warnings, not execution limits. Workflow instructions and the model's
completion result provide the stopping mechanism.

### Native Agent Queues

Every Agent Runtime contains the same task queue mechanism. Task Skills decide when and how it
is exposed; they do not implement their own queue. The optional
`task:common-multi-producer-consumer` Skill mounts create, dispatch, inspect, cancel, and wait
tools for general multi-Agent work. Omitting it leaves those tools absent from that run.

```python
from super_agent import Agent

main = Agent()
main.add_subagent(Agent(), name="coder", purpose="implementation")
main.add_subagent(Agent(), name="reviewer", purpose="code-review")
result = main.run(
    "Implement and review the change",
    skill="common-multi-producer-consumer",
)
```

Each subagent has one serial consumer, while different subagents may execute concurrently.
The main model can request a bounded sleep with `wait_for_agent_tasks`; no model call occurs
while the tool is waiting. The Skill configuration caps each requested sleep:

```toml
[configuration.tools.agent_tasks]
max_tasks = 32
max_wait_seconds = 60
record_mode = "adaptive"
compress_after_tasks = 8
summary_chars = 2000
max_nested_results = 8
agent_selection = "least_busy"
circuit_breaker_failures = 1
circuit_breaker_wait_seconds = 30
retry_unavailable_times = 1
```

Queue records can be `full`, `summary`, or `adaptive`. Adaptive mode keeps the first
`compress_after_tasks` child runs complete, then records later child runs as a bounded text
preview with prompt and result hashes, character counts, and a bounded nested-result list.
The policy is selected when each task starts, so concurrent consumers do not change an
already-started task. Summary child runs also omit model text, tool arguments, and child
prompts from their persisted Runtime events. Their live result still contains the configured
preview needed by the parent Agent to compare a batch.

Automatic selection estimates prompt input tokens with the same deterministic estimator used by
Provider calls. The producing model may also declare `estimated_output_tokens`,
`estimated_cache_creation_tokens`, and `estimated_cache_read_tokens` when it creates a task.
Each value is optional, non-negative, and capped at 10 million. Runtime never guesses an omitted
output or cache amount; `cost_estimate.excludes_unprovided_usage` identifies a partial estimate.

Runtime combines those token counts with the model Skill's four prices to calculate both an
estimated call cost and a blended price per million tokens. Selection then scores each Agent as
`weight * run reliability / (1 + blended price)` and adjusts for its current queue load. Run
reliability starts at `1`, decreases only after an unavailable failure, and recovers through
successful tasks. Dispatch events record the task estimate, four prices, reliability samples,
estimated model, queue load, and final score used for the decision.

`agent_selection = "least_busy"` uses that weighted cost and reliability score. The optional `rotate`
mode cycles through every compatible Agent even when earlier tasks have already finished. It
rejects an explicit `agent_name`, records `selected_by=skill_rotation` and the eligible Agent
count, and lets each selected Agent use its own model configuration.

Unavailable Provider, connection, timeout, HTTP 429, and HTTP 5xx failures count toward the
run-scoped circuit breaker. An open Agent is skipped in favor of another compatible Agent. If
none is ready, the task remains visibly queued until the cooldown expires, then reserves one
half-open probe. Success closes the circuit and updates reliability; failure reopens it.
`retry_unavailable_times` bounds actual reruns, and ordinary task, tool, validation, and
model-output errors fail without opening the circuit or changing health. Events include
`circuit_opened`, `fallback_selected`, `retry_scheduled`,
`circuit_half_open`, and `circuit_closed`; retries never happen silently.

Available wake triggers are `timeout`, `any_task_finished`, `any_task_completed`,
`any_task_failed`, `all_tasks_finished`, and `selected_tasks_finished`. Task states move
through explicit `created`, `queued`, `running`, `completed`, `failed`, or `cancelled`
transitions. Prompts and result bodies stay out of `agent_task.*` events; the run result
contains bounded status snapshots and normal traced subagent results.

### Deep Optimization

`task:code-multi-deep-optimization` reuses the same native queue for evidence-heavy coding,
competition, and performance work. The main Agent owns global planning and parallel batches;
each first-level batch Agent activates the same deep-optimization Skill in its own Runtime and
dispatches minimal experiments to its own rotating child Agents.

```python
from super_agent import Agent

batch = Agent()
batch.add_subagent(Agent(), name="experiment-a", purpose="experiment")
batch.add_subagent(Agent(), name="experiment-b", purpose="experiment")

main = Agent()
main.add_subagent(batch, name="batch-a", purpose="optimization-batch")
result = main.run("Optimize against the benchmark", skill="code-multi-deep-optimization")
```

Each Agent keeps its own model, configuration, Skills, queue, and child graph. This allows
second-level experiments to use different models and viewpoints while first-level Agents judge
their batch evidence and the main Agent avoids optimizing only one local direction. Batch Agents
should start without a fixed Task Skill and activate `code-multi-deep-optimization` for their
assigned batch. Register at least two compatible batch or experiment Agents with the same broad
purpose and different model configurations when diversity is required. The rotating queue then
changes Agent and model without permanently assigning one hypothesis family to one specialist.
Experiment Agents activate `task:code` and should use isolated worktrees or separate workspaces
for concurrent changes.

## Learning

```python
learning = user.runs.learn(result.run_id)
```

Learning is explicit and idempotent for a completed run. It records evaluations, updates
freshness metrics, and summarizes model use. It never proposes, applies, or undoes a Skill
change; those are separate user operations.

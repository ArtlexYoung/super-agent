# Runtime

`Agent.run()` is the simple entry point. `skill.task.runtime.Runtime` owns the full run:
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

It can return final text immediately or request tools until the active workflow's step
limit is reached. Invalid tool names, arguments, Skill references, and action requests fail
the run visibly.

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

- `run.*`: start, completion, and failure.
- `task.*`: task start, selected model/context, and result.
- `model.*`: calls and model turns.
- `skill.*` and `skills.*`: disclosure, activation, and use.
- `tool.*` and `action.*`: requested operations and outcomes.
- `subagent.*`: child execution.
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
main.add_subagent(worker, name="worker", description="Handles repository changes")
```

Omit `name` to receive `subagent01`, `subagent02`, and so on. The model can list and call
registered subagents through explicit tools. Each child owns its configuration, model,
Skills, storage scope, and scene policy.

Cycles are allowed. Before execution, Agent can report a cycle chain or a configured depth
warning; these are warnings, not execution limits. Workflow instructions and the model's
completion result provide the stopping mechanism.

## Learning

```python
learning = user.runs.learn(result.run_id)
```

Learning is explicit and idempotent for a completed run. It evaluates recorded evidence,
updates freshness metrics, and may create a Skill candidate under the configured evolution
policy. Promotion remains a separate checked action.

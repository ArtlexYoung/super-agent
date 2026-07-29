# Architecture

Super Agent keeps five responsibilities separate and gives each one a direct name.

```text
Provider     connects model intelligence
Core         executes prepared model calls and checks declared actions
SkillRunner  turns one Skill type into task behavior
Skill        carries passive content and configuration
Agent        composes Providers, Core options, Skills, storage, and subagents
```

## Source Layout

The repository keeps passive scene data outside the three Python packages:

```text
skill_scenes/       shipped common and optional task-scene Skill trees
src/
  adapter/          external CLI and AG-UI entry points
  core/             Skill-independent Runtime, Provider, events, checks, and storage ports
  skill/            Skill format, scheduling, state, runners, packages, and evolution
  cli.py
  super_agent.py
```

`adapter` and `skill` may import Core. Core never imports Skill, CLI, HTTP, React, or
another external interaction layer. `super_agent.py` is the composition and everyday API; advanced APIs are imported
from the adapter, Core, or Skill module that owns them.

`Agent` construction reads and validates configuration and prepares only in-memory code
registries. Storage creation, Skill indexing, model discovery, and Runtime assembly happen
together on first use behind one lock. Components are published only after the whole build
succeeds, so concurrent first calls share one Runtime and a failed build leaves no partial
state. CLI commands use the same three direct loaders for configuration, Agent, and scoped
RuntimeStore creation.

## One Task Path

Every run enters one kernel:

```text
Agent.run(...)
  -> AgentRuntime.run_task(TaskRequest)
  -> Run with one RunEventLog and an optional RuntimeStore
  -> progressive index selects one scene and its Skill references
  -> one immutable RunPlan fixes scene, Skills, workflow, planner, and one model
  -> preflight validates the complete run before any model or subagent call
  -> one Skills snapshot loads the run and one task loop executes it
  -> one terminal event keeps immutable learning evidence
  -> an explicit post-run call evaluates evidence and reviews Skill evolution
```

Direct tasks are one-step plans. Complex tasks are multi-step plans. Both use the same
loop, event stream, model routing, tool registry, and stopping checks. There is no second
controller for workflows, memory, planning, or subagents.

`RunPlan` is the only execution decision object. It contains stable scene, Skill,
workflow, and planner references, exactly one model decision with reasons, required
features, and subagent choices. Loaded policies and callbacks live only in `PreparedRun`.
Core creates the plan before the matching execution and records that same object in the
task event and Runtime lock. Planned steps use the same contract. There are no candidate
lists or partial selection objects to reconcile later.

Preflight loads each planned Skill once into the Run and aggregates missing
runners, declared services, invalid tools, the selected Provider, and subagents into one
report. A failed report raises `TaskPreflightError` before the Runtime lock, model call,
tool handler, or subagent run. Execution reuses the checked Skill contributions.

`Run` is the only mutable per-run context. It is complete when constructed and carries the
validated identity, one small `RunEventLog`, optional store, Skill index, disclosure core,
selected model state, one Skills snapshot, action runner, event subscribers, and evidence tracker.
`AgentRuntime` constructs it directly from explicit dependencies; no resource container,
session request, user-model wrapper, factory shell, or late disclosure setter exists. The
event log uses memory without a backend and persists the same ordered records when a
backend exists. Derived CLI and Web views are projected from canonical events, not from
parallel mutable state.

Post-run learning is an explicit operation, not part of task execution. A completed or
failed run carries immutable evidence but changes no learned state. `learn_from_run`
applies evaluation, freshness, routing evidence, and Skill evolution in one visible phase;
it records the failing stage and raises any error. Custom Runtime event subscribers remain
separate observers of recursively read-only run events.

## Central Progressive Disclosure

All Skill types use one central `Skills` object. Its internal progressive reader:

1. Scan user, project, and shipped scene sources into one compact index.
2. Select exactly one task scene by request, Agent configuration, prompt trigger, or the
   unique default.
3. Resolve the scene's ordinary Skill references through the same index.
4. Read and verify source content without changing cache, history, or usage state.
5. Disclose instructions, configuration, and resources only through an explicit operation.
6. Reuse content-addressed cache paths when Runtime explicitly enables recording.
7. Store disclosure history in the same user-and-Agent event scope as the run.

Offline discovery and ordinary reads are pure. Disclosure writes cache and history only
when explicitly requested, while activation separately loads runner behavior and records
actual Skill use. Inspecting available Skills has no hidden side effect.

## SkillRunners

A SkillRunner is trusted application code registered for exactly one `skill_type`. It has
one loading method:

```python
loaded = runner.load_skill(request)
```

`LoadedSkill` may contribute model context, prompt context, tools, included Skills, a task
policy, a planning policy, and a completion callback. Core consumes this one shape for
every type. Scene, prompt, MCP, memory, workflow, and planner behavior therefore requires
no separate loader path in Core.

Downloaded Skill directories are always passive. Runtime never imports Python from them.
Custom executable behavior is added explicitly with `Agent.add_skill_runner(...)`. MCP
servers use `Agent.add_mcp_server(...)`; the Skill stores only a registered name while
trusted code owns the command, environment, transport, and declared effects. The Runtime
lock records implementation and settings hashes without environment values.

## Providers and Model Skills

Provider code only normalizes model protocol calls. Model names, purposes, supported
features, quality, expected latency, cost, and connection environment names live in
ordinary model Skills. `ProviderPool` creates a connection lazily after Core selects a
ready model profile.

There is no implicit Mock Provider and no model-failure fallback. A failed selected call
records the error and raises it without a retry marker. The exact `ModelDecision` is fixed
before a call and is visible in the plan, Runtime lock, selection event, and Provider call.

## State and Isolation

```text
Run
  -> RunEventLog
       -> optional StorageBackend
  -> optional RuntimeStore
       -> scoped canonical events
       -> lazy conversation / memory / disclosure / evaluation projections
```

Every event contains one validated user and Agent scope. Conversations, memory, Skill
usage, disclosure history, model evidence, Provider caches, user Skill overlays,
evaluations, and evolution state remain inside that scope. Shared project and shipped
scene Skills are read-only baselines. The index reports shipped content with the
`builtin` source label, so resolution order is `user > project > builtin`.

`RuntimeStore` is the only persisted event boundary. It does not expose its backend;
memory and disclosure receive the store itself instead of independent writer and reader
callbacks. Run explanation reads one canonical stream once, then derives the snapshot,
ordered Runtime events, lock, decisions, and disclosure path from that same input.

JSONL and SQLite use only the standard library. Remote database drivers are imported only
after their backend is explicitly selected.

Concrete Skill kinds, storage implementations, state projections, learning, evolution,
and adapters are also imported only when their corresponding runner or service is used.
A stateless task does not initialize those optional layers.

`RuntimeStore` lazily creates disclosure and memory state and imports evaluation or view
code only for the matching operation. Ordinary runs keep persistent traces without
initializing evaluation, freshness, or evolution services; explicit learning loads them.

## Explicit Side Effects

Every Runtime tool declares one resource and one or more effects. `ActionRunner` checks
that declaration before invoking the handler and records the decision or failure. A
missing declaration is an error; there is no permissive fallback. Mutations are checked,
prepared, and then explicitly applied. Reads run directly after their check. Skill text
cannot change an action declaration because it is treated as untrusted model context.

Management operations such as conversation changes, model Skill writes, and memory
forgetting use the same action boundary. The Web and CLI adapters do not bypass it.

## Evolution

Core records which exact Skill revisions affected each run. Eligible Agent-owned Skills
enter one candidate, evaluation, promotion, monitoring, and rollback state machine. The
candidate unit is the complete Skill directory, and promotion requires non-regression
evidence for every evaluation case. The state machine binds one immutable report by ID and
SHA-256 to the normalized cases plus candidate and baseline directory hashes. Manual and
automatic evolution use this same gate. Activation checks source, copied, and current
target hashes around the atomic directory switch. Runtime refresh and state publication
either complete together or restore the previous Skill explicitly. Executable SkillRunner
code remains reviewed application code rather than downloadable Skill content. Package
installation and updates reuse the same verified directory switch.

## Invariants

- One task has one complete Run, disclosure core, task loop, and event stream; storage
  is one explicit optional service.
- Every model execution reads one immutable RunPlan created before that execution.
- Evaluation and evolution never write directly from the task loop; they observe immutable
  Runtime events and can be disabled without changing execution.
- One run selects exactly one scene before loading memory, planning, workflow, and prompt
  content; the selected scene and reason are recorded.
- Every Skill type uses the same index, cache, stable key, evidence, and evolution format.
- Core has no hard-coded list of custom Skill types.
- Provider failures, memory organization failures, and invalid evolution candidates are
  explicit errors rather than silent degradation.
- User, Agent, conversation, and parent-run identity is preserved through subagent work.
- AG-UI and CLI are adapters over Core, never alternative task engines.
- Internal compatibility imports and schema conversion are intentionally absent.

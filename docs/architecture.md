# Architecture

Super Agent keeps five responsibilities separate and gives each one a direct name.

```text
Provider     connects model intelligence
Core         schedules tasks and owns mutable Runtime state
SkillRunner  turns one Skill type into Runtime behavior
Skill        carries passive content and configuration
Agent        composes Providers, Core options, SkillRunners, storage, and subagents
```

## Source Layout

The repository keeps passive scene data outside the three Python packages:

```text
skill_scenes/       shipped common and optional task-scene Skill trees
src/
  adapter/          external CLI and AG-UI entry points
  core/             Runtime, Provider, task, state, storage, and evolution orchestration
  skill/            Skill format, disclosure, runners, packages, and evaluation
  cli.py
  super_agent.py
```

`adapter` may import Core. Core never imports CLI, HTTP, React, or another external
interaction layer. `super_agent.py` is the only public API aggregate.

## One Task Path

Every run enters one kernel:

```text
Agent.run(...)
  -> AgentRuntime.run_task(TaskRequest)
  -> RuntimeSession and RuntimeStore
  -> progressive index selects one scene and its Skill references
  -> SkillRunners load selected Skills
  -> one adaptive task loop executes plans, models, tools, and subagents
  -> canonical events, evaluation, freshness, and evolution review
```

Direct tasks are one-step plans. Complex tasks are multi-step plans. Both use the same
loop, event stream, model routing, tool registry, and stopping checks. There is no second
controller for workflows, memory, planning, or subagents.

`RuntimeSession` is the only mutable run context. It carries the validated identity,
store, Skill index, disclosure core, selected model state, SkillRunners, action runner,
and evidence tracker. Derived CLI and Web views are projected from canonical events, not
from parallel mutable state.

## Central Progressive Disclosure

All Skill types use `ProgressiveDisclosureCore`:

1. Scan user, project, and shipped scene sources into one compact index.
2. Select exactly one task scene by request, Agent configuration, prompt trigger, or the
   unique default.
3. Resolve the scene's ordinary Skill references through the same index.
4. Read a manifest only for a selected reference.
5. Read instructions, configuration, and resources only when its SkillRunner asks.
6. Reuse content-addressed cache paths when Runtime explicitly enables recording.
7. Store disclosure history in the same user-and-Agent event scope as the run.

Offline discovery is read-only by default. Cache and history writes are explicit options,
so inspecting available Skills has no hidden side effect.

## SkillRunners

A SkillRunner is trusted application code registered for exactly one `skill_type`. It has
one loading method:

```python
loaded = runner.load_skill(request)
```

`LoadedSkill` may contribute model context, prompt context, tools, a scene policy, a task
policy, a planning policy, and a completion callback. Core consumes this one shape for
every type. Scene, prompt, MCP, memory, workflow, and planner behavior therefore requires
no separate loader path in Core.

Downloaded Skill directories are always passive. Runtime never imports Python from them.
Custom executable behavior is added explicitly with `Agent.add_skill_runner(...)`, and
the exact implementation hash is written to the Runtime lock.

## Providers and Model Skills

Provider code only normalizes model protocol calls. Model names, purposes, supported
features, quality, expected latency, cost, and connection environment names live in
ordinary model Skills. `ProviderPool` creates a connection lazily after Core selects a
ready model profile.

There is no implicit Mock Provider and no model-failure fallback. A failed selected call
records `will_retry = false` and raises the original error. Explicit routing decisions
occur before a call and are visible in the trace.

## State and Isolation

```text
RuntimeSession
  -> RuntimeStore
       -> one StorageBackend
            +-> JSONL
            +-> SQLite
            +-> MySQL
            +-> PostgreSQL
```

Every event contains one validated user and Agent scope. Conversations, memory, Skill
usage, disclosure history, model evidence, Provider caches, user Skill overlays,
evaluations, and evolution state remain inside that scope. Shared project and shipped
scene Skills are read-only baselines. The index reports shipped content with the
`builtin` source label, so resolution order is `user > project > builtin`.

JSONL and SQLite use only the standard library. Remote database drivers are imported only
after their backend is explicitly selected.

## Explicit Side Effects

Every Runtime tool declares one resource and one or more effects. `ActionRunner` checks
that declaration before invoking the handler and records the decision, completion, or
failure. A missing declaration is an error; there is no permissive fallback. Skill text
cannot change an action declaration because it is treated as untrusted model context.

Management operations such as conversation changes, model Skill writes, and memory
forgetting use the same action boundary. The Web and CLI adapters do not bypass it.

## Evolution

Core records which exact Skill revisions affected each run. Eligible Agent-owned Skills
enter one candidate, evaluation, promotion, monitoring, and rollback state machine. The
candidate unit is the complete Skill directory, and promotion requires non-regression
evidence for every evaluation case. Executable SkillRunner code remains reviewed
application code rather than downloadable Skill content.

## Invariants

- One run has one Runtime session, store, disclosure core, task loop, and event stream.
- One run selects exactly one scene before loading memory, planning, workflow, and prompt
  content; the selected scene and reason are recorded.
- Every Skill type uses the same index, cache, stable key, evidence, and evolution format.
- Core has no hard-coded list of custom Skill types.
- Provider failures, memory organization failures, and invalid evolution candidates are
  explicit errors rather than silent degradation.
- User, Agent, conversation, and parent-run identity is preserved through subagent work.
- AG-UI and CLI are adapters over Core, never alternative task engines.
- Internal compatibility imports and schema conversion are intentionally absent.

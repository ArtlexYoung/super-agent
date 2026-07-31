# Architecture

Super Agent separates five responsibilities:

```text
Provider supplies model intelligence
Runtime owns one task lifecycle
SkillLoader turns trusted Skill types into instructions and tools
Skill stores passive content and configuration
Agent composes Providers, Skills, storage, rules, and subagents
```

The repository has three internal roots plus two entry files:

```text
src/
  core/       configuration, Provider contracts, action checks, event values
  skill/      disclosure, loaders, task loop, state, and evolution
  adapter/    CLI, Web, AG-UI, storage, and user-facing state access
  super_agent.py
  cli.py
```

`core` never imports `skill`. It defines stable protocols and plain values. `skill` builds
the Agent behavior on those protocols. `adapter` connects external interfaces and durable
backends. `super_agent.py` is the composition root and common API.

## One Task Path

```text
Agent.run
  -> task Runtime creates Run identity and event log
  -> Skills builds one central index
  -> ModelLoop selects the configured default model
  -> model receives the prompt, selected context, and Skill index
  -> ModelCalls sends one measured call through core Provider adapters
  -> model returns final text or checked tool calls, including explicit use_model delegation
  -> Runtime records completion or the exact failure
```

There is no keyword router, separate planner engine, or preflight controller. Planning is
ordinary Skill instruction. A task scene is an ordinary Skill group. The model can inspect
or activate either through the same tools it uses for every other Skill.

The configured default model owns the task loop. When other model Skills exist, Runtime
adds one `use_model` tool containing their descriptions, support, strengths, and readiness.
The default model may send one explicit subtask to one of them and receives the result as a
tool result. That call does not replace the default model for later turns, and failure is
propagated without a fallback call.

## Progressive Disclosure

`ProgressiveDisclosureCore` is the only content discovery path:

1. Build compact index entries from built-in, project, and user Skill sources.
2. Expose names, types, descriptions, versions, hashes, and features.
3. Open a selected manifest, instructions, or configuration on demand.
4. Return a stable cache path when storage-backed disclosure is enabled.
5. Activate executable behavior only through a registered SkillLoader.

Reading disclosed content does not activate a Skill. Activating a Skill does not grant new
authority: every tool carries explicit effects and still passes through action rules.

## Optional State

The basic path uses an in-memory event log and no storage. Stateful features attach through
an explicit backend:

- Conversations persist ordered user and assistant messages as short-term context.
- Memory Skills persist abstract long-term items and usage habits.
- Run learning records evaluations, freshness evidence, and Skill candidates.
- Disclosure caches preserve paths previously opened by the model.

Every persisted stream includes a trusted user ID and Agent name. An unavailable optional
feature raises an error when requested; Runtime does not replace it with an in-memory
version.

## Side Effects

`ActionRequest` declares the actor, resource, effects, and argument names before execution.
`ActionRunner` records requested, applied, blocked, or failed outcomes. Skill text remains
untrusted data and cannot register Python, processes, MCP servers, or permissions.

Provider calls, model selection, Skill disclosure, tool calls, subagent calls, and state
changes are visible in the run event stream. Errors are propagated after Runtime attempts
to record the failure.

## Evolution

Learning is outside the task's implicit completion path. A caller starts it explicitly with
`user.runs.learn(run_id)`. Agent-owned or user-authorized Skills move through candidate,
evaluation, promotion, monitoring, and rollback records. Candidate creation and evaluation
do not modify the active Skill.

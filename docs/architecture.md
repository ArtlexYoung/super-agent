# Architecture

Super Agent separates five responsibilities:

```text
Provider supplies model intelligence
Runtime owns one task lifecycle
Skill handlers turn selected content into checked instructions and tools
Skill stores passive content and configuration
Agent composes Providers, Skills, storage, rules, and subagents
```

The repository has three internal roots plus two entry files:

```text
src/
  core/       Runtime, Provider contracts, configuration, checks, and state
  skill/      passive Skill manifests, source discovery, index, and disclosure
  adapter/    CLI, Web, AG-UI, storage, and user-facing state access
  super_agent.py
  cli.py
```

`core` owns execution and may read passive Skill definitions. `skill` does not own the
Runtime. `adapter` connects external interfaces and durable backends. `super_agent.py` exports
the Agent implemented by `adapter/agent.py`; `core/runtime.py` owns the task lifecycle, while
`core/team.py` owns child Agent composition through a small protocol. Core never
imports Adapter implementations.
The CLI command owner is `adapter.cli`; it owns direct execution, checks, and serving, while
`cli_skills.py` and `cli_data.py` group stateful command domains. All parsed command branches use
the same explicit dispatcher and nested parser constructor. `src/cli.py` only makes direct
source-tree execution possible.

## Ownership Map

| Concern | Owner | Does not own |
| --- | --- | --- |
| Model protocols and calls | `core/provider.py`, `core/model_calls.py` | Model profiles or task routing |
| Storage protocol and records | `core/records/store.py` | Concrete JSONL, SQL, or Web I/O |
| Scoped state store | `core/records/store.py` | Backend construction or external commands |
| One run event log | `core/records/events.py` | Durable backend selection |
| One run lifecycle | `core/runtime.py` | CLI, Web, or storage policy |
| Agent composition | `adapter/agent.py` | Runtime internals or storage implementations |
| Lazy Runtime resources and child graph | `core/runtime.py`, `core/team.py` | External interfaces |
| Task queues and groups | `skill/tasks/` | Generic Run lifecycle |
| Skill discovery | `skill/discovery/` | Storage writes or Skill mutation |
| Skill execution | `skill/handlers/` | Agent learning or external adapters |
| Model Skill management | `skill/handlers/model_management.py` | Provider calls or model routing |
| Skill evidence and changes | `skill/learning/` | Implicit updates during a run |
| Conversations | `core/records/conversations.py` | Storage backend construction or UI |
| Durable storage I/O | `adapter/storage_backends/` | Runtime state policy |
| AG-UI and Web I/O | `adapter/http/` | Runtime decisions |

Dependencies point from adapters into Core and Skill owners. `super_agent.Agent` is the default
Adapter composition and supplies concrete storage factories to Core. Skill discovery remains passive;
Runtime may consume disclosed Skill content, while optional learning is invoked explicitly after
a run. Removed ownership paths are release-tested as failed imports rather than retained through
aliases or forwarding modules.

## One Task Path

```text
Agent.run
  -> task Runtime creates Run identity and event log
  -> Skills builds one central index
  -> TaskRunner selects the configured default model
  -> model receives the prompt, selected context, and Skill index
  -> ModelCaller sends one measured call through core Provider adapters
  -> model returns final text or checked tool calls, including explicit use_model delegation
  -> Runtime records completion or the exact failure
```

There is no keyword router, separate planner engine, or preflight controller. Planning is
ordinary Skill instruction. A task Skill combines instructions with one run policy. The
model can inspect or activate either through the same tools it uses for every other Skill.

The Core Runtime owns the generic task lifecycle, while a selected Task Skill may install the
optional queue and group mechanism for that run. Child Agents use the same Skill-owned mechanism
with their own child graph, so nested producer-consumer work does not add a second scheduler.

The configured default model owns the task loop. When other model Skills exist, Runtime
adds one `use_model` tool containing their descriptions, support, strengths, and readiness.
The default model may send one explicit subtask to one of them and receives the result as a
tool result. That call does not replace the default model for later turns, and failure is
propagated without a fallback call.

## Progressive Disclosure

`ProgressiveDisclosureCore` is the only content discovery path. Its optional recorder is a
storage port; JSONL, SQLite, and remote storage implementations live under
`adapter/storage_backends/`
and are injected by `super_agent.Agent`:

1. Build compact index entries from built-in, project, and user Skill sources.
2. Expose names, types, descriptions, versions, hashes, and features.
3. Open a selected manifest, instructions, or configuration on demand.
4. Return a stable cache path when storage-backed disclosure is enabled.
5. Pass selected content to Runtime; disclosure itself never activates behavior.

Reading disclosed content does not activate a Skill. Activating a Skill does not grant new
authority: every tool carries explicit effects and still passes through action rules.

## Optional State

The basic path uses an in-memory event log and no storage. Stateful features attach through
an explicit backend:

- Conversations persist ordered user and assistant messages as short-term context.
- Memory Skills persist abstract long-term items and usage habits.
- Run learning records evaluations, freshness evidence, and model use.
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

## Skill Changes

Learning is outside the task's implicit completion path. A caller starts it explicitly
with `user.runs.learn(run_id)`, and it never changes active content. Agent-owned or
user-authorized Skills can be proposed, tested, applied, and undone through four separate
operations. Proposal and testing do not modify the active Skill.

## Release Gate

The release suite verifies the claims above as behavior:

- JSONL and SQLite pass the same conversation, memory, run, disclosure, and Skill change
  isolation checks for multiple users and Agents.
- Stateless runs do not create storage or import optional state and update modules.
- Disabled storage and failed Providers raise their original errors without substitution.
- Skill changes remain four separate propose, test, apply, and undo operations.
- Python source follows the locked `v0.1.81-v0.1.90` reduction budgets and finishes below
  9,750 non-empty lines, while per-file, function, complexity, and directory-size limits remain
  enforced.
- Primitive input and persisted-record validation enters through `core.models`; domain modules
  retain only rules that carry domain-specific meaning or public error contracts.
- `StorageEventQuery` owns in-memory event matching. EventStore adds the trusted user and Agent
  scope, while each backend implements only its physical query execution.
- `StorageEvent` owns canonical field validation and immutable data copying. Storage adapters
  decode physical rows and preserve their transaction, schema, and connection responsibilities.
- `SkillIndexEntry` has one persisted projection, and `SkillDisclosure` sends every disclosed
  Skill document through the same cache, history, hashing, and context-budget path.
- Skill package, model, and evolution writes share `apply_skill_directory_updates`; the verified
  transaction returns its post-apply value and restores every activated target before surfacing
  a failure.
- The optional task Skill normalizes one selectable child-Agent fact. Queue dispatch and group
  dispatch use that same fact for feature matching, model choice, price, health, rotation, and
  circuit state; Core remains unaware of producer-consumer scheduling.
- One `ModelCallContext` carries the task purpose and exact run recorders through the model loop
  and configured-model tool. Text-only model users resolve one storage or run event writer before
  their first call, never by falling back after a Provider failure.
- External adapters share one parsed CLI dispatch boundary, one AG-UI byte-response path, and one
  SQL cursor lifecycle; the concrete commands, HTTP routes, and database dialects remain visible
  at their owning adapters.

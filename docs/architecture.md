# Architecture

Super Agent separates five responsibilities:

```text
Provider supplies model intelligence
Runtime owns one task lifecycle
Runtime turns selected Skill content into checked instructions and tools
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
Runtime. `adapter` connects external interfaces and durable backends. `super_agent.py` is the
public wiring entry point; `core/runtime/agent.py` owns the public Core actions, `setup.py` owns
lazy Runtime resources, and `team.py` owns child Agent composition. Core never imports Adapter
implementations: storage creation and user views are supplied by the public wiring entry point.
The CLI command owner is `adapter.cli_adapter.commands`; its `run`, `manage`, and `data`
directories group command domains, while `src/cli.py` only makes direct
source-tree execution possible.

## Ownership Map

| Concern | Owner | Does not own |
| --- | --- | --- |
| Model protocols and calls | `core/provider.py` | Model profiles or task routing |
| Storage protocol | `core/state/backend.py` | Concrete JSONL, SQL, or Web I/O |
| Scoped state store | `core/state/store.py` | Backend construction or external commands |
| One run event log | `core/state/run.py` | Durable backend selection |
| One run lifecycle | `core/runtime/run.py` | CLI, Web, or storage policy |
| Agent setup and child graph | `core/runtime/setup.py`, `team.py` | Skill content or Provider protocols |
| Task queues and groups | `skill/runtime/tasks/` | Generic Run lifecycle |
| Skill discovery | `skill/disclosure.py` | Storage writes or Skill mutation |
| Skill execution | `skill/runtime/` | Agent learning or external adapters |
| Skill evidence and changes | `skill/learning/` | Implicit updates during a run |
| Conversations | `core/state/conversations.py` | Storage backend construction or UI |
| External and durable I/O | `adapter/` | Runtime decisions or Skill mechanisms |

Dependencies point from adapters into Core and Skill owners. `super_agent.Agent` wires the
default Adapter factories into Core without making Core import them. Skill discovery remains passive;
Runtime may consume disclosed Skill content, while optional learning is invoked explicitly after
a run. Removed ownership paths are release-tested as failed imports rather than retained through
aliases or forwarding modules.

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
ordinary Skill instruction. A task Skill combines instructions with one run policy. The
model can inspect or activate either through the same tools it uses for every other Skill.

The Agent Runtime owns one native task queue mechanism. A selected Task Skill may expose its
tools and limits for that run. Child Agents use the same mechanism with their own child graph,
so nested producer-consumer work does not add another scheduler or copy queue code into Skills.

The configured default model owns the task loop. When other model Skills exist, Runtime
adds one `use_model` tool containing their descriptions, support, strengths, and readiness.
The default model may send one explicit subtask to one of them and receives the result as a
tool result. That call does not replace the default model for later turns, and failure is
propagated without a fallback call.

## Progressive Disclosure

`ProgressiveDisclosureCore` is the only content discovery path. Its optional recorder is a
storage port; JSONL, SQLite, and remote storage implementations live under `adapter/storage`
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
- Python source has fewer files and lines than `v0.0.114`, while file, function,
  complexity, and directory-size limits remain enforced.

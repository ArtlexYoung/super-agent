# Architecture

Super Agent uses five responsibilities:

```text
Provider provides intelligence
Runtime schedules and owns task lifecycle
Capability executes a Skill mechanism
Skill carries content and configuration
Agent composes models, Skills, storage, and subagents
```

## One Task Path

Every public run enters the same kernel:

```text
Agent.run
  -> AgentRuntime.run_task(TaskRequest)
  -> disclose task context
  -> TaskScheduler selects models, Skills, and subagents
  -> execute model and Capability steps
  -> append TaskTrace events
  -> evaluate used targets
  -> review evolution evidence
```

Model calls, tool calls, and subagent work are observable steps of the same task path. `RuntimeSession` is the only mutable context for a task. It carries one `RunIdentity`, `RuntimeStore`, Skill index, disclosure core, selected model, Capability registry, and evidence tracker.

`runtime.insights` projects UI-facing task, model, routing, freshness, and evolution views from canonical events and evaluation records. CLI and macOS consume this projection instead of implementing their own evidence logic.

`TaskScheduler` first filters model profiles by connection readiness and required features. It then scores purpose, prompt traits, default status, declared quality, latency, and cost. This produces a deterministic ordered fallback list without an extra model call. Skill selection uses the same progressive-disclosure core, while subagent selection uses the descriptions and triggers supplied by `Agent.add_subagent(...)`.

## Stable Runtime Kernel

Runtime directly owns:

- Conversation loading and persistence.
- Progressive Skill disclosure.
- Workflow interpretation and model/tool loops.
- Runtime locks and task traces.
- Evaluation recording and evolution review.

These lifecycle stages cannot be replaced by parallel controllers. Workflow Skills are passive policies containing a mode, instruction, and stopping limit.

## Executable Capabilities

`CapabilityRegistry` stores only Skill executors. Each descriptor locks the executor name, version, class, SHA-256, dependencies, permissions, update ownership, and optional source Skill.

Built-ins require no configuration. Installable and evolvable handlers are `capability` Skills and therefore use the ordinary Skill lifecycle. Code composition uses the explicit `Agent.add_skill_executor(...)` method.

## Skills and Providers

Every Skill has the stable identity `capability:name`. Prompt, MCP, memory, workflow, model, and executable Capability definitions share one index, progressive-disclosure cache, evidence stream, and evolution format.

Providers normalize protocol calls only. Model descriptions and routing traits live in model Skills, while connection instances are created lazily by `ProviderPool`.

## State and Isolation

```text
RuntimeSession -> RuntimeStore -> StorageBackend
                                  +-> JSONL (default)
                                  +-> SQLite
                                  +-> MySQL (optional)
                                  +-> PostgreSQL (optional)
```

`RunIdentity` scopes user, Agent, conversation, task, and parent task. Conversations, traces, evaluations, disclosure history, memory, habits, and evolution decisions use one canonical event schema. Derived views can always be rebuilt from those events.

## Invariants

- One task uses one Runtime session, Skill index, disclosure cache, and store.
- Runtime is the only task lifecycle and model-loop owner.
- Every Skill executor is registered once and locked by exact hash.
- Every used Skill and executor becomes an evaluation target automatically.
- Workflow is Skill data, not a second execution engine.
- Evolution cannot bypass validation, evaluation, promotion, or rollback.
- Internal compatibility shells are intentionally absent during `0.0.x`.
- Model editors persist standard model Skills; API-key values remain outside Skill and Agent configuration.

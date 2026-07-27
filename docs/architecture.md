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
  -> AdaptiveTaskLoop selects and executes models, Skills, tools, and subagents
  -> append TaskTrace events
  -> evaluate used targets
  -> review evolution evidence
```

Model calls, tool calls, and subagent work are observable steps of the same task path. `RuntimeSession` is the only mutable context for a task. It carries one `RunIdentity`, `RuntimeStore`, Skill index, disclosure core, selected model, Capability registry, and evidence tracker.

`runtime.insights` projects UI-facing task, model, routing, freshness, and evolution views from canonical events and evaluation records. CLI and macOS consume this projection instead of implementing their own evidence logic.

`AdaptiveTaskLoop` is the only task-step owner. Pure decision functions first filter model profiles by connection readiness and required features, then score purpose, prompt traits, default status, declared quality, latency, cost, and user-scoped evidence. Skill selection uses the same progressive-disclosure core, while subagent selection uses the descriptions and triggers supplied by `Agent.add_subagent(...)`.

The progressively disclosed `planner:default` Skill contributes only its planning instruction and deterministic planning thresholds. Simple tasks stay on the direct path. For a planned task, the loop requests one strict plan, validates every field, and then owns every resulting step. Each step receives a fresh model fallback order from its purpose and required features, may run one named subagent, and receives prior step results. Planner is data and policy, not a second controller or execution loop.

Task history is the ordered event stream emitted by actual schedule, model, tool, and subagent steps. Runtime does not maintain a second execution context or mutable history. The former scheduler, model-router, and execution modules are intentionally absent.

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

Every executor returns one `SkillContribution`. A contribution may contain model context, prompt context, tools, a task policy, and a completion recorder. Runtime consumes these fields uniformly and never imports concrete memory, MCP, or workflow implementations. Capability-owned tools use one `CapabilityTool` contract and enter the same traced tool registry.

Built-ins require no configuration. Installable and evolvable handlers are `capability` Skills and therefore use the ordinary Skill lifecycle. Code composition uses the explicit `Agent.add_skill_executor(...)` method.

## Skills and Providers

Every Skill has the stable identity `capability:name`. Prompt, MCP, memory, workflow, model, and executable Capability definitions share one index, progressive-disclosure cache, evidence stream, and evolution format.

Planner and model routing revisions are not privileged evolution targets. When used, both become ordinary `SkillRevision` values, receive the task's evaluation record, and enter the same recommendation, complete-directory candidate, evaluation, promotion, monitoring, and rollback state machine. Model connection fields remain user-owned unless the active model Skill explicitly grants update permission.

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

## Action Safety

`RuntimeActionExecutor` is the only model-triggered side-effect boundary. A Capability
declares effects and a resource; Runtime intersects that declaration with the Agent's
safety preset before invoking the handler. Skill text is never an authority source.
Management services reuse the same executor and write decisions to the same storage
backend. See [Runtime Safety](safety.md).

## Invariants

- One task uses one Runtime session, Skill index, disclosure cache, and store.
- Runtime is the only task lifecycle and model-loop owner.
- Every Skill executor is registered once and locked by exact hash.
- Runtime consumes only `SkillContribution`, never a Skill-kind-specific runtime object.
- One `AdaptiveTaskLoop` owns direct execution, planning, step scheduling, model fallback, and tool iteration.
- Project Skills override same-key built-in fallback Skills inside one disclosure source scan.
- Every used Skill revision, including an executor's `capability` Skill, is evaluated automatically.
- Workflow is Skill data, not a second execution engine.
- Evolution cannot bypass validation, evaluation, promotion, or rollback.
- Planner and model Skills cannot bypass the shared Skill evolution state machine.
- Internal compatibility shells are intentionally absent during `0.0.x`.
- Model editors persist standard model Skills; API-key values remain outside Skill and Agent configuration.
- Model-triggered side effects use one Runtime action contract and one safety decision stream.
- Unknown external actions cannot execute before an allow or explicit approval decision.

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

Model routing produces one ordered fallback list with explicit confidence and evidence sufficiency. Confidence combines declared compatibility with observed user-scoped outcomes. A low-confidence candidate cannot displace a sufficiently evidenced candidate silently: Runtime records either a confidence escalation or the uncertainty that leaves the provider-failure fallback chain in place.

`runtime.insights` projects UI-facing task, model, routing, freshness, and evolution views from canonical events and evaluation records. CLI and user interfaces consume this projection instead of implementing their own evidence logic. `ag_ui_bridge` maps the same live event stream to AG-UI without owning execution or state.

`AdaptiveTaskLoop` is the only task-step owner. `runtime.task_preparation` loads passive
policies and contributions, assembles tools, and builds prompt context, but never advances
a task. Pure decision functions first filter model profiles by connection readiness and
required features, then score purpose, prompt traits, default status, declared quality,
latency, cost, and user-scoped evidence. Skill selection uses the same
progressive-disclosure core, while subagent selection uses the descriptions and triggers
supplied by `Agent.add_subagent(...)`.

The progressively disclosed `planner:default` Skill contributes only its planning instruction and deterministic planning thresholds. Every task becomes one `TaskPlan`. Simple tasks receive one deterministic step without a planning model call; complex tasks request and validate a strict model-generated plan. The same step loop owns both forms. Each generated step receives a fresh model fallback order from its purpose and required features, may run one named subagent, and receives prior step results. Planner is data and policy, not a second controller or execution loop.

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

`CapabilityRegistry` stores only explicitly registered Capabilities. Each descriptor
locks the Capability name, version, class, SHA-256, and dependencies. Authorization lives
only in Runtime action effects and safety policy.

Every Capability has one `load_skill(request)` method and returns one `SkillContribution`. A contribution may contain model context, prompt context, tools, a task policy, and a completion recorder. Runtime consumes these fields uniformly and never imports concrete memory, MCP, or workflow implementations. Capability-owned tools use one `CapabilityTool` contract and enter the same traced tool registry.

Built-ins require no configuration. Custom code composition uses the explicit
`Agent.add_capability(...)` method. Runtime rejects executable Capability Skills and
never imports Python from a Skill directory.

## Skills and Providers

Every Skill has the stable identity `capability:name`. Prompt, MCP, memory, workflow,
model, and custom declarative Skills share one index, progressive-disclosure cache,
evidence stream, and evolution format.

The index resolves sources in `user > project > builtin` order. User-created, installed,
edited, and promoted Skills live below the user-and-Agent private runtime root. Project
Skills are shared read-only baselines, so one user's evolution cannot modify another
user's effective Skill or the repository copy.

Planner and model routing revisions are not privileged evolution targets. When used, both become ordinary `SkillRevision` values, receive the task's evaluation record, and enter the same recommendation, complete-directory candidate, evaluation, promotion, monitoring, and rollback state machine. Model connection fields remain user-owned unless the active model Skill explicitly grants update permission.

Providers normalize protocol calls only. Model descriptions and routing traits live in model Skills, while connection instances are created lazily by `ProviderPool`.

## State and Isolation

```text
RuntimeSession
  -> RuntimeStore
       -> RuntimeDisclosureStore -> scoped cache + disclosure events
       -> RuntimeMemoryStore     -> memory and habit events
       -> StorageBackend
            +-> JSONL (default)
            +-> SQLite
            +-> MySQL (optional)
            +-> PostgreSQL (optional)
```

`RunIdentity` scopes user, Agent, conversation, task, and parent task through one central validator. Conversations, traces, evaluations, disclosure history, memory, habits, and evolution decisions use one canonical event schema. Derived views can always be rebuilt from those events.

The focused disclosure and memory stores are domain operation boundaries, not additional
state backends. `RuntimeStore` creates both with the same user and Agent scope; disclosure
history and every memory mutation still enter the one configured `StorageBackend`.
Disclosure file reads and writes are restricted to that scope's cache root.

Model state follows the same boundary. Runtime resolves model Skills after creating the
user Store, then creates a run-scoped `AdaptiveTaskLoop` and user Provider pool. A
`UserSecretResolver` supplies a non-enumerable environment view keyed by validated user
ID and variable name. A user model overlay or Provider credential therefore cannot alter
another user's model schedule or cached connection.

## Action Safety

`RuntimeActionExecutor` is the only model-triggered side-effect boundary. A Capability
declares effects and a resource; Runtime intersects that declaration with the Agent's
safety preset before invoking the handler. Skill text is never an authority source.
Management services reuse the same executor and write decisions to the same storage
backend. See [Runtime Safety](safety.md).

Capability tools have no implicit action fallback. A Runtime-scoped `SkillLoadRequest`
is invalid without the central action executor, Registry validates every contributed
tool contract, and Runtime checks the declared action before invoking its handler.
Passive Skill files cannot supply or weaken that contract.

## Invariants

- One task uses one Runtime session, Skill index, disclosure cache, and store.
- Runtime is the only task lifecycle and model-loop owner.
- Every Capability is registered once and locked by exact hash.
- Runtime consumes only `SkillContribution`, never a Skill-kind-specific runtime object.
- One run-scoped `AdaptiveTaskLoop` owns plan creation, step scheduling, model fallback, and tool iteration.
- User Skills override project Skills, which override same-key built-in fallbacks in one disclosure source scan.
- Every used Skill revision is evaluated automatically.
- Workflow is Skill data, not a second execution engine.
- Evolution cannot bypass validation, evaluation, promotion, or rollback.
- Planner and model Skills cannot bypass the shared Skill evolution state machine.
- Internal compatibility shells are intentionally absent during `0.0.x`.
- Model editors persist standard model Skills; API-key values remain outside Skill and Agent configuration.
- Model profiles, Provider caches, and optional secret lookups are resolved per user.
- Model-triggered side effects use one Runtime action contract and one safety decision stream.
- Unknown external actions cannot execute before an allow or explicit approval decision.
- AG-UI is a transport projection over canonical events, never a second task engine or state store.

## Verification

The maintained [v0.0.61 unified proof](experiments/v0.0.61.md) exercises these
boundaries through one real Agent with two isolated users. Earlier reports are retained
only as [release snapshots](experiments/README.md).

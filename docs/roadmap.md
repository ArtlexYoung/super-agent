# Roadmap

Super Agent remains in `0.0.x` while it tests one claim: a low-configuration Agent can
represent prompts, tools, memory, workflows, planning, and model descriptions as
progressively disclosed Skills, then improve eligible Skills from real evidence.

## Completed Foundation

Versions `v0.0.25` through `v0.0.61` established the current foundation:

- One Runtime session, task loop, event stream, evaluation format, and progressive Skill
  index per run.
- JSONL by default, standard-library SQLite, and optional MySQL/PostgreSQL behind one
  storage contract.
- User-and-Agent isolation for conversations, memory, disclosure, model evidence, Skill
  overlays, and evolution state.
- Code-first subagent composition with readable cycle and depth warnings.
- Model Skills, evidence-aware scheduling, explicit Provider selection, and traceable
  decisions.
- Deterministic Skill freshness plus candidate, evaluation, promotion, monitoring, and
  rollback for Agent-owned Skills.
- Explicit action declarations, passive Skill isolation, staged memory organization,
  AG-UI, and the React Web client.

## v0.0.62: One Public Architecture

Status: implemented.

- Keep only `adapter`, `core`, and `skill` under `src`, plus the CLI and public library
  entry modules; passive shipped content lives outside Python source.
- Keep Provider implementations in `core/provider`; adapters contain only external CLI
  and AG-UI interaction.
- Use `type` and `type:name` for every Skill, and one `SkillLoader.load_skill(...)`
  contract for trusted mechanisms.
- Remove old imports, old schema fields, conversion shells, and migration aliases.
- Require an explicit model source, preserve Provider failures, and remove hidden model or
  memory fallback behavior.
- Add a lazy CopilotKit integration page over the same AG-UI endpoint.

## v0.0.63: Two Memory Lifetimes

Status: implemented.

- Bind temporary memory to one conversation and prevent cross-conversation access in
  storage, Runtime tools, recall organization, and prompt context.
- Reserve long-term memory for abstract, critical, important, stable, or habitual
  knowledge that remains available across conversations.
- Replace the generic model write tool with explicit temporary and long-term tools.
- Show both types and temporary conversation ownership in CLI, Web, events, and tests.
- Reject untyped memory streams instead of adding migration guesses or hidden fallback.

## v0.0.64: Explicit Memory Promotion

Status: implemented.

- Let long-term organization inspect relevant temporary memory from the current
  conversation without exposing another conversation.
- Add a validated `promote` operation that creates an abstract long-term item while
  preserving the temporary source.
- Record source item IDs and the source conversation in the append-only long-term event.
- Prevent repeat promotion and keep every other organization operation inside one boundary.

## v0.0.65: Task-Specific Skill Scenes

Status: implemented.

- Move shipped passive content to root-level `skill_scenes/common` and
  `skill_scenes/code`, with no old path or compatibility loader.
- Add one ordinary `scene` Skill type that references prompt, memory, planner, workflow,
  and other task-specific Skills through the central progressive index.
- Select exactly one scene by explicit request, Agent configuration, prompt triggers, or
  one default; reject every ambiguity and missing workflow.
- Provide a general task chain informed by public Hermes and OpenClaw patterns and a full
  coding chain informed by public Codex source plus Claude Code's public plugins, SDK, and
  documentation.
- Let models and Python callers create complete Agent-owned user-private scenes, without
  mutating the current run's prepared index.
- Expose scene selection through Python, CLI, stdin JSON, AG-UI, Runtime traces, tests, and
  Web configuration.

## v0.0.66: Storage-Free Progressive Disclosure

Status: implemented.

- Make the central disclosure catalog read-only by default and remove its EventStore
  dependency.
- Inject freshness, cache writes, history reads, and run events through explicit inputs.
- Keep cache paths optional and produce the same progressive index prompt without storage.
- Validate Skill directories, replacements, packages, scenes, and candidates without
  creating hidden state directories.
- Preserve cache hits and disclosure history only when a Runtime recorder is requested.

## v0.0.67: One Route Plan

Status: implemented.

- Represent scene, Skill, workflow, and model decisions in one immutable plan.
- Produce the plan once before execution and record the same object without rebuilding it.
- Reject missing, ambiguous, or incompatible choices while planning.
- Remove parallel selection result objects and adapter-specific routing logic.

## v0.0.68: Stateless Runtime

Status: implemented.

- Let the task Runtime execute with Provider, Skills, and an in-memory event sink only.
- Make conversations, memory, persistence, disclosure cache, and evaluation independent
  optional services.
- Initially keep `Agent()` stateful by default; v0.0.92 later makes the zero-storage path
  the Python library default.
- Prove the stateless path creates no files and does not silently substitute features.

## v0.0.69: Preflight and Staged Actions

Status: implemented.

- Check every planned Skill, loader, Provider, tool, and required service before the first
  model call.
- Separate side-effecting actions into explicit prepare and apply stages.
- Return all preflight problems together without partially executing the task.
- Keep reads direct and make every mutation visible in the run result and event stream.

## v0.0.70: Optional Event-Driven Learning

Status: implemented.

- Publish immutable Runtime events without requiring evaluation or evolution services.
- Let evaluation, freshness, routing evidence, and evolution subscribe independently.
- Make subscriber failures explicit while keeping the completed task result intact.
- Remove direct learning writes from the task loop.

## v0.0.71: Lean Core Release

Status: implemented.

- Move session construction and immutable Runtime resources to their single owner; delete
  duplicate data objects, storage wrappers, unused helpers, and stale version literals.
- Keep the default path dependency-free, stateful, and zero-configuration.
- Document the progressive opt-in layers from pure disclosure through full self-evolution.
- Enforce dependency, version, source-size, fresh-import, removed-path, and wheel-layout
  checks for the reduced public surface.

## v0.0.72: Progressive Imports and Assembly

Status: implemented.

- Import concrete Skill kinds only when their loader loads a selected Skill.
- Import storage, state, learning, and evolution implementations only when explicitly used.
- Keep `super_agent` focused on the everyday Runtime API and expose advanced APIs from
  their owning modules.
- Prove in a fresh process that stateless execution does not initialize optional layers.

## v0.0.73: Small Event Log and Optional Domain State

Status: implemented.

- Use one `RunEventLog` for ordered in-memory events, streaming, subscribers, persisted
  traces, and returned task events.
- Lazily initialize disclosure, memory, evaluation, and state projections from
  `EventStore` only when their operations need them.
- Keep persistent tracing independent from optional learning state.
- Prove that a persisted stateless scene can run without importing memory, evaluation, or
  evolution modules.

## v0.0.74: Pure Run Plan and One Model Decision

Status: implemented.

- Replace the mixed routing object with an immutable, serializable `Plan` and keep
  loaded policies and callbacks in one task-local `RunContext`.
- Put exactly one `ModelDecision` in each executable plan; candidate ranking remains a
  pure deterministic function and never reaches execution.
- Use the same model decision for preflight, the Runtime lock, selection events, and the
  Provider call.
- Raise Provider failures directly without retry labels, alternate candidates, or hidden
  model switching.

## v0.0.75: Explicit Code Registration and Effects

Status: implemented.

- Keep MCP Skills passive: they contain only tool guidance and an optional registered
  server name, never commands, arguments, environment, transport, or executable authority.
- Register each MCP implementation and its complete effects explicitly in Agent code.
- Reject missing registrations during preflight and check effects before a process or
  handler starts.
- Record implementation and settings hashes plus effects in the Runtime lock without
  recording environment values.

## v0.0.76: Evidence-Bound Skill Promotion

Status: implemented.

- Bind every evaluation report to normalized cases plus complete candidate and baseline
  directory hashes.
- Require every candidate case to pass, the configured minimum score to be reached, and
  every same-name case to match or exceed its baseline.
- Store one exact report ID and SHA-256 in evolution state; never select evidence by file
  recency or silently substitute an unrecorded report.
- Make manual and automatic evolution pass through the same strict report reader and
  promotion gate.

## v0.0.77: Failure-Atomic Skill Activation

Status: implemented.

- Verify the source, copied directory, and current target revision before every Skill
  promotion or rollback switch.
- Restore the previous Skill and Runtime view when activation callbacks or evolution-state
  writes fail; reject a conflicting third directory state instead of overwriting it.
- Remove reports and history snapshots that could not be bound to state, while preserving
  both the original and cleanup errors through normal exception chaining.
- Reject symlinked Skill directories and require rollback history content to match the
  source revision hash already stored in evolution state.

## v0.0.78: Explicit Non-Degradation Contract

Status: implemented.

- Select automatic scenes from service-compatible candidates before freezing the plan;
  record exclusions and reject incompatible explicit choices.
- Record the effective workflow mode and model-call limit in every Plan, and reject
  planner steps that require tools when the selected workflow does not allow them.
- Move private scene creation into an explicit storage-dependent `scene_manager` Skill
  instead of conditionally omitting a tool from the Scene loader.
- Raise requested subscriber and learning failures by default while preserving the
  completed task result; best-effort behavior is an explicit per-run choice.

## v0.0.79: One Complete Run Context

Status: implemented.

- Make `Run` the only mutable per-run context and require its disclosure core and Skill
  index at construction time.
- Let `Runtime` directly construct identity, event log, optional store, models, task
  loop, and Run from its explicit dependencies.
- Delete the resource container, session-request object, user-model wrapper, two factory
  functions, late disclosure setter, and all compatibility names.
- Keep stateful, stateless, subagent, learning, and management paths on the same Runtime
  owner without changing the public `Agent` entry point.

## v0.0.80: One Skill Loading Result

Status: implemented.

- Make every trusted loader return the same validated `LoadedSkill` result for model
  context, prompt context, tools, rules, callbacks, and included Skills.
- Replace the scene-only policy result with the generic `included_skills` field; scene
  Skills remain ordinary composition content rather than a second execution mechanism.
- Keep scene selection in central progressive disclosure and keep composition, explicit
  Agent overrides, dependency expansion, and loader checks in one Core path.
- Reject malformed and duplicate included references at the loader boundary and remove
  the old scene policy function and names without compatibility aliases.

## v0.0.81: Explicit Reads and Changes

Status: implemented.

- Make every Skill `read_*` operation pure; cache and history writes happen only through
  matching `disclose_*` operations.
- Separate model-side Skill disclosure from `activate_skill`, which alone loads loader
  output, attaches tools, records use, and contributes task-completion behavior.
- Make memory recall a pure ranked read and replace recall-time organization with an
  immutable prepare-and-apply plan that rejects stale sources.
- Disclose selected model Skills through the same center and remove old tool names,
  events, and memory settings without compatibility behavior.

## v0.0.82: One Canonical Event Path

Status: implemented.

- Make `EventStore.append_event`, `read_events`, and `delete_events` the only
  user-and-Agent-scoped access to persisted Runtime events.
- Pass the EventStore directly to memory and disclosure projections; remove callback
  protocols, duplicate backend calls, and the public backend escape hatch.
- Project one run's snapshot, ordered events, Runtime lock, selection, and disclosure path
  from one immutable event read instead of independently rereading storage.
- Keep cross-Agent run lookup inside the same user through one explicit `store_for_run`
  operation and reject ambiguous ownership.

## v0.0.83: One Explicit Learning Phase

Status: implemented.

- Make ordinary runs record immutable evidence without changing evaluation, freshness,
  routing, or Skill evolution state.
- Add explicit, idempotent `learn_from_run` operations for Python and CLI users; record
  the exact failing stage and raise instead of silently continuing.
- Give manual and automatic Skill updates one `continue_skill_evolution` path through
  candidate creation, evaluation, rejection, and promotion.
- Use deterministic run-evaluation IDs so retries reuse completed work without duplicate
  evidence or hidden fallback behavior.

## v0.0.84: Lazy Zero-Configuration Entry

Status: implemented.

- Make `Agent` construction free of storage creation, Skill scanning, model discovery, and
  Runtime assembly while keeping configuration errors immediate.
- Allow SkillLoader, MCP server, event subscriber, and subagent registration before the
  first Runtime operation.
- Build all lazy components once under a lock and publish them atomically; raise failures
  unchanged and allow a clean retry instead of caching partial initialization.
- Give every CLI command the same direct configuration, Agent, and EventStore loaders
  instead of repeating adapter assembly.

## v0.0.85: Enforced Maintenance Budgets

Status: implemented.

- Merge run identity and per-run state into `core.run`, keep actions with task execution,
  and keep secret resolution with Providers; remove the four old modules outright.
- Split long validation and Skill activation flows into direct preparation, verification,
  application, and recovery steps without changing failure-atomic behavior.
- Enforce source-file, function-length, control-flow, directory-size, fresh-import, and
  removed-module limits in release tests.
- Remove historical experiment snapshots already preserved by Git tags and shorten both
  READMEs to installation, first run, core guarantees, and links to focused documentation.

## v0.0.86: One-Way Runtime Kernel

Status: implemented.

- Make Core a Skill-independent Provider execution kernel with one immutable `ModelCall`
  input and explicit selected, completed, or failed events.
- Move task scheduling, Skill-backed run state, optional persisted state, and evolution
  ownership out of Core without leaving import aliases or compatibility modules.
- Put Agent composition in the public `super_agent` entry and user-scoped management in
  the adapter layer instead of treating either as Runtime mechanisms.
- Enforce the one-way boundary with an AST test: no Core module may import Skill code.

## v0.0.87: One Skill Center

Status: implemented.

- Add one `Skills` object that owns a verified index snapshot and the trusted loaders
  allowed to turn passive content into task behavior.
- Make every Run receive only that central object instead of independently carrying a
  disclosure core, Skill index, and loader registry that could drift apart.
- Route model discovery, task preparation, tool activation, preflight, and Runtime locks
  through the same `Skills` snapshot.
- Keep ordinary reads free of cache or history writes; recording remains an explicit
  property of the disclosure recorder attached when `Skills` is built.

## v0.0.88: Optional Event Storage

Status: implemented.

- Keep only backend-neutral event records and the storage protocol in Core; move JSONL,
  SQLite, MySQL, PostgreSQL, copying, and value encoding to the Adapter layer.
- Make stateless runs avoid importing or initializing every storage implementation while
  retaining the same ordered in-memory run events.
- Move conversation projection and changes out of task Runtime and the generic event store
  into one explicit Adapter module.
- Commit each successful user and assistant conversation turn as one event; a failed model
  run leaves no partial user message and missing storage fails before task execution.

## v0.0.89: One Skill Evolution Domain

Status: implemented.

- Remove the vague `evolution/tracking` layer; keep evidence, records, learning,
  recommendations, insights, and state directly under one evolution package.
- Group only candidate creation, evaluation, promotion, and rollback in the clear
  `evolution/change` subdomain and remove old import paths without aliases.
- Move evaluation record projection out of the generic event store so evolution explicitly
  reads and appends its own canonical records.
- Keep prepare and apply separate: candidate creation and evaluation cannot activate a
  Skill, and only an explicit checked promotion performs the atomic directory change.
- Remove the production benchmark helper; reproducible behavior remains covered by focused
  execution, evaluation, non-regression, and release-shape tests.

## v0.0.90: Checks Only When Actions Need Them

Status: implemented.

- Keep action declarations mandatory for every tool while creating the action checker only
  when a checked action or management change actually runs.
- Let stateless model-only tasks complete without constructing action rules, an action
  executor, storage, or another optional state layer.
- Allow an explicitly checker-free Runtime to execute read-only tools, but reject every
  state-changing tool and completion callback together during preflight.
- Preserve the standard checked policy for the zero-configuration Agent; external changes
  still require confirmation and never run through an implicit permissive path.

## v0.0.91: One Scheduler Skill

Status: implemented.

- Add one zero-configuration `scheduler:default` Skill and route scene, workflow, planner,
  purpose, model, and subagent choices through its central `Scheduler` mechanism.
- Keep model descriptions and Scheduler policy as ordinary inspectable, replaceable, and
  evolvable Skill content rather than fixed Provider or Runtime configuration.
- Reject model-score ties, multiple automatic purposes, conflicting single selections,
  and one-subagent policy ambiguity instead of using list or name order.
- Freeze and record every planned Step's one model decision before executing any Step, so
  evidence produced earlier in a plan cannot silently reroute its later work.
- Remove the old `routing` module and initial model preselection; no Provider is obtained
  before the Scheduler has made an unambiguous decision.

## v0.0.92: Smaller Explicit Library API

Status: implemented.

- Replace vague runtime task names with `Runtime`, `Task`, `RunResult`, `Plan`, `Step`,
  `SkillLoader`, and `EventStore`; remove old modules and aliases directly.
- Use one task-local `RunContext` from initial scheduling through every planned Step,
  instead of wrapping the same Plan and loaded mechanisms more than once.
- Make Python `Agent()` storage-free by default. Supplying `storage=` or setting
  `use_storage=True` is explicit; CLI and Web entry points explicitly enable JSONL.
- Rename the Skill loader package and documentation without forwarding modules, and keep
  preflight, Runtime locks, activation, and registration on that one vocabulary.
- Preserve all-step scheduling before execution, strict Provider selection, ordered
  in-memory events, and explicit failures when a selected feature requires storage.

## Release Gate

The project will not move to `0.1.x` because of feature count. The gate is a reproducible
proof that the single Skill lifecycle is useful, understandable, isolated across users,
and maintainable without hidden fallback paths or mandatory heavy dependencies.

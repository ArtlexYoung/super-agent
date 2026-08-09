# Roadmap

Super Agent remains below `1.0` while it tests one claim: a low-configuration Agent can
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

- Keep shipped passive content under `src/skill/builtin`, grouped by stable
  `type/name` keys, with no external package-data path or compatibility loader.
- Add one ordinary `scene` Skill type that references prompt, memory, planner, workflow,
  and other task-specific Skills through the central progressive index.
- Select exactly one scene through an explicit request or the central model route; reject
  unknown, unavailable, policy-excluded, and incomplete selections.
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
- Give manual and automatic Skill updates one recorded state machine for candidate
  creation, evaluation, rejection, and promotion.
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
- Validate the model's selected scene, Skills, purpose, execution model, and subagents
  instead of using list or name order.
- Freeze and record one execution model before running the task so evidence produced during
  a plan cannot silently reroute later work.
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

## v0.0.93: Packaged Built-in Skills

Status: implemented.

- Move shipped Skills under `src/skill/builtin`, grouped directly by their stable
  `type/name` keys instead of separate common and code directory trees.
- Remove the root-level package-data path and its source-versus-installed probing logic.
- Keep scene selection behavior unchanged while making shared scheduler and scene manager
  ownership visible in the directory layout.
- Include passive Skill manifests and content in the normal `skill` wheel package.

## v0.0.94: Per-Agent Scene Policies

Status: implemented.

- Add clear `use_only_scenes(...)`, `disable_scenes()`, and
  `select_scenes_automatically()` Python methods to each Agent.
- Keep a subagent's scene policy independent when its parent delegates work.
- Remove scene pinning from TOML and reject `scene:*` entries instead of ignoring them.
- Record scene allowlists and explicit no-scene choices in the immutable Plan and event
  stream; unavailable, ambiguous, or policy-excluded scenes fail before execution.

## v0.0.95: Optional Scenes and Workflows

Status: implemented.

- Remove the synthetic `scene:stateless`; storage-free direct execution is a Runtime mode,
  not a task domain.
- Allow a scene to compose any non-scene Skills without requiring a workflow.
- Record `scene=null` and `workflow=null` when no compatible scene or workflow is selected,
  while returning the stable public workflow label `direct` for the base model call.
- Fail explicit incompatible scene policies and tool requests without a tool-using
  workflow instead of silently changing the requested behavior.

## v0.0.96: Direct Skill Mechanism Ownership

Status: implemented.

- Remove the vague `skill/kinds` package and every old import without forwarding modules.
- Put model and workflow interpretation with loaders, memory behavior with state, scene
  and model changes with the Skill ecosystem, and planning rules with task planning.
- Merge MCP Skill settings into the MCP loader and planner settings into task planning.
- Add `SkillLoadRequest.open_skill()` as the one direct way for a loader to open its exact
  progressively disclosed reference, removing repeated open-and-type code.

## v0.0.97: One Explicit Task Context

Status: implemented.

- Build the Runtime lock directly from the active Run and immutable Plan instead of copying
  the same configuration, provider, storage, and Skill registry into another request object.
- Keep model choice only in the Plan and selected Run provider; remove its duplicate task
  context projection and validate the selected profile when it is actually applied.
- Pass background Skill contributions explicitly into task execution rather than replacing
  an immutable context with execution state.
- Let scheduling choose direct versus planned contexts from the Plan itself while preserving
  the guarantee that every planned Step is routed and recorded before any Step executes.
- Execute planned subagents in the task loop and remove one-call forwarding helpers.

## v0.0.98: Explicit Evolution Stages

Status: implemented.

- Remove the Python and CLI one-call evolution path; manual callers now create a candidate,
  evaluate it, and promote it through three visible operations.
- Keep automatic evolution inside explicit post-run learning while invoking candidate
  creation, evaluation, and promotion as separate state-machine stages.
- Guarantee that candidate creation and evaluation only write isolated artifacts and
  records; only the checked promotion stage may replace an active Skill directory.
- Move shared Skill revision values into the evolution value model and automatic stage
  scheduling into learning, deleting the old revision and service forwarding modules.
- Reuse the canonical evolution serialization in learning results instead of maintaining a
  smaller duplicate state projection.

## v0.0.99: Reproducible Skill-First Release Gate

Status: implemented.

- Add release assertions for no-scene direct execution, one selected scene, independent
  per-Agent scenes, and failures before any Provider call.
- Prove that a requested tool feature without a workflow fails explicitly and that model
  failures never retry, switch Providers, remove features, or substitute mock behavior.
- Lock the complete built-in Skill resource set into the packaged `skill` source tree and
  reject the return of removed compatibility and one-call evolution modules.
- Make the English and Chinese README files lead with the zero-configuration CLI and Python
  paths while moving internal details to focused documentation.
- Require the full Python tests, bytecode compilation, Web lint, typecheck, production
  build, and package artifact inspection before the release tag is created.

## v0.0.100: Model-Decided Routing

Status: implemented.

- Replace prompt keywords, manifest triggers, local model scoring, planning thresholds,
  subagent matching, and correction markers with one structured routing-model decision.
- Let that decision choose scene, Skills, planning, purpose, execution model, and subagents
  from progressively disclosed descriptions, model traits, and scoped evidence.
- Preserve explicit caller choices as strict constraints and fail unknown, unavailable, or
  incompatible model output without retries or default substitution.
- Record routing calls and `task.route.decided` separately from preflight-protected model
  execution, and expose no routing field in Skill, model, CLI, Web, or example manifests.

## v0.0.101: Skill-Owned Behavior

Status: implemented.

- Move workflow instructions, memory organization rules, feedback judgment, freshness and
  evolution settings, and generated-scene templates out of Python constants into Skills.
- Select scheduler, feedback, evolution, and scene-manager policies through one central
  configured-or-default Skill rule with no duplicate selection code.
- Require workflow limits, memory organization limits, and behavior instructions
  explicitly; reject old fields and incomplete Skills instead of applying hidden defaults.
- Make freshness and evolution deterministic but configurable, while keeping protocol
  schemas, validation, trusted execution, atomic persistence, and Provider wire formats in
  Python.
- Prove custom behavior reaches model calls and changes calculations or generated Skill
  directories through focused regression tests.

## v0.0.102: Explicit Model Turns

Status: implemented.

- Normalize Provider output into explicit final answers or action requests.
- Keep Provider wire differences out of the task loop and document a short source tour.

## v0.0.103: One Model Loop

Status: implemented.

- Replace route-then-execute with one model loop for direct answers and checked actions.
- Let simple prompts finish in one model call without a hidden routing call.

## v0.0.104: Ordinary Scene Groups

Status: implemented.

- Delete scheduler, planner, preflight, and preparation controllers.
- Activate scenes as ordinary Skill groups through the same progressive action path.

## v0.0.105: Conversation Context and Long-Term Memory

Status: implemented.

- Use conversation messages as the only short-term memory and persist only durable items.
- Remove temporary memory streams, promotion history, and the secondary organizer model.
- Let the main model submit explicit, validated, atomic long-term memory changes.

## v0.0.106: One Evolution Model and Metrics Path

Status: implemented.

- Replace the vague evolution `values` module with one explicit `models` owner.
- Merge evidence aggregation and freshness calculation into one `metrics` data path.
- Remove old module paths and state-machine re-exports instead of keeping import shims.

## v0.0.107: Five User Entry Points

Status: implemented.

- Export only `Agent` from the common Python module; advanced contracts use their owning
  modules.
- Keep five CLI groups: `init`, `run`, `skills`, `data`, and `serve`.
- Put models and evolution under Skills, and persisted user operations under data.
- Rewrite the user path and source tour around the active single model loop.
- Remove the redundant `Agent.learn_from_run` shortcut; user-scoped learning remains
  explicit through `agent.for_user(...).runs.learn(...)`.

## v0.0.108: One Runtime and Explicit Model Use

Status: implemented.

- Keep `core.runtime.run.Runtime` as the only task lifecycle owner and move one measured
  Provider call into `core.provider`.
- Expose configured non-default model Skills through one explicit `use_model` tool; keep
  the default model in control of later turns and propagate delegated failures unchanged.
- Replace score-shaped routing records with selected-model reasons and measured model-use
  statistics.
- Delete the old Mock routing contract, generated-scene manager, task-step projections,
  and empty Runtime-lock output instead of preserving compatibility fields.
- Keep scenes as ordinary readable Skill groups and update CLI, Web, docs, and release
  checks to the single active execution path.

## v0.0.109: Core-Owned Execution

Status: implemented.

- Move task execution decisions into one Core model loop and remove adapter-owned behavior.
- Keep every Provider call, Skill action, and failure visible in the run event stream.
- Preserve stateless execution without importing optional persistence or learning layers.

## v0.0.110: Passive Skill Layer

Status: implemented.

- Make Skill content passive data and require trusted Runtime registration for execution.
- Reject executable runner content instead of converting or loading it.
- Keep discovery, validation, and disclosure independent from optional state.

## v0.0.111: Minimal Skill Manifests

Status: implemented.

- Infer Skill name and type from clear directory structure when fields are omitted.
- Keep ownership, update authority, freshness, and Runtime evidence outside editable TOML.
- Reject unknown fields rather than maintaining old manifest conversions.

## v0.0.112: Explicit Skill Changes

Status: implemented.

- Remove the automatic evolution state machine and its recommendation and monitoring layers.
- Separate Skill proposal, testing, application, and undo into four visible operations.
- Make learning record evaluation, freshness, and model use without changing active Skills.

## v0.0.113: Small Agent Facade

Status: implemented.

- Move Agent composition into `core/runtime/agent.py` and keep `super_agent.py` as a small
  public facade.
- Reduce the common Agent actions to `run`, `for_user`, `add_subagent`, `add_skill_path`,
  `add_tool`, and `add_model`.
- Make scene selection a per-run choice and remove persistent scene policy methods.

## v0.0.114: Explainable First Run

Status: implemented.

- Add a read-only `super-agent check` command for configuration, Skills, and model readiness.
- Print the actual model, scene, workflow, Skills, stop reason, and run ID after text runs.
- Rewrite the first-run path and source tour around the current facade, Runtime, disclosure,
  Provider, and explicit Skill-change boundaries.

## v0.0.115: Explicit CLI State

Status: implemented.

- Replace `init` with `setup` and add optional model presets.
- Keep one-shot runs and ordinary interactive chat file-free unless `--save` or a
  conversation ID explicitly requests persistence.
- Catch CLI failures at one boundary, print a direct fix hint, and reserve tracebacks for
  explicit `--debug` runs.

## v0.0.116: Task Skills

Status: implemented.

- Replace task scenes and their duplicated prompt/workflow groups with one `task` Skill.
- Let a task Skill carry both progressively disclosed instructions and a Runtime run policy.
- Select a task Skill with `Agent.run(skill=...)`, `--skill`, or AG-UI `forwardedProps.skill`.
- Keep memory optional so built-in `common` and `code` tasks run without storage.
- Reject activating a second task Skill after one is explicitly selected.

## v0.0.117: Simple Skill Handlers

Status: implemented.

- Replace loader descriptors, code hashes, service declarations, and dependency graphs with
  one `SkillHandler` map keyed by Skill type.
- Keep handler registration explicit in trusted code and validate every `SkillResult` at the
  execution boundary.
- Collapse the old registry, loaded-value, and Skills wrapper modules into one handler owner.
- Remove all old loader names, imports, files, and documentation without aliases.

## v0.0.118: Task-Only Runtime

Status: implemented.

- Give Runtime one public operation, `run_task`, and move user data, feedback, learning,
  model usage, and Skill update management to their owning interfaces.
- Pass execution dependencies in one `RuntimeContext` instead of mutating Runtime after
  construction.
- Separate user-scoped storage and checked management actions into `StateAccess`.
- Keep the direct path `super_agent.Agent -> Runtime.run_task -> ModelLoop` visible in the
  source tour and release tests.

## v0.0.119: Optional Management Domains

Status: implemented.

- Keep memory, evaluation, model management, and Skill updates outside the basic Agent
  import and stateless execution graph.
- Load each management domain only when its explicit user-scoped operation is called.
- Preserve the visible Skill update stages: propose, test, apply, and undo.
- Prove in fresh processes that creating an Agent, binding a user, and running without
  state do not load optional management modules.

## v0.0.120: Progressive CLI

Status: implemented.

- Keep setup, checks, runs, Skill inspection, saved data, and serving at shallow paths.
- Group advanced Skill changes, Skill packages, and model Skills under `manage`.
- Name the four Skill update stages directly as `propose`, `test`, `apply`, and `undo`.
- Remove the old command paths without aliases or hidden forwarding behavior.

## v0.0.121: Readable Source and Examples

Status: implemented.

- Correct current documentation so task Skills, setup output, and optional boundaries
  match the source exactly.
- Replace the mixed legacy example project with minimal, custom Skill, and team examples.
- Run all three examples offline in the release tests.
- Remove stale current terminology without rewriting the historical roadmap.

## v0.0.122: Reproducible Release Gate

Status: implemented.

- Verify multi-user isolation on every dependency-free persistent backend.
- Verify stateless execution has no hidden writes or optional-domain imports.
- Verify Provider and storage failures never trigger hidden fallback behavior.
- Verify Skill updates retain separate propose, test, apply, and undo stages.
- Enforce a lower Python source file and line count than `v0.0.114` before release.

## v0.0.123: Scoped Configuration Files

Status: implemented.

- Rename shared Agent and Runtime configuration from `agent.toml` to `common.toml`.
- Require an explicit schema version and kind in every persisted configuration file.
- Define `code.toml` for coding workspace behavior without granting action authority.
- Reject cross-scope files, unknown fields, shell command strings, and old configuration
  names without compatibility readers.

## v0.0.124: CLI-Owned Configuration

Status: implemented.

- Add a strict optional `cli.toml` for terminal defaults only.
- Keep CLI, shared Runtime, coding workspace, and model settings in separate owners.
- Replace the ambiguous `--config` option with `--common-config` without an alias.
- Add read-only `config show` and `config validate` commands; never create configuration
  automatically.

## v0.0.125: Lazy Code Configuration

Status: implemented.

- Keep coding workspace settings in optional `code.toml`, with an explicit
  `SUPER_AGENT_CODE_CONFIG` or `--code-config` path.
- Attach the code settings reader only to the trusted task handler and load it when
  `task:code` is actually activated.
- Keep ordinary tasks independent from missing or invalid code configuration.
- Stop setup from generating model Skills; model ownership remains with model Skills and
  environment configuration.

## v0.0.126: Direct Terminal Entry

Status: implemented.

- Make the root command the only terminal task entry: no arguments starts a conversation,
  while a prompt runs one task.
- Put task options directly on that entry and remove the duplicate `run` command without
  an alias or forwarding path.
- Keep management commands independent from optional CLI configuration.
- Add an explicit version flag without creating files, state, or a first-run flow.

## v0.0.127: Explicit Conversation Commands

Status: implemented.

- Add `/help`, `/clear`, and `/exit` to interactive terminal conversations.
- Clear in-memory context or start a new saved conversation without deleting history.
- Report unknown slash commands instead of passing them to the model.
- Treat ordinary words such as `exit` and `quit` as user messages, not hidden controls.

## v0.0.128: Bounded Workspace Reads

Status: implemented.

- Give `task:code` UTF-8 file reading and text search tools only when its lazy code
  configuration is loaded by the terminal adapter.
- Keep reads inside the configured root and reject absolute, escaping, ignored, oversized,
  and non-text paths without truncating content.
- Return exact paths, line numbers, and explicit skipped-file errors from search.
- Remove project scaffolding from the CLI; defaults and built-in Skills already run with
  no generated files, while custom configuration remains explicit.

## v0.0.129: Explicit Workspace Actions

Status: implemented.

- Add bounded workspace creation or replacement, exact one-occurrence patching, deletion,
  and numbered verification tools to `task:code`.
- Route every code-task tool through the central `ActionRunner`; terminal adapters ask for
  explicit confirmation before non-read effects and never execute on refusal or EOF.
- Keep verification commands as validated argument arrays from `code.toml`; no model-provided
  shell command is executed, and undeclared command numbers fail visibly.
- Remove the generic runtime stdin request and streaming JSONL output paths; retain direct
  `text` and `json` output without compatibility forwarding or hidden degradation.

## v0.0.130: CLI Source Ownership

Status: implemented.

- Move CLI parsing, terminal conversations, command dispatch, and output handling into
  `adapter.cli_adapter.commands`, where external CLI behavior belongs.
- Keep `src/cli.py` as a direct source-tree entry point only and point the installed command
  directly at the adapter implementation.
- Remove the redundant result-to-dictionary helper and update tests and documentation to
  import each CLI contract from its owning module.
- Preserve the v0.0.129 command surface without aliases, forwarding commands, or runtime
  behavior changes.

## v0.0.131: Reproducible CLI Release Gate

Status: implemented.

- Add CLI usability coverage for direct source-tree version/help entry points and the
  no-project-file startup path.
- Add a read-only standard-library release script that checks synchronized versions,
  dependency-free defaults, source layout, wheel roots, README language link, and source
  size thresholds.
- Document the exact Homebrew Python 3.11, pnpm, test, build, commit, and tag commands for
  a local release without remote or hidden migration actions.
- Keep the release gate separate from Runtime behavior; failed checks stop the release
  before commit and never silently reduce the tested surface.

## v0.0.132: Bounded Central Audit

Status: implemented.

- Add one central audit classifier for detailed, critical, and protected state events.
- Store model turns, tool payloads, and subagent prompts as digests with size metadata instead
  of retaining their full content in runtime audit events.
- Add configurable `[storage.audit]` retention with 180 detailed days and 365 critical days
  by default.
- Add explicit `data storage prune` preview and `--apply` deletion across JSONL, SQLite,
  MySQL, and PostgreSQL without guessing unknown event types.
- Keep conversation, memory, habit, evaluation, and unknown streams intact and record each
  applied cleanup with a compact `audit.pruned` event.
- Keep the release gate below 83 Python source files and 17,000 lines after adding the
  central audit layer.

## v0.0.133: Dynamic Audit Redaction

Status: implemented.

- Keep canonical audit events complete so replay, learning, storage copy, and direct review
  do not lose original evidence.
- Move prompt, model text, tool payload, and error redaction into the central read view instead
  of irreversibly changing data during writes.
- Redact run status, event, explanation, export, and Web views by default.
- Require the explicit CLI `--include-sensitive` flag for complete run output; do not add an
  unredacted Web route or silently weaken the default view.
- Retain the v0.0.132 detailed and critical cleanup periods and explicit `--apply` deletion.

## v0.1.0: Reproducible Capability Baseline

Status: implemented.

- Add one dependency-free benchmark runner for structured Agent commands and task manifests.
- Isolate each Agent and task in a copied workspace without invoking shell strings.
- Record versions, manifest and output hashes, elapsed time, exit status, timeouts, and bounded
  stderr in one report while refusing to overwrite prior evidence.
- Provide an explicit SiliconFlow smoke manifest that reads the API key only from
  `OA3_SILICONFLOW_API_KEY` and never persists the secret.
- Move release versions to `0.x.y` and keep the v0.1 source budget below 90 Python files and
  20,000 lines with no default runtime dependencies.

## v0.1.1: One Progressive Content Path

Status: implemented.

- Extend `ProgressiveDisclosureCore` from Skill-only content to tool results, memory context,
  and delegated subagent results.
- Return bounded pages with stable references and explicit offsets; stateless runs keep the
  reference in memory, while stateful runs use a content-hash cache.
- Record generic `content.disclosed` events with kind, reference, digest, and cache-hit facts;
  never treat a successful tool process as proof that its content is correct.
- Reject unsupported content sizes and invalid page ranges instead of silently degrading.
- Keep the passive Skill package at five source files and preserve the default dependency-free
  install.

## v0.1.2: Checked Code Workspace

Status: implemented.

- Add bounded workspace trees, ranged UTF-8 reads, ignored-directory pruning, and fixed-argv
  Git status and diff tools to the optional code task.
- Return a SHA-256 from file reads and require it for replacement, structured patching, and
  deletion so stale model state cannot overwrite a newer file.
- Reject ambiguous, duplicate, overlapping, escaping, symbolic-link, oversized, and undeclared
  operations instead of guessing or silently reducing behavior.
- Keep code settings optional and separate in `code.toml`; zero configuration ignores common
  repository and build-noise directories without adding a runtime dependency.

## v0.1.3: Bounded Declared Processes

Status: implemented.

- Replace the blocking verification tool with explicit start, poll, and stop operations for
  argv commands declared in `code.toml`; never accept model-provided executables or shell text.
- Bound each process by time, combined output bytes, and active-process count, and terminate
  the complete process group on timeout, output overflow, or an explicit stop.
- Report running, collecting-output, completed, timed-out, output-limit, and stopped states
  with return code and decode facts instead of treating partial output as success.
- Send process results through the same progressive disclosure and action audit paths used by
  every other Skill tool.

## v0.1.4: Incremental Repository Map

Status: implemented.

- Add one read-only `refresh_repository_map` tool to the code Skill with bounded file count,
  per-file bytes, total bytes, skipped paths, and symbols.
- Return stable file paths, sizes, line counts, content hashes, parser status, and Python
  class/function/method locations without guessing symbols for unsupported file types.
- Recalculate every bounded file hash but reuse unchanged parsed summaries in the current run;
  report refreshed, reused, deleted, and skipped entries explicitly without writing an index.

## v0.1.5: Verification-Driven Repair Evidence

Status: implemented.

- Add `run_declared_check` as the bounded synchronous form of the explicit process lifecycle,
  while retaining start, poll, and stop for long-running checks.
- Return `passed` only when a check actually exits with code zero, with stdout, stderr,
  timeout, output-limit, stop, and decoding facts preserved in the result.
- Teach the code Skill to inspect failed evidence, make a separate checked change, and rerun
  the check; Runtime never performs an implicit repair or treats partial output as success.
- Test a failed-check-to-unchanged-file path so verification cannot mutate the workspace.

## v0.1.6: Explicit Checkpoints and Resume

Status: implemented.

- Record content-free checkpoints when a task is ready and after each model turn with a
  bounded facts contract, stable ID, state hash, selected Skills, workflow, and message hashes.
- Add `user.runs.list_checkpoints(run_id)` and `user.runs.resume(run_id, prompt,
  checkpoint_id=...)` with strict user and Agent scope checks.
- Start a new run for resume, record the source run and checkpoint, and disclose only the
  checkpoint metadata through the central content path; never claim to restore model output.
- Verify latest and selected checkpoint lookup, unknown IDs, user scope, and absence of model
  text in checkpoint events.

## v0.1.7: One Context Budget

Status: implemented.

- Add one per-run context budget to `ProgressiveDisclosureCore` and make model context, tool
  results, memory context, subagent results, and reference reads use the same accounting.
- Return a stable reference, hash, total size, and next offset when the budget is exhausted;
  keep explicit page reads as the only way to retrieve more content.
- Keep reference-only pages distinct from ordinary page arguments so no public paging contract
  accepts invalid zero-length pages.
- Verify budget sharing and exhaustion through Skill, tool, and memory-stage disclosures.

## v0.1.8: Evidence-Based Model Assignment

Status: implemented.

- Discover the configured free SiliconFlow OpenAI-compatible model only when
  `OA3_SILICONFLOW_API_KEY` is present; keep the secret itself out of profiles and events.
- Select a model from declared purpose and feature support, configured quality, and observed
  reliability without keyword triggers or hidden fallback behavior.
- Record the score and every selection fact in the normal model call and task events.
- Expose `AgentRunOptions.purpose` and `required_features` so callers can state a task contract
  without coupling the Runtime to a provider.

## v0.1.9: Code-Composed Specialist Agents

Status: implemented.

- Let code-created child Agents declare a plain task purpose and required features directly
  in `Agent.add_subagent(...)`; names remain optional and sequence automatically.
- Show specialist contracts to the deciding model and carry the same contract into child task
  selection, without keyword routing or a second configuration format.
- Record purpose and required features in subagent start and scheduled events so delegation
  can be inspected without retaining model text.

## v0.1.10: Isolated Git Worktrees

Status: implemented.

- Add explicit code Skill tools for create, list, and remove of detached Git worktrees.
- Restrict worktree identifiers, paths, and Git arguments; create only below
  `.super-agent/worktrees` and never accept model-provided shell commands.
- Keep removal non-forced so dirty worktrees fail visibly instead of losing changes.

## v0.1.11: Independent Review Evidence

Status: implemented.

- Add an explicit user-scoped review operation that uses a separate review-purpose model call.
- Send review evidence through the central progressive disclosure path and parse a strict report
  contract with no Markdown or implicit repair fallback.
- Store only verdicts, findings, and check names in the run audit stream; review failures record
  their type and leave the original run unchanged.

## v0.1.12: Comparative Skill Evolution

Status: implemented.

- Compare candidate and parent Skill revisions on the same cases and expose the measured
  improvement instead of only a pass/fail score.
- Allow callers to require a declared minimum improvement; a failed target blocks activation
  and leaves the active Skill untouched.
- Persist comparison facts through the central evolution record module and management audit
  event without storing model output in the report.

## v0.1.13: Public Coding Evaluation

Status: implemented.

- Upgrade the dependency-free benchmark manifest to explicit output and workspace file checks.
- Score correctness separately from process completion and publish each bounded check result in
  the immutable report.
- Reject escaping paths, symbolic links, oversized files, and non-UTF-8 artifacts instead of
  skipping them, and publish a small SiliconFlow coding benchmark starter.

## v0.1.14: Optional General Tools

Status: implemented.

- Add one explicit `attach_general_tools_to_agent(agent)` entry point backed by an ordinary
  built-in MCP Skill; default Agents remain unchanged.
- Provide bounded numeric calculation and literal text search without network, file, process,
  regular-expression, or third-party dependency access.
- Route calls through existing Skill selection, action checks, audit events, and progressive
  tool-result disclosure instead of adding a separate tool system.

## v0.1.15: Model-Planned Task Decomposition

Status: implemented.

- Let Task Skills provide `set_task_plan` and `update_task_plan_step` tools backed only by
  current-run state; no planner service or persistent task controller is introduced.
- Keep planning model-driven and optional, cap plans at 20 steps, and allow only one active
  in-progress step without prompt keyword triggers.
- Record plan counts, statuses, and evidence hashes through Runtime events while normal tool
  results continue through central progressive disclosure.

## v0.1.16: Typed Memory and Task Evolution

Status: implemented.

- Let evaluation cases declare exact expected Skill configuration in addition to model-output
  checks, with configuration read through the central Skill source path.
- Validate memory and task candidates with their real settings parsers before testing, and
  combine deterministic settings checks with candidate/baseline scores.
- Activate passing new typed Skills only in the user-private overlay, where source ownership
  marks them Agent-created and updateable; failed candidates never replace active behavior.

## v0.1.17: General and Safety Evaluation

Status: implemented.

- Add a bounded before/after workspace hash assertion for benchmark tasks that must be free of
  side effects; an unexpected path or byte change becomes a scored failure.
- Publish small general and prompt-injection benchmark starters beside the coding benchmark,
  all using the same strict dependency-free runner and non-secret environment declarations.
- Keep process success, output correctness, file artifacts, and workspace safety as separate
  report facts rather than collapsing them into one inferred success.

## v0.1.18: Remove Superseded Source

Status: implemented.

- Delete the unused parallel model-message builder after proving the Runtime loop owns the only
  active construction path.
- Remove seven empty package shells and rely on the namespace-package layout already used by
  `adapter`, without aliases or import forwarding files.
- Tighten the release budget from fewer than 90 files and 20,000 lines to fewer than 85 files
  and 19,500 lines; the release contains 82 Python files and 19,345 physical lines.

## v0.1.19: Final Reproducible Gate

Status: implemented.

- Extend the existing release verifier with one explicit `--full` path for all Python tests,
  compilation, diff validation, and a committed offline scored benchmark.
- Add explicit `--web` verification for pnpm typecheck, lint, and build; unavailable tooling or
  any failed command is reported and never skipped or replaced.
- Keep every subprocess as a fixed argv array, run the benchmark in a temporary directory, and
  publish one command in the release guide for the complete v0.1 non-regression gate.

## v0.1.20: Skill-Driven Agent Task Queues

Status: implemented.

- Add the optional `task:common-multi-producer-consumer` Skill, which mounts run-scoped
  create, dispatch, inspect, cancel, and wait tools without adding an execution-mode branch
  to Core.
- Give each subagent one serial consumer while allowing different subagents to run concurrently;
  route from declared purpose, required features, queue load, or an explicit model choice.
- Let the model request bounded sleep and wake on timeout, any completion or failure, all tasks,
  or selected task IDs without spending model calls while waiting.
- Record ordered `agent_task.*` events without prompt or result bodies, disclose queue tool
  results through the central context budget, and expose final safe task snapshots.
- Support both run-start selection and dynamic `activate_skill` mounting through the same
  Runtime tool owner, with no keyword triggers or silent fallback path.

## v0.1.21: Native Nested Queues and Deep Optimization

Status: implemented.

- Move the single Agent task queue mechanism into Runtime and remove the old Skill-mechanism
  module without an import alias or migration path.
- Rename the general Task Skill to `task:common-multi-producer-consumer` everywhere, with no
  legacy Skill directory or alternate name.
- Add `task:code-multi-deep-optimization` for global batch planning, first-level evidence
  synthesis, and second-level minimal implementation and benchmark experiments.
- Reuse each Agent's own queue and child graph at every level; different child Agents remain
  serial consumers of their own work while separate Agents and batches run concurrently.
- Prove the complete main-to-batch-to-experiment result tree with an end-to-end nested Runtime
  test instead of introducing a special deep-optimization scheduler.

## v0.1.22: Adaptive Subagent Record Compression

Status: implemented.

- Let queue Skills choose full, summary, or task-count-based adaptive child records without
  adding a fixed execution mode to Runtime.
- Keep bounded prompt and result hashes, character counts, text previews, and nested-result
  counts while dropping child model text, tool arguments, and prompts from summary events.
- Apply one central compression function to queue results and Runtime events so deep nested
  batches do not duplicate independent compression logic.
- Preserve complete results for ordinary direct `run_subagent` calls and keep the queue policy
  visible in task state, events, and the Skill configuration.
- Verify threshold switching, nested result limits, content removal, strict settings, and the
  unchanged full-record path with focused tests.
- Raise the explicit Python source budget from 20,000 to 20,500 lines for this central feature;
  keep the 85-file limit, per-file and function limits, and dependency-free default unchanged.

## v0.1.23: Rotating Deep-Optimization Agents

Status: implemented.

- Add one optional `agent_selection` queue setting with explicit `least_busy` and `rotate`
  behavior; keep the general queue default unchanged.
- Make deep optimization rotate every compatible batch and experiment task across Agent
  identities instead of returning to the first idle specialist.
- Reject fixed Agent names in rotation mode so model output cannot silently restore permanent
  task-type ownership.
- Let each rotated Agent use its own model Skills and Provider settings, making Agent rotation
  also a model and perspective rotation without moving model selection into the queue.
- Record the selection strategy, eligible Agent count, and `skill_rotation` decision in normal
  dispatch events, and prove sequential rotation with a deterministic queue test.

## v0.1.24: Weighted, Priced, Resilient Subagents

Status: implemented.

- Add a positive code-defined weight to every registered subagent, defaulting to `1`, and add
  input, output, cache-creation, and cache-read prices to model Skills.
- Prefer compatible Agents with higher weight and lower total model price while retaining queue
  load balancing; keep deep-optimization rotation across the ranked compatible set.
- Record the selected Agent, estimated model, weight, four-part pricing, score, and selection
  policy so every automatic dispatch decision remains inspectable.
- Add run-scoped Agent circuit breakers with explicit unavailable-error classification, bounded
  retries, immediate compatible fallback, cooldown, one half-open probe, and auditable state
  changes without adding Provider fallback behavior.
- Keep cache cost reporting honest: configured cache prices are recorded, while estimated call
  cost explicitly excludes cache until a Provider supplies measured cache token usage.
- Preserve the dependency-free default and all per-file and complexity limits. Add one focused
  Runtime module and raise only the aggregate Python source budget from 20,500 to 21,000 lines.

## v0.1.25: Task-Aware Agent Cost and Reliability

Status: implemented.

- Let a produced task explicitly estimate output, cache-creation, and cache-read tokens while
  deriving prompt input tokens from the same deterministic estimator used by Provider calls.
- Combine task token proportions with all four model prices instead of treating unrelated prices
  as equally used; mark omitted usage as excluded rather than inventing an output or cache amount.
- Rank compatible Agents by exact task contract, code-defined weight, run-scoped availability
  reliability, estimated blended price, and current queue load.
- Reduce reliability only for classified unavailable failures, recover it through successful
  tasks, and leave ordinary task, tool, validation, and model-output errors out of Agent health.
- Record every cost input, partial-estimate flag, health sample, and score in dispatch events, and
  classify circuit, fallback, and retry events under normal detailed audit retention.
- Keep both task queue modules below 600 lines, the complete Python source below 21,000 lines,
  and the default Runtime dependency-free.

## v0.1.26: Budgeted Group Decisions

Status: implemented.

- Let an optional Task Skill attach decision-group tools to the existing Agent queue; Runtime
  does not add another execution mode, scheduler, or default group behavior.
- Preflight member availability, distinct configured models, and estimated four-part call cost
  before creating any task. Budget failure creates no child work and remains visible as
  `budget_exceeded`.
- Send one shared task packet through central progressive disclosure and give each member only
  its role delta, context reference, and strict JSON decision contract.
- Require a configurable quorum, defaulting to two votes from three members. Member failure,
  malformed output, split votes, and failed experiments stay `inconclusive`; two independent
  negative votes are required for rejection.
- Make reduced groups opt-in and mark every reduction explicitly. Return bounded evidence to the
  parent while detailed audit records retain hashes and vote facts rather than evidence text.
- Support the same group tools at main, first-level, and second-level Agents through native nested
  queues, while preserving zero-storage execution and the dependency-free default.
- Keep fewer than 85 Python files, the complete source below 21,000 physical lines, and every
  source file and function within the existing maintenance limits.

## v0.1.27: Readable Source Layout

Status: implemented.

- Count non-empty source lines in the release gate so normal Python spacing is not treated as
  unnecessary implementation code.
- Restore readable separation between top-level declarations and class methods across `src`.
- Update the source tour to show the shortest path from `Agent.run()` to the model call and the
  owners of optional state, Skill handling, Provider calls, and external adapters.
- Keep behavior, public entry points, and the dependency-free default unchanged.

## v0.1.28: One Provider Module

Status: implemented.

- Combine Provider request formats, connection normalization, user secret isolation, and the
  per-run Provider pool in `core/provider.py`.
- Remove the nested `core/provider` directory and all old import paths without forwarding files.
- Keep model profiles and model Skill configuration outside Provider so Provider remains focused
  on making a configured model call.
- Verify Provider caching, user isolation, OpenAI-compatible requests, Anthropic-compatible
  requests, and Mock behavior through the same test suite.

## v0.1.29: One Run Lifecycle Module

Status: implemented.

- Keep `Run`, `RuntimeContext`, and `Runtime` together in `core/runtime/run.py` so one file
  explains run state, setup, execution, completion, and failure.
- Remove `core/runtime/runtime.py` and its old import path without a forwarding module.
- Delay the model-loop import at the execution boundary to preserve one-way module imports.
- Keep the public `Agent.run` behavior and stateless default unchanged.

## v0.1.30: One Skill Runtime

Status: implemented.

- Move trusted Skill handlers, models, MCP connections, defaults, and managed Skill files from
  `core/skill_use` to `skill/runtime` so Skill mechanisms have one visible domain owner.
- Merge workflow and task policy parsing into `skill/runtime/handlers.py` and remove the separate
  workflow module.
- Remove every old import path without aliases or forwarding packages.
- Keep central progressive disclosure in `skill/disclosure.py` and the task lifecycle in Core.

## v0.1.31: Unified Skill Learning

Status: implemented.

- Move evaluation records, freshness, review, run learning, and insight projection from Core to
  `skill/learning`.
- Move the explicit, tested, reversible Skill updater from `skill/runtime` to
  `skill/learning/update.py`.
- Name the post-run entry point `skill.learning.runs.learn_from_run` so its purpose is visible
  without a repeated `learning.learning` path.
- Keep every learning and update module optional: ordinary stateless execution imports none of
  them and performs no implicit evolution.

## v0.1.32: Clear Agent Owners

Status: implemented.

- Keep the six public Agent actions and per-run request assembly in `core/runtime/agent.py`.
- Move lazy storage, Provider, model, Skill-handler, and Runtime initialization into
  `core/runtime/setup.py` with all-or-nothing state assignment.
- Move subagent registration, automatic names, link warnings, model-visible descriptions, and
  child execution into `core/runtime/team.py`.
- Remove the old Agent initialization fields and private team methods instead of forwarding
  them, while keeping `Agent.run` and `Agent.add_subagent` unchanged.

## Release Gate

The project will not move to `1.0` because of feature count. The gate is reproducible proof
that the single Skill lifecycle is useful, understandable, isolated across users, and
maintainable without hidden fallback paths or mandatory heavy dependencies.

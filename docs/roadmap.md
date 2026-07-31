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

- Keep `core.runtime.runtime.Runtime` as the only task lifecycle owner and move one measured
  Provider call into `core.provider.chat`.
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

## Release Gate

The project will not move to `0.1.x` because of feature count. The gate is a reproducible
proof that the single Skill lifecycle is useful, understandable, isolated across users,
and maintainable without hidden fallback paths or mandatory heavy dependencies.

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
- Explicit action declarations, passive Skill isolation, recall-time memory organization,
  AG-UI, and the React Web client.

Reproducible snapshots remain under [experiments](experiments/README.md).

## v0.0.62: One Public Architecture

Status: implemented.

- Keep only `adapter`, `core`, and `skill` under `src`, plus the CLI and public library
  entry modules; passive shipped content lives outside Python source.
- Keep Provider implementations in `core/provider`; adapters contain only external CLI
  and AG-UI interaction.
- Use `type` and `type:name` for every Skill, and one `SkillRunner.load_skill(...)`
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

- Make the central disclosure catalog read-only by default and remove its RuntimeStore
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
- Keep `Agent()` stateful by default while exposing one explicit stateless construction.
- Prove the stateless path creates no files and does not silently substitute features.

## v0.0.69: Preflight and Staged Actions

Status: implemented.

- Check every planned Skill, runner, Provider, tool, and required service before the first
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

- Import concrete Skill kinds only when their runner loads a selected Skill.
- Import storage, state, learning, and evolution implementations only when explicitly used.
- Keep `super_agent` focused on the everyday Runtime API and expose advanced APIs from
  their owning modules.
- Prove in a fresh process that stateless execution does not initialize optional layers.

## v0.0.73: Small Event Log and Optional Domain State

Status: implemented.

- Use one `RunEventLog` for ordered in-memory events, streaming, subscribers, persisted
  traces, and returned task events.
- Lazily initialize disclosure, memory, evaluation, and state projections from
  `RuntimeStore` only when their operations need them.
- Keep persistent tracing available when run learning is explicitly disabled.
- Prove that a persisted stateless scene can run without importing memory, evaluation, or
  evolution modules.

## v0.0.74: Pure Run Plan and One Model Decision

Status: implemented.

- Replace the mixed routing object with an immutable, serializable `RunPlan` and keep
  loaded policies and callbacks in an internal `PreparedRun`.
- Put exactly one `ModelDecision` in each executable plan; candidate ranking remains a
  pure deterministic function and never reaches execution.
- Use the same model decision for preflight, the Runtime lock, selection events, and the
  Provider call.
- Raise Provider failures directly without retry labels, alternate candidates, or hidden
  model switching.

## Release Gate

The project will not move to `0.1.x` because of feature count. The gate is a reproducible
proof that the single Skill lifecycle is useful, understandable, isolated across users,
and maintainable without hidden fallback paths or mandatory heavy dependencies.

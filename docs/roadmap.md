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

- Keep only `adapter`, `builtin_skills`, `core`, and `skill` under `src`, plus the
  CLI and public library entry modules.
- Keep Provider implementations in `core/provider`; adapters contain only external CLI
  and AG-UI interaction.
- Use `type` and `type:name` for every Skill, and one `SkillRunner.load_skill(...)`
  contract for trusted mechanisms.
- Remove old imports, old schema fields, conversion shells, and migration aliases.
- Require an explicit model source, preserve Provider failures, and remove hidden model or
  memory fallback behavior.
- Add a lazy CopilotKit integration page over the same AG-UI endpoint.

## v0.0.63: Request Identity Adapter

Status: planned.

- Let server applications resolve an authenticated external identity into one validated
  Runtime user for each request.
- Keep authentication, sessions, and framework dependencies in adapters rather than Core.
- Add cross-user attack tests for every management and AG-UI route.
- Preserve the local single-user server with no added dependency or required setup.

## v0.0.64: Explicit Approval Continuation

Status: planned.

- Return blocked action details as a resumable, user-visible decision rather than asking
  adapters to repeat opaque work.
- Let applications approve one exact action ID without broadening global rules.
- Record request, decision, resumption, completion, and expiry in the canonical trace.
- Keep unattended execution opt-in through code-only action rules.

## v0.0.65: Storage at Service Scale

Status: planned.

- Add pagination and bounded projection for long conversations, run trees, and Skill
  evidence without changing storage semantics.
- Verify concurrent multi-process use and connection lifecycle on each selected backend.
- Publish deterministic copy, integrity, and recovery checks.
- Keep JSONL clean and dependency-free as the default path.

## v0.0.66: Model and Task Learning Proof

Status: planned.

- Benchmark task assignment across models with declared traits, sparse evidence, explicit
  feedback, failure, latency, and cost.
- Prove that every selected model has a visible reason and that failed calls never trigger
  hidden replacement.
- Measure routing quality separately for each user, Agent, and task purpose.
- Expose compact evidence explanations through CLI, Web, and AG-UI custom events.

## v0.0.67: Skill Evolution Proof

Status: planned.

- Run long-lived experiments for freshness, replacement signals, candidate quality,
  no-regression promotion, monitoring, and rollback.
- Compare static, eager-loading, progressive, and self-updating Agent variants.
- Publish failure cases and resource costs, not only successful demonstrations.
- Define the evidence threshold required before considering a `0.1.x` release.

## Release Gate

The project will not move to `0.1.x` because of feature count. The gate is a reproducible
proof that the single Skill lifecycle is useful, understandable, isolated across users,
and maintainable without hidden fallback paths or mandatory heavy dependencies.

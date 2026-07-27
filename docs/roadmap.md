# Roadmap

The project remains in `0.0.x` while it tests whether a fully Skill-based, automatic, self-evolving Agent runtime is practical. Each version below has one narrow proof target.

## v0.0.25: Central Runtime Lifecycle

Status: implemented.

- One `RuntimeSession` per run.
- Target-neutral evaluation records owned by Runtime.
- Skill and Capability use tracking centralized in the session.
- Freshness derived from evaluation evidence.
- One public progressive-disclosure API.

## v0.0.26: Central Storage Semantics

Status: implemented.

- One backend-neutral `StorageEvent` contract for mutable runtime state.
- One `RuntimeStore` for runs, locks, evaluations, disclosure history, memory, and habits.
- Dependency-free JSONL as the zero-configuration default.
- Run snapshots, freshness, and disclosure history derived from canonical events.
- Explicit `RunIdentity` and user/Agent storage scopes.
- Old parallel stores and compatibility shells removed.

## v0.0.27: Conversations and User Isolation

Status: implemented.

- Make conversations a first-class Runtime operation.
- Require one `RunIdentity` across main Agents and subagents.
- Isolate conversations, memory, Skill usage, freshness, and evolution by user.
- Make the macOS app read and write conversations through Runtime storage.

## v0.0.28: SQLite Storage

Status: implemented.

- Add a standard-library SQLite backend.
- Preserve the exact `StorageBackend` contract and Runtime semantics.
- Add transactions, WAL mode, concurrency tests, and storage copy commands.
- Keep JSONL as the default.

## v0.0.29: Optional SQL Storage

Status: implemented.

- Add MySQL and PostgreSQL backends as optional extras.
- Load database drivers only when their backend is selected.
- Run one shared storage contract suite against every available backend.
- Document schema setup, connection environment variables, and migration behavior.

## v0.0.30: Evolution for Every Skill Type

Status: implemented.

- Treat the complete Skill directory as the candidate unit.
- Remove prompt-only and mandatory `SKILL.md` assumptions.
- Use one candidate, evaluation, promotion, history, and rollback flow for prompt, memory, workflow, and MCP Skills.
- Keep Capability-specific validation inside explicit validators, not separate lifecycles.

## v0.0.31: Capability Registry and Version Lock

Status: implemented.

- Define Capability descriptors, versions, hashes, dependencies, and permissions.
- Register executable mechanisms through one Runtime registry.
- Lock exact Capability implementations in every run.
- Support local install, update, remove, and rollback operations.

## v0.0.32: Capability Self-Evolution

Status: implemented.

- Apply the central evolution lifecycle to Capability code.
- Validate candidates in isolation before activation.
- Evaluate real behavior, promote atomically, and roll back failures.
- Default update permission to Agent-created Capabilities.

## v0.0.33: Autonomous Evolution Scheduling

Status: implemented.

- Propose evolution from failures, scores, freshness, replacement rate, token cost, and latency.
- Require no new configuration for the default behavior.
- Store the reason, evidence, candidate difference, and decision.
- Prevent repeated optimization loops for unchanged evidence.

## v0.0.34: End-to-End Proof

Status: implemented.

- Verify the same multiuser behavior on every storage backend.
- Benchmark discovery, disclosure, execution, evaluation, evolution, and rollback.
- Compare no-Skill, eager-context, and progressive-Skill approaches.
- Publish a reproducible local experiment report.

## v0.0.35: One Skill Lifecycle

Status: implemented.

- Represent installable executable mechanisms as `capability` Skills.
- Remove independent Capability packaging, candidates, evaluation, and rollback.
- Attribute a Skill-backed Capability's runtime evidence to its source Skill.
- Remove completed benchmark orchestration from the shipped Runtime API.

## v0.0.36: Model Skills and Provider Pool

Status: implemented.

- Replace the fixed `[model]` table with standard `model` Skills.
- Keep zero-configuration environment profiles and a local mock fallback.
- Make Provider adapters lazy, reusable connection mechanisms through one pool.
- Protect user-owned model connection fields during Skill evolution.
- Put the selected model profile in each Runtime session and lock, and attribute persistent model Skill use to ordinary evaluation evidence.

## v0.0.37: One Task Kernel

Status: implemented.

- Replace Agent-run and controller contracts with one `TaskRequest` and `run_task` entry.
- Make Runtime the only model/tool loop and lifecycle owner.
- Reduce Capability registration to executable Skill handlers.
- Turn workflow implementations into passive Skill policies.
- Remove parallel controllers, replacement methods, compatibility aliases, and broad internal exports.

## v0.0.38: Automatic Task Scheduling

Status: implemented.

- Select a compatible configured model for every task and model step.
- Select matching Skills and subagents through one central scheduler.
- Use deterministic fallback when a selected model or delegate fails.
- Record the complete selection reason in the task trace.

## v0.0.39: Evidence-Learned Scheduling

Status: implemented.

- Isolate routing evidence by user, Agent, model Skill, and task purpose.
- Learn quality, latency, reliability, and cost with bounded UCB exploration.
- Improve task quality evidence with explicit feedback and implicit correction signals.
- Preserve deterministic cold-start behavior and user ownership of model connections.

## v0.0.40: Automatic Evolution Loop

Status: implemented.

- Generate candidates automatically for eligible Agent-owned Skills.
- Evaluate, promote, monitor, and roll back through the same task path.
- Keep evolution decisions deterministic and isolated by user and Agent.
- Collapse recommendation and candidate coordination into one clear service.

## v0.0.41: Task and Evolution Proof

Status: implemented.

- Show task trees, scheduler reasons, model evidence, and evolution state in macOS.
- Publish a reproducible multi-model scheduling and isolation experiment.
- Verify automatic evolution and rollback from real task evidence.
- Build and package the versioned macOS release.

Proof: [experiment description](experiments/v0.0.41.md) and [machine-readable result](experiments/v0.0.41.json).

## v0.0.42: Uniform Skill Contributions

Status: implemented.

- Replace opaque Skill runtime values with one `SkillContribution` contract.
- Let every Capability contribute context, tools, policy, and completion behavior.
- Remove concrete Memory, MCP, and Workflow imports from task execution.
- Require custom executors to declare both Skill loading and capability-wide tools.

## v0.0.43: One Adaptive Task Loop

Status: implemented.

- Collapse scheduling, model routing, execution, and tool iteration into one task loop.
- Keep model filtering and evidence scoring as pure decisions inside that loop.
- Derive task history from executed steps instead of a parallel execution context.

## v0.0.44: One Skill Revision State

Status: implemented.

- Replace duplicate evaluation, evolution target, and schedule identities with Skill revisions.
- Keep evidence, candidate status, promotion, monitoring, and rollback in one state machine.
- Remove Capability-specific evolution identities because executable mechanisms are Skills.

## v0.0.45: Zero-Configuration Planning

Status: implemented.

- Add a default Planner Skill that can decompose tasks without mandatory configuration.
- Route every planned step to a model and optional subagent from declared traits and evidence.
- Preserve a direct fast path when a task needs no planning or tools.
- Keep Planner inside the central progressive-disclosure, evaluation, and trace path.

## v0.0.46: Self-Evolving Planning Proof

Status: implemented.

- Evaluate Planner and routing Skills from ordinary task evidence.
- Improve eligible Planner and routing revisions through the same evolution loop.
- Publish a reproducible zero-configuration scheduling and evolution proof.

Proof: [experiment description](experiments/v0.0.46.md) and [machine-readable result](experiments/v0.0.46.json).

The project will not move to `0.1.x` merely because more features exist. That change should follow a reproducible demonstration that the central Skill-first lifecycle is useful and maintainable.

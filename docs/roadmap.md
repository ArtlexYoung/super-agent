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

The project will not move to `0.1.x` merely because more features exist. That change should follow a reproducible demonstration that the central Skill-first lifecycle is useful and maintainable.

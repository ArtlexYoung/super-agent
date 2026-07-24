# Roadmap

The project remains in `0.0.x` while it tests whether a fully Skill-based, automatic, self-evolving Agent runtime is practical.

## v0.0.25: Central Runtime Lifecycle

Status: implemented.

- One `RuntimeSession` per run.
- Explicit `RuntimeStatePaths`.
- Target-neutral evaluation records owned by Runtime.
- Skill and Capability use tracking centralized in the session.
- Freshness moved to a derived Skill view.
- `skill_disclosure` is the single public disclosure API.
- User-focused README and split reference documentation.

## v0.0.26: Evolution for Every Skill Type

- Treat the complete Skill directory as the candidate unit.
- Remove prompt-path and mandatory `SKILL.md` assumptions.
- Use one candidate, evaluation, promotion, history, and rollback flow for prompt, memory, workflow, and MCP Skills.
- Add Capability-specific candidate validation without duplicating the lifecycle.

## v0.0.27: Capability Registry and Version Lock

- Define Capability descriptors, versions, hashes, and dependencies.
- Register executable mechanisms through one runtime registry.
- Lock exact Capability implementations in every run snapshot.
- Support local install, update, remove, and rollback operations.

## v0.0.28: Capability Self-Evolution

- Apply the same central evolution lifecycle to Capability code.
- Validate candidates in isolation before activation.
- Evaluate real behavior, promote atomically, and roll back failures.
- Default updates to Agent-created Capabilities with explicit update permission.

## v0.0.29: Autonomous Evolution Scheduling

- Propose evolution from failures, evaluation scores, freshness, replacement rate, token cost, and latency.
- Require no new configuration for the default behavior.
- Store the reason, evidence, candidate difference, and decision.
- Prevent repeated optimization loops for the same unchanged evidence.

## v0.0.30: End-to-End Proof

- Benchmark discovery, disclosure, execution, evaluation, evolution, and rollback.
- Compare no-Skill, eager-context, and progressive-Skill approaches.
- Show Skill and Capability evolution in the macOS app.
- Publish a reproducible open-source experiment report.

The project will not move to `0.1.x` merely because more features exist. That change should follow a reproducible demonstration that the central Skill-first lifecycle is useful and maintainable.

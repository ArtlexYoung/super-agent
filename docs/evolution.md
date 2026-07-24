# Evaluation, Freshness, Memory, and Evolution

Super Agent treats runtime evidence as the basis for self-improvement.

## Canonical Evaluation Records

Online runs and candidate evaluations use one strict record format stored at:

```text
.super-agent/memory/evaluations/evaluation_records.jsonl
```

Each record separates:

- `target`: Skill or Capability identity, version, function group, and SHA-256.
- `source`: online Agent run or candidate evaluation case.
- `result`: success, score, token usage, latency, error type, and checks.

The Runtime automatically tracks every Skill and Capability that affected a run through the shared `RuntimeSession`.

Evaluation record readers reject unknown fields, unsupported target or source types, invalid scores, negative token values, malformed hashes, and unsupported schema versions.

## Skill Freshness

Freshness is a deterministic derived view. It never calls a model and can be rebuilt from canonical evaluation records.

```text
.super-agent/memory/derived/skill_stats.json
```

Signals include:

- Quality from an exponentially weighted success score.
- Recency from time since last use.
- Frequency from calls over active time.
- Efficiency from estimated token cost and observed latency.
- Reliability from success, error, and empty-output rates.
- Replacement from successful follow-up use of another Skill in the same function group.
- Confidence that pulls sparse samples toward the default score.

Candidate evaluation records never affect live freshness because freshness consumes only `agent_run` Skill records.

```bash
super-agent skills freshness --config agent.toml
```

## Self-Updating Memory

A memory Skill defines policy while runtime data remains append-only:

```toml
schema_version = 2
name = "default"
capability = "memory"
description = "Default memory behavior"
version = "0.1.0"
triggers = []

[configuration]
default_scope = "agent"
recall_limit = 20
include_in_prompt = true
include_usage_habits = true
```

Memory supports add, recall, forget, and deterministic duplicate consolidation. Successful Agent runs update workflow and Skill usage habits for later prompts.

```bash
super-agent memory habits --config agent.toml
super-agent memory list --config agent.toml --scope agent
super-agent memory add --config agent.toml --text "Prefer concise answers."
super-agent memory recall --config agent.toml --query "answer style"
super-agent memory forget --config agent.toml --item-id <memory-id>
super-agent memory consolidate --config agent.toml
```

## Skill Update Permission

```toml
agent_created = true
agent_can_update = true
```

Human-created Skills are immutable by default. When `agent_can_update` is omitted, it defaults to the value of `agent_created`.

## Evolution Lifecycle

```text
create candidate -> validate -> evaluate -> promote -> rollback
```

- Candidate files are isolated from the active Skill.
- The candidate records its parent version and directory hash.
- Evaluation calls the configured real Provider with deterministic assertions.
- Promotion requires a passing score and an unchanged active parent.
- Promotion is atomic and stores an immutable previous revision.
- Rollback restores the previous revision and records the action.

Python example:

```python
from super_agent import Agent, EvaluationCase

manager = Agent.load_from_config_file("agent.toml").create_skill_evolution_manager()
candidate = manager.create_skill_candidate("concise", "make answers clearer")
report = manager.evaluate_skill_candidate(
    candidate.candidate_id,
    [
        EvaluationCase(
            name="contains answer",
            prompt="Answer the question",
            expected_output_contains=["answer"],
        )
    ],
)

if report.passed:
    manager.promote_skill_candidate(candidate.candidate_id)
```

CLI example:

```bash
super-agent skills propose --config agent.toml --name concise --goal "make it clearer"
super-agent skills evaluate --config agent.toml --candidate-id <id> --cases cases.json
super-agent skills promote --config agent.toml --candidate-id <id>
super-agent skills rollback --config agent.toml --name concise
```

## Current Boundary

`v0.0.25` centralizes the Runtime session, evaluation records, state paths, and freshness derivation. Candidate generation is still instruction-oriented and assumes a prompt-style `SKILL.md`. `v0.0.26` will make the candidate unit the complete Skill directory so prompt, memory, workflow, and MCP Skills evolve through the same lifecycle.

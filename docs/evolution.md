# Evaluation, Freshness, Memory, and Evolution

Super Agent treats runtime evidence as the basis for self-improvement.

## Canonical Evaluation Records

Online runs and candidate evaluations use one strict record format. Records are `evaluation.recorded` entries in the selected storage backend; with default JSONL they share the user's canonical event stream:

```text
.super-agent/users/<user-hash>/events.jsonl
```

Each record separates:

- `revision`: the exact Skill identity, capability, version, function group, and SHA-256.
- `source`: online Agent run or candidate evaluation case.
- `result`: success, score, token usage, latency, error type, and checks.

The Runtime automatically tracks every Skill revision that affected a run through the shared `RuntimeSession`. Executable mechanisms are represented by their ordinary `capability` Skill revision, never by a second Capability-only identity.

Evaluation record readers reject unknown fields, unsupported source types, invalid scores, negative token values, malformed hashes, and unsupported schema versions.

## Skill Freshness

Freshness is a deterministic derived view. It never calls a model and is rebuilt from canonical evaluation records when the Skill index is prepared.

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

## Automatic Evolution Loop

After a run evaluation is stored, Runtime reviews the active Skill revisions. A revision is eligible only when it is Agent-owned, `agent_can_update` is true, and its Skill executor supports directory evolution. No additional configuration is required.

The default signals are:

- Any failed run.
- Average score below `0.75` after at least three samples.
- Skill freshness below `45` after at least two samples.
- Successful same-function replacement rate of at least `50%` after two follow-ups.
- Average estimated token usage of at least `12,000`.
- Average latency of at least `10,000 ms`.

Runtime stores the exact source revision, aggregate metrics, reason codes, source evaluation IDs, and a SHA-256 of the evidence snapshot. The evolution ID is deterministic for the Agent, revision, and evidence, so checking unchanged evidence again is idempotent. New evidence may create a new recommendation.

An eligible recommendation advances automatically through the central Skill lifecycle:

1. The central adaptive model-call path creates an isolated complete-directory candidate and records every added, modified, and deleted path.
2. Runtime evaluates the candidate against up to three prompts from the runs that triggered the recommendation. If none can be recovered, it uses the evolution goal as one fallback case.
3. A passing candidate is promoted atomically. A rejected or failed candidate stays inactive and its final status is recorded.
4. Later real runs monitor the promoted version. Any failure rolls it back; three successful samples with an average score of at least `0.75` mark it stable; a lower average rolls it back.

```bash
super-agent evolution list --config agent.toml --user-id alice
super-agent evolution show --config agent.toml --user-id alice --evolution-id <id> --output json
```

These commands are read-only views over the automatic state. The same inspection is available in Python:

```python
agent = Agent.load_from_config_file("agent.toml")
evolutions = agent.list_skill_evolutions(user_id="alice")
evolution = agent.read_skill_evolution(
    evolutions[0].evolution_id,
    user_id="alice",
)
```

Evolution events, model-call evidence, candidate workspaces, and monitoring status remain isolated by user and Agent. An automation failure is recorded as `evolution.automation_failed` in the run trace and never replaces the main task result.

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

## Central Evolution Lifecycle

```text
create candidate -> validate -> evaluate -> promote -> rollback
```

- Candidate files are isolated from active Skills and Capabilities.
- The candidate unit is a complete Skill directory, including resources.
- The model returns strict JSON with complete UTF-8 file writes and explicit deletions.
- The candidate records its parent version and directory hash.
- Runtime forces the next patch version and rejects path traversal, symlinks, identity changes, and empty changes.
- Prompt, memory, workflow, MCP, model, custom Skills, and executable `capability` Skills share one Runtime state machine and event stream.
- Skill evaluation calls the configured Provider with deterministic assertions.
- Executable `capability` Skill evaluation calls `evaluate_capability(input_data)` in a separate Python process and checks exact JSON output.
- Promotion requires a passing score and an unchanged active parent.
- Promotion is atomic and stores an immutable previous revision.
- Rollback restores the previous revision and records the action.

Python example:

```python
from super_agent import Agent, EvaluationCase

manager = Agent.load_from_config_file("agent.toml").create_skill_evolution_manager()
candidate = manager.create_skill_candidate("prompt:concise", "make answers clearer")
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
super-agent skills propose --config agent.toml --name prompt:concise --goal "make it clearer"
super-agent skills evaluate --config agent.toml --candidate-id <id> --cases cases.json
super-agent skills promote --config agent.toml --candidate-id <id>
super-agent skills rollback --config agent.toml --name prompt:concise
```

For a new Skill, omit `--capability` to create a prompt Skill or pass a Capability explicitly, such as `--capability memory`. Existing bare names resolve automatically only when unique.

Evolution workspaces and evaluation evidence remain isolated by user and Agent. Executable mechanisms are `capability` Skills and use the same cases and commands:

```bash
super-agent skills evolve \
  --config agent.toml \
  --name capability:adaptive \
  --goal "improve successful completion" \
  --cases cases.json
```

The complete Skill directory remains outside the active path until evaluation passes. Promotion verifies the original parent version and SHA-256, then remounts the Capability in the current Agent. Activation failures restore the previous Skill revision and registry.

Model Skills use the same flow. Their descriptions and routing traits can evolve normally. Runtime rejects changes to `provider`, `model`, `base_url`, `api_key_env`, or the ownership flag unless the active Skill already declares `agent_can_update_connection = true`. Promoting or rolling back a model Skill refreshes the selected profile in the current Agent.

# Evaluation, Memory, and Evolution

Super Agent updates Skills from recorded evidence rather than intuition alone. The same
user-and-Agent-scoped event stream drives evaluation, freshness, recommendations,
candidates, promotion, monitoring, and rollback.

## Evaluation Records

Every Skill revision that affects a run receives an `evaluation.recorded` event with:

- Exact `type:name`, version, function group, and directory SHA-256.
- Online run or candidate-evaluation source identity.
- Success, score, token estimates, latency, error type, and deterministic checks.

Readers reject unknown fields, invalid scores, negative metrics, malformed hashes, and
unsupported schema versions. Candidate evaluation cannot change live freshness because
freshness reads online `agent_run` evidence only.

## Freshness Without a Model

Freshness is recalculated when the central Skill index is prepared. It uses:

- Exponentially weighted quality.
- Time since the last use.
- Calls per active week.
- Input/output token and latency efficiency.
- Success, error, and empty-output reliability.
- Successful follow-up use of another Skill in the same function group.
- Sample confidence that keeps sparse evidence near the default score.

The result is deterministic and never calls a model.

```bash
super-agent skills freshness --config agent.toml
```

## Automatic Skill Updates

Only an Agent-owned Skill with `agent_can_update = true` is eligible:

```toml
agent_created = true
agent_can_update = true
```

When `agent_can_update` is omitted, it defaults to `agent_created`. Human-created Skills
are immutable by default.

After each task evaluation, Core checks failures, sustained low scores, low freshness,
same-function replacement, high token use, and high latency. Unchanged evidence produces
the same recommendation ID, so repeated review is idempotent.

An eligible update follows one state machine:

```text
recommend -> create isolated candidate -> evaluate -> promote -> monitor -> stable
                                                     -> reject
                                                     -> rollback
```

The candidate is a complete Skill directory. Validation rejects path traversal, symlinks,
identity changes, stale parents, empty changes, and invalid manifests. Promotion requires
a passing no-regression report and atomically writes a user overlay. Shared project and
built-in Skills are never overwritten.

After promotion, any failed online sample rolls the Skill back. Three successful samples
with an average score of at least `0.75` mark it stable; a lower average rolls it back.
Automation errors are recorded in the task trace and returned in `TaskResult.skill_updates`.
They do not masquerade as successful updates.

```bash
super-agent evolution list --config agent.toml --user-id alice
super-agent evolution show --config agent.toml --user-id alice --evolution-id <id> --output json
```

Manual candidate commands use the same lifecycle:

```bash
super-agent skills propose --config agent.toml --name prompt:concise --goal "make it clearer"
super-agent skills evaluate --config agent.toml --candidate-id <id> --cases cases.json
super-agent skills promote --config agent.toml --candidate-id <id>
super-agent skills rollback --config agent.toml --name prompt:concise
```

Executable SkillRunner code is never generated or activated from a candidate. Only passive
Skill content and configuration can evolve.

## Temporary and Long-Term Memory

Memory is optional. Select a memory Skill explicitly:

```toml
[agent]
skills = ["memory:default"]
```

A memory Skill defines behavior:

```toml
schema_version = 3
name = "default"
type = "memory"
description = "Default memory behavior"
version = "0.1.0"
triggers = []

[configuration]
default_scope = "agent"
recall_limit = 20
include_in_prompt = true
include_usage_habits = true
organize_on_recall = true
```

Each item has one explicit `memory_type`:

- `temporary` requires a conversation ID. Only that conversation can list, recall,
  organize, replace, or forget the item.
- `long_term` has no conversation ID. It is reserved for abstract, critical, important,
  stable, or habitual knowledge that remains useful in later conversations.

The model receives two unambiguous write tools: `add_temporary_memory` and
`add_long_term_memory`. There is no generic model-side write default and no automatic
promotion from temporary to long-term memory. Python callers use the methods with the
same names; the CLI defaults manual additions to `long-term` for convenience.

Recall ranks relevant items and organizes each type and conversation independently. It
merges exact duplicates deterministically, then can ask the selected model for validated
merge, supersede, archive, and forget operations. Replacement items must preserve the
source type, conversation, and scope. Every mutation uses the same explicit action
boundary as other Runtime changes.

Archived and forgotten items disappear from the active view while canonical events remain
append-only. Invalid or failed model organization is an explicit recall failure; Runtime
does not silently return the unorganized result.

```bash
super-agent memory list --config agent.toml --type long-term
super-agent memory add --config agent.toml --type long-term \
  --text "Prefer concise answers." --scope agent
super-agent memory add --config agent.toml --type temporary \
  --conversation-id <conversation-id> --text "This task uses Python 3.12."
super-agent memory recall --config agent.toml --type temporary \
  --conversation-id <conversation-id> --query "Python version"
super-agent memory forget --config agent.toml --item-id <memory-id>
super-agent memory consolidate --config agent.toml --type long-term
```

Successful tasks can also update workflow and Skill usage habits. All memory items, habits,
organization decisions, and update evidence remain isolated by user and Agent. Untyped
legacy memory streams are rejected explicitly rather than guessed or silently hidden.

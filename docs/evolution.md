# Evaluation, Memory, and Evolution

Super Agent updates Skills from recorded evidence rather than intuition alone. The same
user-and-Agent-scoped event stream drives evaluation, freshness, recommendations,
candidates, promotion, monitoring, and rollback.

Task execution writes immutable evidence into its terminal event but never updates learned
state. Call `user.runs.learn(run_id)` or `data runs learn` to start the explicit post-run
phase. That operation records evaluation, recalculates freshness, summarizes model use,
and reviews evolution in a fixed order.

Learning is idempotent per run and Skill revision. A completed call returns the recorded
result without writing again. A failed call records `learning.failed` with the exact stage,
raises the original error, and can be retried without duplicating evaluation records.

## Evaluation Records

Every Skill revision that affects a run receives an `evaluation.recorded` event with:

- Exact `type:name`, version, function group, and directory SHA-256.
- Online run or candidate-evaluation source identity.
- Success, score, token estimates, latency, error type, and deterministic checks.

Readers reject unknown fields, invalid scores, negative metrics, malformed hashes, and
unsupported schema versions. Candidate evaluation cannot change live freshness because
freshness reads online `agent_run` evidence only.

## Freshness Without a Model

Freshness is recalculated when the central Skill index is prepared. The selected
`evolution` Skill supplies the initial value, weights, scaling values, penalties, and
thresholds. The deterministic calculator uses:

- Exponentially weighted quality.
- Time since the last use.
- Calls per active week.
- Input/output token and latency efficiency.
- Success, error, and empty-output reliability.
- Successful follow-up use of another Skill in the same function group.
- Sample confidence that keeps sparse evidence near the default score.

The result is deterministic and never calls a model. Recommendation thresholds, evidence
limits, candidate score, automatic evaluation case count, and post-promotion monitoring
rules come from the same Skill. Missing, unknown, non-finite, or out-of-range values fail
when the policy is disclosed; Runtime does not substitute built-in constants.

```bash
super-agent skills freshness --config agent.toml
```

## Automatic Skill Updates

Update authority comes from trusted source metadata rather than editable Skill content.
Built-ins are immutable, project Skills are user-authorized, and Runtime marks a promoted
Agent-created user overlay as Agent-owned.

During each explicit run evaluation, Core checks failures, sustained low scores, low freshness,
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
a passing no-regression report and atomically writes a user overlay. The report binds the
normalized case set and the complete candidate and baseline directory hashes. Every case
must pass, the aggregate must reach the configured minimum, and no same-name case may score
below its baseline. Evolution state records the exact report ID and file hash; promotion
never substitutes a newer report. Shared project and built-in Skills are never overwritten.

Activation verifies both the expected target revision and the copied candidate before the
atomic directory switch. Runtime refresh and the promoted-state event are part of the same
compensated operation: if either fails, the previous directory and Runtime view are
restored, and the unused history snapshot is removed. A failed evaluation-state write
likewise removes its unrecorded report. Rollback applies the same rules in reverse and
requires the history content hash to match the source revision stored in evolution state.

After promotion, any failed online sample rolls the Skill back. The configured sample and
score thresholds decide when successful evidence marks it stable or rolls it back.
Automation errors are recorded as `learning.failed` and raised directly. They do not
masquerade as successful Skill updates.

```bash
super-agent data runs learn --config agent.toml --user-id alice --run-id <run-id>
super-agent skills evolution list --config agent.toml --user-id alice
super-agent skills evolution show --config agent.toml --user-id alice \
  --evolution-id <id> --output json
```

Manual candidate commands expose each lifecycle stage separately. `propose` and `evaluate`
never activate a Skill; only an explicit `promote` command can replace the active revision:

```bash
super-agent skills propose --config agent.toml --name prompt:concise --goal "make it clearer"
super-agent skills evaluate --config agent.toml --candidate-id <id> --cases cases.json
super-agent skills promote --config agent.toml --candidate-id <id>
super-agent skills rollback --config agent.toml --name prompt:concise
```

Executable SkillLoader code is never generated or activated from a candidate. Only passive
Skill content and configuration can evolve.

## Conversation Context and Long-Term Memory

Memory is optional. Select a memory Skill explicitly:

```toml
[agent]
skills = ["memory:default"]
```

A memory Skill defines behavior:

```toml
type = "memory"
description = "Default memory behavior"

[configuration]
default_scope = "agent"
recall_limit = 20
include_in_prompt = true
include_usage_habits = true
```

The current conversation is the only short-term memory. Its messages already reach the
main model, so Runtime does not copy raw conversation details into a second memory store.
Long-term items contain only text, scope, source run, and creation time. They are reserved
for abstract, critical, stable, or habitual knowledge that remains useful later.

The same main model receives five clear tools: `list_long_term_memory`,
`remember_long_term`, `recall_long_term_memory`, `organize_long_term_memory`, and
`forget_long_term_memory`. Recall is a pure ranked read. Organization accepts explicit
merge, replace, and forget operations over recalled item IDs. Runtime validates the whole
operation list before appending one atomic event; it never starts a hidden organizer model
call or applies a partial plan.

`SKILL.md` tells the main model when conversation evidence is durable enough to remember
and when recalled knowledge should be merged, replaced, or forgotten. Replacement items
cannot combine scopes. Invalid IDs, reused items, cross-scope merges, and old temporary
memory streams fail visibly.

```bash
super-agent data memory list --config agent.toml
super-agent data memory add --config agent.toml \
  --text "Prefer concise answers." --scope agent
super-agent data memory recall --config agent.toml --query "response style"
super-agent data memory forget --config agent.toml --item-id <memory-id>
```

Successful tasks can also update workflow and Skill usage habits. All memory items, habits,
organization decisions, and update evidence remain isolated by user and Agent. Old memory
streams are rejected explicitly rather than guessed, converted, or silently hidden.

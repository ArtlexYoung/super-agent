# Learning, Memory, and Skill Changes

These are three separate opt-in operations. A task run records immutable evidence but does
not learn, rewrite memory, or change a Skill after returning its answer.

## Learn From a Run

```python
result = user.run("Complete the task")
learning = user.runs.learn(result.run_id)
```

Learning is explicit and idempotent. It records evaluations for the exact Skill revisions
used by the run, recalculates deterministic freshness, and summarizes model use. Calling it
again returns the same records. It does not propose or apply a Skill change.

Evaluation records include Skill identity and content hash, success, score, token
estimates, latency, error type, and source run. Readers reject malformed values and unknown
schemas instead of guessing.

## Freshness Without a Model

Freshness is calculated from recorded evidence and does not call a model. The selected
`freshness` Skill supplies weights and thresholds. Inputs include:

- weighted result quality and reliability;
- time since use and calls per active week;
- input/output token and latency efficiency;
- successful same-function follow-up use;
- sample confidence for sparse evidence.

```bash
super-agent skills freshness --config agent.toml
```

## Change a Skill

An Agent-authorized Skill can move through four explicit operations:

```text
propose -> test -> apply -> optional undo
```

```bash
super-agent skills propose-change --name prompt:concise --goal "make it clearer"
super-agent skills test-change --change-id <id> --cases cases.json
super-agent skills apply-change --change-id <id>
super-agent skills undo-change --change-id <id>
```

`propose-change` writes a complete candidate outside active Skill roots. `test-change`
runs explicit cases against that candidate and, when present, its baseline. Neither can
activate the candidate. `apply-change` is the only activation operation and requires a
matching passing report with no regression. `undo-change` restores the prior user overlay
or removes the newly created one.

Built-in Skills are immutable. Project and user Skill update authority comes from trusted
source metadata, never editable Skill TOML. Every operation passes through action rules
and records its result. There is no background monitor, automatic application, or hidden
undo.

## Short-Term and Long-Term Memory

Conversation messages are the only short-term memory. They stay inside that conversation
and are already visible to the main model.

A selected memory Skill exposes long-term operations for durable facts, preferences,
abstractions, and habits. The main model can inspect current short-term context while
deciding what deserves long-term storage. It can explicitly list, remember, recall,
organize, and forget long-term items.

Organization validates a complete merge, replace, or forget operation before appending one
event. It does not start a hidden organizer model or apply a partial plan. Recall is a pure
ranked read. Forgetting appends an explicit tombstone rather than rewriting history.

```bash
super-agent data memory list --config agent.toml
super-agent data memory add --config agent.toml \
  --text "Prefer concise answers." --scope agent
super-agent data memory recall --config agent.toml --query "response style"
super-agent data memory forget --config agent.toml --item-id <memory-id>
```

Learning records, memory items, usage habits, and Skill overlays are isolated by trusted
user ID and Agent name.

# Native exploration protocol

Use the two registered tools after activating `research/exploration`. On another
host, supply equivalent graph tools explicitly; Skill text cannot register them.

## Creating and editing

`exploration_update` accepts `action`, `data`, `expected_version`, `request_id`.
The version starts at zero. Every successful new edit returns the new version;
an identical request ID/content is replayed without another edit. A stale version
means read, reconsider and submit a new request; never blindly retry changed work.

Create one graph per run with the user's contract:

```json
{"action":"create","expected_version":0,"request_id":"create-study","data":{"origin":"Find a correct lower-bound method for sorted integers","goals":[{"id":"correct","statement":"Return the first index whose value is at least the target","acceptance":"Independent oracle on empty, duplicate and boundary cases"}],"max_steps":6}}
```

The default host allows 24 work starts and 192 stored nodes; query host limits
with `exploration_read` before initialization. Requests may lower these limits.
IDs start with a letter and contain letters, digits, `_`, `.` or `-`; `origin`
is reserved. Goal contracts cannot be changed through tools.

| Action | Required data | Optional data |
| --- | --- | --- |
| `add_direction` | `id`, `statement`, `goal_ids`, `parents`, `rationale` | `priority` (0–100, default 50), `cost` (estimated effort 1–256, default 1), `basis` (`hypothesis` default, or `evidence`) |
| `begin` | `direction_id` | — |
| `attach_task` | `direction_id`, `task_id` | — |
| `result` | `direction_id`, `id`, `statement`, `outcome` | `supports`, `counterevidence`, `claim_kind` (`observation` default, `inference`, `hypothesis`) |
| `abandon` | `direction_id`, `reason` | — |
| `stop` | `reason` | — |

Directions target existing goals; parents are existing results or `origin`.
Multiple result parents preserve combined provenance. `begin` creates a work node
and spends one step; it executes no code. Use one direction for one attempt;
rework creates a derived direction, preserving the original. A direction finishes
with one result or is explicitly abandoned. Task attachment is immutable and
scoped; a task can be attached only once. Finish or stop attached tasks through
their actual owner before recording a result or abandoning work.

Results retain `positive`, `negative` or `inconclusive` outcome labels and exact
EvidenceLink objects:

```json
{"reference":"experiment:oracle","sha256":"<actual 64-character lowercase SHA-256>","locator":"failures[0]"}
```

The trusted host supplies current evidence observations. A result's `linked`
status means the declared relationships are current, not that the statement is
true or the outcome classification correct. `contested`, `hypothesis` and
`needs_review` retain their evidence semantics. Without a host evidence reader,
results remain `unverified`. Never invent hashes to clear a check. An evidence-based
direction inherits its result parents' dependencies; use a hypothesis direction
for independent reinvestigation of invalid or disputed material.

## Reading, replanning and stopping

`exploration_read` accepts `view` (default `overview`), `offset` (default 0),
`limit` (1–50, default 20), and `node_id` for `view=node`.

- `overview`: goals, budget spent, unmet goals, running/interrupted work, frontier,
  status and `event_token`. Goal acceptance is host-owned.
- `frontier`: ready directions by descending priority, ascending effort then ID;
  blocked directions, truthful totals and `can_begin` for global execution gates.
- `nodes` / `node`: bounded node index or detailed node with incident edges.
- `mermaid`: bounded graph view, omitted-edge count and pagination. A partial page
  is not the whole graph; use the host's full export for a complete artifact.

Compare `event_token` after new evidence or an existing task event. The token can
change even when graph version is unchanged. No graph watcher or scheduler runs
in the background. Evidence-dependent directions become blocked on invalidation;
record a distinct recheck direction rather than rewriting old evidence pins.

`needs_direction`, `budget_exhausted`, `recovery_required` and `stopped` are
incomplete states. Even `working` can have no remaining budget for new starts.
Report live work and missing acceptance. Stop preserves results and does not
cancel a TaskQueue task, process or remote operation.

## Host integration and explicit recovery

The Python host can register `ExplorationTools(scope, graph=graph).tools()` through
`Agent.add_tools_for_skills`. Construct `ExplorationGraph` with the existing
`TaskQueue`, a fresh evidence reader and a scoped identity. The host alone calls
`bind_goal(goal_id, task_id, result_ids, ...)` after defining the task's check.
Use `graph.snapshot(result_ids)` in TaskCheck.snapshot together with artifact
hashes; perform independent semantic/oracle checks in TaskCheck.check.
The host may supersede a stale goal binding with a reason; binding history stays
visible. Tools cannot create, replace or approve these acceptance bindings.

`checkpoint()` returns JSON-safe data without writing it. Save only when explicitly
enabled; `restore(checkpoint, scope=..., tasks=..., evidence=...)` checks corruption,
structure and scope, then re-observes current checks. The checksum is not
authentication. Rebind the original host checks; old accepted flags are never
trusted. Restored running work is `interrupted`. The host must inspect actual
effects and call `reconcile` with a reason before collecting results or abandoning
it. No restoration operation executes or repeats an external action.

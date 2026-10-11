---
name: "exploration"
description: "Explore uncertain research or optimization goals through a bounded graph of hypotheses, work, evidence and revised directions"
metadata:
  super-agent-categories: "[\"research/exploration\",\"planning\",\"optimization\"]"
  super-agent-type: "method"
  super-agent-version: "0.2.126"
  super-agent-requires: "[\"exploration_update\",\"exploration_read\"]"
---
# Exploration method

Use when findings can change which direction is worth pursuing: technical research,
algorithm search, offline quantitative experiments or measured optimization. A
straightforward edit can stay a single task. The graph is a research ledger;
execution, permissions and acceptance belong to the host's existing tools.

Read [references/graph-protocol.md](references/graph-protocol.md) before the first
graph update. Fix the requested goals, constraints, deliverables and acceptance
descriptions when creating the graph. Separate them from possible approaches.
Use a bounded work budget within the host's limits; model token/time limits remain
separate. If those limits are too small for the requested outcome, report it.

Create a small set of distinct directions with a hypothesis, rationale, expected
uncertainty reduction, estimated effort and priority. Anchor first directions to
`origin`; anchor later ones to the actual result IDs, including multiple parents
when combining findings. Select a ready frontier item and begin a work record
before executing through authorized host tools. Attach an existing TaskQueue task
when the host provides one; the graph never schedules, cancels or approves it.

Record positive, negative and inconclusive results with exact source/report hashes
and locations. Outcome labels are proposals; retain failed directions and
counterexamples. Investigating an unverified finding is a `hypothesis` direction;
relying on a checked result is an `evidence` direction and requires current links.
Re-read the frontier after results, failed checks, source changes or task events.
Use `event_token` to identify a changed view, rather than polling unchanged state.
Add, narrow or abandon directions based on what was learned. Ranking is a starting
point, not proof that a branch is the best use of the budget.

For measured comparisons use `skill:super-agent/research/experiments`; for domain
checks use `research/algorithms` or `research/quantitative` when relevant. Teams
are optional and require the host's ordinary delegation authorization. One
canonical graph serves the task; do not make separate ledgers per method.

An empty frontier, exhausted budget or completed model call does not establish a
goal. Only a host-bound task with a current TaskCheck and current result evidence
can mark a goal accepted. Without that binding, report findings as unverified
and include the missing checks. Stop with the reason, remaining goals and live
work; a graph stop does not stop external processes. Interrupted restored work
requires host reconciliation before further execution.

Deliver the answer or candidate, graph/lineage, retained negative results,
reproduction evidence, checks and limitations. `exploration_read` can export a
bounded Mermaid view. Full graph/checkpoint export and restoration are explicit
host operations; this method does not create persistent memory. This preset
supports general research, algorithms and offline quantitative analysis only.

---
name: "deep-optimization"
description: "Search measured optimization alternatives with a reproducible baseline, guardrails and optional independent batches"
metadata:
  super-agent-categories: "[\"code/optimization\",\"experiment\",\"competition\"]"
  super-agent-includes: "[\"skill:super-agent/code\",\"skill:super-agent/research/exploration\",\"skill:super-agent/research/experiments\"]"
  super-agent-type: "task"
  super-agent-version: "0.2.126"
---
# Measured deep optimization

Use for performance work, competitions and search problems that need measured
alternatives. Freeze the workload, reproducible baseline, evaluator, primary metric,
guardrails, allowed artifacts, budget and stopping conditions. Preserve input/output
behavior and account for preprocessing or resource costs included in the workload.

Use the included exploration graph for the global search: each direction states
a distinct hypothesis, expected value and cost; each work attempt records exact
changes, measurements and failures. Use the included experimental method for
candidate comparison. Keep one shared graph and one evaluation protocol across
batches. A promising local candidate must still beat the global baseline under
the same checks. Retain rejected and inconclusive branches and counterexamples.

Change one useful factor per attempt. Separate search data from final evaluation;
do not retune on the holdout. Replan after measurements or invalidated evidence,
not after every routine edit. Stop at the declared budget or when further gains
cannot be supported. No qualifying candidate means retain the baseline.

When independent experiments justify parallel work and the host permits delegation,
activate `skill:super-agent/team/multi-agent`. Give each batch a bounded hypothesis,
baseline, protocol, allowed paths and expected report. Use isolated worktrees or
separate workspaces for concurrent writes. Agent/model rotation is useful when
it adds independent hypotheses or checks; it is optional, as is nested delegation.
Wait on existing task events. Missing team tools do not block a single Agent's
measured search. The search owner compares batches and owns final acceptance.

Deliver the best verified candidate or unchanged baseline, metric deltas, guardrail
results, artifact/evaluator/data identities, reproduction commands, failed trials,
costs, graph and remaining directions. A model recommendation or graph result does
not pass the host's TaskCheck. Avoid claiming a speedup from operation counts alone
or a general improvement from one synthetic workload.

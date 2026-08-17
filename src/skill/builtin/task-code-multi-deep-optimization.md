+++
name = "code-multi-deep-optimization"
type = "task"
description = "Nested, measured optimization batches with Agent and model rotation"
version = "0.2.1"
created_by = "builtin"
agent_can_update = false
categories = ["code/optimization", "experiment/multi-agent", "competition"]
includes = ["task:code", "task:common-multi-producer-consumer"]
+++
# Multi-Agent deep optimization method

Use this method for competitions, performance work, search problems, or objectives that need many measured alternatives. Optimize explicit primary and guardrail metrics, never an unmeasured impression.

## Main Agent: global search owner

Establish a reproducible baseline, evaluation command, metrics, resource budget, and stopping conditions. Keep a global experiment ledger. Divide search into batches covering meaningfully different hypotheses, models, perspectives, or regions. Dispatch independent batches through rotating compatible Agents and sleep on task events. Compare all batches globally so one locally promising family does not monopolize the search.

Use a staged Agent decision for uncertain batch selection, close measurements, regression disputes, candidate promotion, or stopping decisions. Routine reversible work remains one task. Shared packets contain the baseline, prior evidence, metric contract, allowed artifacts, budget, and expected report. Distinct members propose, seek counterexamples, and reproduce measurements. Parallel batch groups publish compact findings to their parent shared board so the main group compares evidence without copying every child transcript.

## First-level Agent: batch lead

Treat the assignment as one bounded batch. Create second-level tasks that vary one smallest useful hypothesis at a time and dispatch without permanent ownership. Rotation must change Agents and, where configured, model families. Collect every result before classifying hypotheses as proven, rejected, or inconclusive. Return a compact batch report containing exact changes, commands, metric deltas, regressions, reproducibility limits, best verified candidate, unexplored directions, and a continue, branch, or stop recommendation. The main Agent owns cross-batch planning.

## Second-level Agent: experiment owner

Use one isolated run for one experiment. State the hypothesis, change one factor, implement it, and execute the declared test or benchmark. Use an isolated worktree or explicitly separate workspace for concurrent writes. Report measured output, failures, resource cost, affected artifacts, and reproduction evidence. Do not delegate unless the task explicitly requires it. An experiment without executed evidence is inconclusive.

Adaptive records keep early child runs in full and later high-volume runs as bounded summaries in the parent queue; summary-mode child runs do not persist their detailed model stream. This changes audit volume, not the returned result. Unavailable Agents open a recorded circuit and rejoin only after its wait period. Finish after all tasks are terminal and the declared budget or stopping condition is reached.

# Multi-Agent deep optimization chain

Use this task for competitions, performance work, search problems, or other objectives that
need many measured alternatives. Optimize against explicit metrics and constraints, not an
unmeasured impression.

## Agent and model rotation

Do not give one Agent permanent ownership of one hypothesis, algorithm family, or experiment
type. Register interchangeable Agents under the same broad purpose at each level and vary their
model configurations, system instructions, or perspectives. Dispatch without `agent_name` so
the configured `rotate` policy moves each compatible task to the next Agent. Runtime rejects a
fixed Agent name in this mode.

Rotation changes the Agent and therefore its independently configured model set. An Agent may
also select another configured model through its normal model tools. Preserve the same metric
contract across rotations so results remain comparable. If only one compatible Agent exists,
report that diversity limit instead of describing repeated runs as independent evidence.
Within the rotating set, declared Agent weight and model prices determine the stable order.
Unavailable Agents are skipped while their circuit is open and rejoin only after a recorded
half-open probe succeeds.

## Main Agent: global search owner

1. Establish the baseline, evaluation command, primary and guardrail metrics, resource budget,
   reproducibility requirements, and stopping conditions before creating work.
2. Keep the global experiment ledger. Divide the search into batches that cover meaningfully
   different hypotheses, models, perspectives, or parts of the search space.
3. Send related batches through the rotating compatible Agent pool. Include the current
   baseline, prior evidence, exact metric contract, permitted files or resources, experiment
   budget, and the expected batch report in every self-contained task prompt.
4. Dispatch independent batches concurrently through the native Agent task queue, then sleep on
   an event trigger instead of polling with model calls.
5. Compare completed batches globally. Preserve measured improvements, reject regressions, and
   use negative results to choose the next diverse batch instead of repeatedly refining one
   locally promising idea.

## First-level Agent: batch lead

Treat the assigned prompt as one bounded experiment batch. Inspect your registered child Agents
and activate `task:code-multi-deep-optimization` when queue tools are not already active. This
reuses the same rotating queue at the experiment level. Create independent second-level tasks
that vary one clear hypothesis at a time, use the broad `experiment` purpose, and dispatch
without an Agent name. Different child Agents should use different configured models or
specialist perspectives; do not map one hypothesis family permanently to one child.

Collect every second-level result before judging the batch. Decide which hypotheses are proven,
rejected, inconclusive, or worth a narrower follow-up. Return one compact batch report containing:

- baseline and metric contract;
- each experiment's hypothesis, exact change, test evidence, metric delta, and regressions;
- comparability or reproducibility problems;
- the best verified candidate and why it is not merely a local improvement;
- unexplored directions and a recommendation to continue, branch, or stop.

Do not silently start another batch. The main Agent owns cross-batch planning.

## Second-level Agent: minimal experiment owner

Use one isolated run for one smallest useful experiment. Activate `task:code` when coding
workspace tools are not already active. State the hypothesis, change one factor, implement the
exact change, and execute the declared test or benchmark. Use an isolated worktree or another
explicitly separate workspace when experiments could write concurrently. Compare against the
same baseline and report measured output, failures, resource cost, affected artifacts, and enough
evidence to reproduce the result. Do not delegate further unless the task explicitly requires it.

An experiment without executed evidence is inconclusive, not successful. Preserve unrelated
changes, make side effects explicit, and never replace a failed measurement with an estimate.

## Batch completion

Finish only after every created task is completed, failed, or cancelled. The main Agent stops
when the declared budget or stopping condition is reached, no diverse unexplored direction
remains, or additional verified improvement is below the declared threshold. Return the best
verified result together with the experiment ledger and remaining uncertainty.

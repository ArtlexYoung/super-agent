---
name: "experiments"
description: "Compare reproducible experimental candidates against a baseline with explicit guardrails and stopping rules"
metadata:
  super-agent-categories: "[\"research/experiments\",\"reproducibility\"]"
  super-agent-type: "method"
  super-agent-version: "0.2.125"
---
# Experimental method

Before running alternatives, freeze the hypothesis, baseline, evaluator, data
version, environment, ordered seeds/splits, primary metric and direction, minimum
improvement, guardrails, resource budget and stopping conditions. Record their
identity and hashes. Use the same protocol for baseline and candidates. Separate
exploration data from final evaluation data; do not retune on the final holdout.

Change one useful factor per experiment. Execute with host-provided, authorized
tools and retain commands, exit status, raw measurements, checks and artifact hashes.
Repeat noisy measurements; retain failed and inconclusive attempts. Do not cherry-pick
the best seed, redefine metrics after observing results, or call an unexecuted idea
an improvement. Repeated deterministic computations test reproducibility, not
statistical significance. Explain what the measurements do and do not establish.

If available, the trusted host may use `super_agent.skill.experiments` to compare
`Experiment` records under frozen `ExperimentCriteria`. Its primary metric uses
the median and guardrails use the worst observed sample; tolerances use absolute
metric units. The host must generate and verify evidence. Model-authored hashes,
metrics and boolean checks cannot authenticate themselves. This helper does not
run commands or replace the host's `TaskCheck` acceptance.

Keep a compact ledger: hypothesis, implementation/report identity, protocol/data
identity, raw samples, checks, candidate decision, failures and next question.
Reject incomparable protocols, missing evidence, failed checks and guardrail
regressions. If no candidate qualifies, retain the baseline. Promote only after
the declared checks pass, report all limitations, and stop at the agreed budget.

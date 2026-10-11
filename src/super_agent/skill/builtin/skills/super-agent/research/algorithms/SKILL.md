---
name: "algorithms"
description: "Design and evaluate algorithms with a reference oracle, invariants, counterexamples and measured complexity"
metadata:
  super-agent-categories: "[\"research/algorithms\",\"optimization\"]"
  super-agent-type: "method"
  super-agent-includes: "[\"skill:super-agent/research/exploration\",\"skill:super-agent/research/experiments\"]"
  super-agent-version: "0.2.124"
---
# Algorithm research

State the input/output contract, valid domain, constraints, numerical precision,
objective and resource limits. Identify a simple correct reference or independent
oracle before optimizing. Explain invariants and correctness reasoning separately
from empirical results; a benchmark does not prove correctness or complexity.

Build minimal, empty, boundary, duplicate, adversarial and invalid cases appropriate
to the contract. Use exhaustive small inputs, seeded differential checks and
metamorphic properties where useful. Preserve failing seeds and counterexamples.
Check approximate methods with an explicit error bound; check floating point
behavior, conditioning and overflow where applicable.

For measured search, read `skill:super-agent/research/experiments`. Compare candidates
on identical data and evaluation conditions. Distinguish operation counts, wall
time, memory and asymptotic reasoning. Include preprocessing, warm-up and data
transfer when they belong to the user's workload. Test scaling across input sizes
and distributions; do not generalize a single synthetic workload to production.

Deliver the method, proof or stated proof gap, implementation, independent checks,
reproduction commands, complexity assumptions, measured results and counterexamples.
Execution or oracle access missing means the corresponding result is unverified.
Use existing task acceptance when provided; a faster incorrect algorithm fails.

---
name: "research"
description: "Evidence-led research: questions, sources, hypotheses, counterexamples and verifiable conclusions"
metadata:
  super-agent-categories: "[\"research\",\"sources\"]"
  super-agent-type: "method"
  super-agent-includes: "[\"skill:super-agent/research/exploration\"]"
  super-agent-version: "0.2.125"
---
# Research method

Use for literature review, technical decisions, scientific questions, algorithms
and quantitative analysis. Define the question, intended deliverable, constraints,
available inputs, acceptance criteria, budget and stopping conditions. Separate
the desired outcome from the steps used to investigate it. Resolve small reversible
choices directly; ask for missing information only when it changes the result.

## Evidence and hypotheses

Separate observed facts, source statements, calculations, hypotheses and inferences.
For each important claim keep a stable ID, source identity, publication/update date
when available, retrieval time, supporting passage/location, scope and uncertainty.
Use primary sources when possible. Link a hypothesis to its supporting evidence,
contradictions and the next observation that could falsify it. Record negative and
inconclusive results so later attempts do not silently repeat failed directions.
Multiple summaries of the same source are one source, not independent confirmation.

When the host provides `super_agent.skill.evidence`, propose scoped `Claim` records
with exact `EvidenceLink` hashes and locations, retained counterevidence and claim
dependencies. The host supplies current `Evidence` observations and a freshness
policy. Its `assess_claims` report checks linkage only: `linked` does not establish
truth, `hypothesis` is unproved, `contested` retains opposition, and `needs_review`
identifies unavailable, changed, expired or unresolved prerequisites. Investigate
those causes before building further conclusions. Never remove a counterexample
or repin a new source version merely to clear a check; re-read and reassess it.

Use only tools supplied by the host. Missing source access blocks claims requiring
fresh sources; missing execution blocks measured claims. Never invent citations,
measurements or independent reviews. These methods do not register tools, grant
permissions, open connections or create persistent memory.

## Adaptive execution

Use the included exploration method when new findings can change the next useful
investigation. Its shared graph preserves goals, directions, work and results,
including negative branches; it owns frontier and stopping records. Keep small
source lookups simple. Evidence review and the research deliverable remain here.

Use `skill:super-agent/research/experiments` for measured alternatives,
`skill:super-agent/research/algorithms` for algorithm design and validation, and
`skill:super-agent/research/quantitative` for financial data and backtests. Read
these optional methods only when relevant. Use existing tasks and shared notes
when provided; a single Agent can use a compact evidence table. Delegate only
authorized independent work with explicit inputs, artifacts and checks.

## Delivery

Return the answer, claim/source table, counterevidence, executed checks, reproduction
steps, limitations and open questions. Distinguish a plausible proposal from a
verified result. Summaries retain links to originals and unresolved contradictions;
changed sources or artifacts require rechecking affected conclusions. A summary,
finished model call or candidate comparison does not itself pass task acceptance.
If the host uses TaskCheck, bind both the result artifacts and the evidence report's
snapshot. Recompute current source observations and time at review and delivery;
an immutable cached source alone cannot detect changes at its origin. Semantic
source review and independent experiment checks remain required by the host.
This research scope excludes attack and penetration-testing workflows.

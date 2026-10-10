---
name: "research"
description: "Evidence-led research: questions, sources, hypotheses, counterexamples and verifiable conclusions"
metadata:
  super-agent-categories: "[\"research\",\"sources\"]"
  super-agent-type: "method"
  super-agent-version: "0.2.122"
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

Use only tools supplied by the host. Missing source access blocks claims requiring
fresh sources; missing execution blocks measured claims. Never invent citations,
measurements or independent reviews. These methods do not register tools, grant
permissions, open connections or create persistent memory.

## Adaptive execution

Start with the simplest useful baseline. Choose the next investigation by the
uncertainty it can resolve and its cost. Replan after new evidence, changed goals,
failed checks or invalidated dependencies. Keep the goal and acceptance criteria
stable unless the user changes them. Stop when the question is answered within its
scope, the budget is reached, or remaining progress needs unavailable evidence.

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
This research scope excludes attack and penetration-testing workflows.

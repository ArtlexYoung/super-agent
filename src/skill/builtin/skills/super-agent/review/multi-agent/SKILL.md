---
name: "multi-agent"
description: "Coordinate several independent Agents to cross-check findings"
metadata:
  super-agent-categories: "[\"general/review\",\"review/multi-agent\"]"
  super-agent-includes: "[\"skill:super-agent/team/multi-agent\"]"
  super-agent-requires: "[\"dispatch_agent_tasks\"]"
  super-agent-type: "review"
  super-agent-version: "0.2.40"
---
# Multi-Agent review

This is not executor self-check. Create at least two review tasks with the same target, purpose, and required features, then dispatch them atomically. Request different models when configured diversity matters. If there are not enough compatible Agents, state that review is inconclusive instead of silently running one review.

Collect all results before synthesis. Cross-check each retained finding by reproducing its evidence, looking for counterexamples, correcting severity, and identifying duplicate claims. Use a focused decision group only for consequential disputes that remain after cross-checking.

Return findings first, ordered by impact and evidence strength. Include kind, severity, location, impact, evidence, suggested response, verification, confidence, cross-review status, failed reviewers, and remaining limitations. Review is read-only unless the caller separately authorizes changes.

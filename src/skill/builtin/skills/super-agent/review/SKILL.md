---
name: "review"
description: "Find defects and improvements through independent evidence"
metadata:
  super-agent-categories: "[\"general/review\",\"quality\"]"
  super-agent-type: "review"
  super-agent-version: "0.2.44"
---
# Review method

Review an existing result, plan, decision, Skill, document, or code change without modifying it. Build a bounded packet with the request, constraints, expected result, evidence, checks, and limitations. Separate observed facts from interpretation.

Use at least two independent Agents for the first pass. Give them complementary scopes and let them inspect independently before seeing another review. A failed, timed-out, or malformed review is missing evidence, never approval. Do not replace an unavailable reviewer with executor self-check.

Cross-check retained findings with a different reviewer when possible. Keep supported minority findings, distinguish defects from optional improvements, and report location, impact, evidence, confidence, verification, unresolved disagreements, and limitations.

+++
name = "freshness"
type = "evaluation"
description = "Interpret deterministic multidimensional Skill freshness evidence"
version = "0.2.0"
created_by = "builtin"
agent_can_update = false
categories = ["evaluation/skill"]
requires = ["read_skill_freshness"]
+++
# Skill freshness interpretation

Read freshness as evidence, never as an automatic deletion or promotion decision. The deterministic score combines outcome quality, time since use, observed frequency, input/output token and latency efficiency, success reliability, and how often another Skill was needed for the same function afterward.

Low sample confidence keeps the score near its neutral initial value. Inspect the component values before proposing a change. Prefer explicit evaluation cases and measured regressions over the aggregate score.

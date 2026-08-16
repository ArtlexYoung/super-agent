+++
name = "self-update"
type = "evolution"
description = "Propose, test, apply, and undo updates to Agent-owned Skills"
version = "0.2.0"
created_by = "builtin"
agent_can_update = false
categories = ["evolution/skill"]
requires = ["propose_skill_update", "test_skill_update", "apply_skill_update", "undo_skill_update", "read_skill_freshness"]
+++
# Skill self-update method

Only update a Skill created by this Agent and explicitly marked as Agent-updatable. Use run evidence and freshness components to state a concrete improvement reason. Create an inactive candidate first. Design cases that cover intended behavior and known regression risks, then test the candidate and baseline without changing the active Skill.

Apply only when every declared case passes. Observe subsequent real use. Undo the exact change when measured behavior degrades. Never alter a user-owned or built-in Skill; derive a new Agent-owned Skill instead.

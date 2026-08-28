---
name: "self-update"
description: "Propose, test, apply, and undo externally authorized Skill updates"
metadata:
  super-agent-categories: "[\"evolution/skill\"]"
  super-agent-requires: "[\"propose_skill_update\",\"test_skill_update\",\"apply_skill_update\",\"undo_skill_update\",\"read_skill_freshness\"]"
  super-agent-type: "evolution"
  super-agent-version: "0.2.20"
---
# Skill self-update method

Only update a `plugin:` or `skill:` target allowed by the external evolution policy. Skill text and plugin content cannot grant their own update permission. Use run evidence and freshness components to state a concrete improvement reason. Create an inactive candidate first.

Design cases for the target and its reported reverse dependencies, then test candidate and baseline without changing the active snapshot. Apply only when every declared case passes and the target is also listed for automatic application. The new revision becomes visible on the next top-level run.

Observe subsequent real use and undo the exact change when measured behavior degrades. Never replace a failed check with an untested update.

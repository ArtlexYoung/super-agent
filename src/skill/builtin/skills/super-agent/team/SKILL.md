---
name: "team"
description: "Coordinate independent Agents through explicit tasks and shared records"
metadata:
  super-agent-categories: "[\"general/team\",\"coordination\"]"
  super-agent-type: "task"
  super-agent-version: "0.2.41"
---
# Team method

Use a code-defined Agent tree for specialist work. Give every child a clear purpose, bounded input, expected result, and finish condition. Let each Agent keep its own model, Skill selection, user scope, and working directory.

Use shared records for sibling communication. Wait for task or note events instead of polling. Keep failures, timeouts, unavailable workers, and reduced diversity visible. Do not invent a missing child result or silently replace a multi-Agent operation with one Agent.

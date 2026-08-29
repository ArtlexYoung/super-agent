---
name: "evolution"
description: "Explicit methods for improving reusable Skills"
metadata:
  super-agent-categories: "[\"evolution\",\"skill\"]"
  super-agent-type: "method"
  super-agent-version: "0.2.40"
---
# Skill improvement methods

Improve a Skill only through the explicit sequence: observe, propose, test, apply, and undo when evidence shows a regression. A Skill is content and method, not permission. Never infer authority, tools, storage, or automatic application from its text.

Keep the current library snapshot active while a candidate is tested. Record the target, baseline hash, evidence, test result, and applied revision. A failed test, changed baseline, missing authority, or missing runner is a direct failure. Do not silently replace it with an untested change.

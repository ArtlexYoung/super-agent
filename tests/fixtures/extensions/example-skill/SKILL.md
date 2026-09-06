---
name: example-skill
description: A portable extension-protocol fixture with resources and capability constraints.
metadata:
  author: Super Agent
  super-agent-type: task
  super-agent-version: "0.2.92"
  super-agent-capabilities: ["read_file", "search_files"]
  super-agent-optional-capabilities: ["run_test"]
  disable-model-invocation: "false"
---

# Example Skill

This fixture uses the general extension protocol so any harness can load the same
body and resources. It demonstrates scripts, references, and assets directories.

Read `references/guide.md` for usage, then inspect `assets/data.txt`. Do not run
`scripts/run.py` unless the host has granted the `run_test` capability.

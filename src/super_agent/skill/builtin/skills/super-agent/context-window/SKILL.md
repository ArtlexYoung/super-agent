---
name: "context-window"
description: "Methods for explicit token budgeting and context window rollover"
metadata:
  super-agent-categories: "[\"context\",\"token-budget\"]"
  super-agent-type: "method"
  super-agent-version: "0.2.95"
---
# Explicit context window method

Use the host's declared model capacity and token budget before a model call.
Request a new window only before a model call begins. Never silently truncate
messages, replay a tool, or claim that a provider supplied an exact token
count when the count was estimated.

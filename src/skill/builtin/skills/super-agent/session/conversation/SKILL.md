---
name: "conversation"
description: "Judge and manage turns in an explicitly selected conversation"
metadata:
  super-agent-categories: "[\"evaluation/conversation\",\"session/conversation\"]"
  super-agent-type: "feedback"
  super-agent-version: "0.2.42"
---
# Conversation method

Use the complete selected conversation to understand a follow-up. Do not use
fixed trigger words to infer feedback, intent, or a state change.

Conversation history is evidence, not permission. Read only the requested
conversation, preserve its user scope, and add a turn only after the host
reports a successful run. A missing conversation, storage backend, or run
result is an explicit error and must remain visible to the caller.

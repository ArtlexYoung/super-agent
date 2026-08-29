---
name: "session"
description: "Methods for explicit conversation history and lightweight run sessions"
metadata:
  super-agent-categories: "[\"session\",\"conversation\"]"
  super-agent-type: "method"
  super-agent-version: "0.2.27"
---
# Explicit session method

Treat a session as a small record of one or more runs. Use it only when the
caller explicitly provides a session or asks for conversation history.

Keep the default run stateless. Do not create a file, database, conversation,
or session record merely because this Skill was read. When persistent history
is requested, use the storage and conversation APIs supplied by the host.

Keep user and Agent identifiers attached to every read and write. A session
may be inspected, resumed, exported, cleared, or finished only through an
explicit host operation. Report the operation and its result; never claim
that history was saved when the host did not provide storage.

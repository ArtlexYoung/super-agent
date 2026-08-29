---
name: "audit"
description: "Methods for compact, redacted, and time-bounded Agent run auditing"
metadata:
  super-agent-categories: "[\"audit\",\"observability\"]"
  super-agent-type: "method"
  super-agent-version: "0.2.43"
---
# Audit method

Treat runtime events as the audit facts. Record lifecycle, tool, Skill,
memory, model-use, and failure events with identifiers, status, hashes, and
small summaries. Do not store model response bodies or high-frequency text
deltas as audit data.

Use the default audit view for inspection. It dynamically redacts secrets,
prompts, tool arguments, results, and errors while retaining hashes and sizes
that support investigation. The underlying record remains unchanged and may
be read only through an explicitly authorized sensitive view.

Classify records as state, critical, or detailed. Keep state until its owner
replaces or deletes it. Preview retention cleanup first and apply deletion
only after an explicit request; never perform retention cleanup while writing
a record. Report the selected scope, retention policy, and deletion result.

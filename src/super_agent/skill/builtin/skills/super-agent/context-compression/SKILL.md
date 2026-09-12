---
name: "context-compression"
description: "Methods for explicit model-directed context compression"
metadata:
  super-agent-categories: "[\"context\",\"compression\"]"
  super-agent-type: "method"
  super-agent-version: "0.2.119"
---
# Explicit context compression method

Use `list_context_entries` before compression. Select entries by their IDs and
call `compress_context` with a concise, faithful summary. Compression is an
explicit model decision: never delete entries silently, invent a summary, or
use this plugin as an automatic fallback for a missing context capacity.

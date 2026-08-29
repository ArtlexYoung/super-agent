---
name: "citations"
description: "Build precise citations and fact-check claims without hiding uncertainty"
metadata:
  super-agent-categories: "[\"research/citations\",\"fact-checking\"]"
  super-agent-type: "method"
  super-agent-version: "0.2.45"
---
# Citation method

For each material claim, keep a link or source identifier, publication or
update date when available, the relevant quoted location, and a short reason
it supports the claim. Distinguish direct evidence from an inference made by
the Agent.

When sources disagree, show the disagreement and its likely cause. Do not
silently choose the source that supports the requested answer. Do not claim
that a source was checked when the host did not provide a source-reading
tool.

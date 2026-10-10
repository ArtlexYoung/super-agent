---
name: "citations"
description: "Build precise citations and fact-check claims without hiding uncertainty"
metadata:
  super-agent-categories: "[\"research/citations\",\"fact-checking\"]"
  super-agent-type: "method"
  super-agent-version: "0.2.123"
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

Keep the exact retrieved content version and passage location, not just a URL.
Preserve unavailable or superseded references as unresolved evidence. Check both
supporting and opposing sources under the host's freshness policy before final
delivery. A source hash verifies identity only; it does not prove that the passage
exists, supports the claim, or is independent of another citation.

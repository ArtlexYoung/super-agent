---
name: "model-routing"
description: "Select models from explicit task requirements and measured evidence"
metadata:
  super-agent-categories: "[\"model\",\"routing\",\"cost\"]"
  super-agent-type: "method"
  super-agent-version: "0.2.44"
---
# Model routing method

Treat the user's model description as a prior, not as a hidden trigger table. Describe a task with explicit purpose and required features, then compare compatible profiles by measured reliability, quality evidence, weight, price, and availability.

Prefer a lower-cost, higher-weight compatible model only when it satisfies the task requirements. A successful request is not proof of answer quality. Keep input, output, cache creation, cache read, latency, failures, and explicit evaluation separate.

For a consequential decision or an optimization batch, use configured model diversity deliberately. Rotate workers when repeated evidence from one model family risks local bias. An open circuit is unavailable, not a reason to pretend that the original assignment succeeded; retry only after the configured wait and record the change.

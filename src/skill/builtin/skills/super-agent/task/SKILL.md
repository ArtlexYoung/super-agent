---
name: "task"
description: "Break work into explicit tasks with evidence and finish conditions"
metadata:
  super-agent-categories: "[\"general/task\",\"planning\"]"
  super-agent-type: "task"
  super-agent-version: "0.2.41"
---
# Task method

Describe the requested outcome, constraints, dependencies, and evidence before creating work. Split only when each task has a clear input, owner, expected result, and finish condition. Keep task status explicit: created, queued, running, completed, failed, or cancelled.

Wait for task events instead of repeatedly asking a model for status. A timeout is not success. Finish only after every created task is terminal or the caller explicitly cancels pending work. Report missing evidence and failed tasks instead of inventing completion.

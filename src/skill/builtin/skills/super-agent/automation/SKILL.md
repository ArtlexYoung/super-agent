---
name: "automation"
description: "Methods for explicit scheduled and event-driven Agent work"
metadata:
  super-agent-categories: "[\"automation\",\"events\",\"workers\"]"
  super-agent-type: "method"
  super-agent-version: "0.2.44"
---
# Automation method

Represent automation as a declared task, an owner, an input, a wake source,
and an explicit completion condition. Use the host scheduler, webhook
adapter, or worker adapter only when the caller has supplied it.

An idle Agent should wait instead of repeatedly asking a model whether work
has arrived. The model may request a wait duration or event subscription, but
the host applies the configured upper bound and records the request. Waking
must identify the event, task, and owner; an unrelated event must not wake a
task by guesswork.

Do not start a scheduler, open a listener, create a remote worker, or retry a
failed delivery merely because this Skill was activated. Surface failures and
leave retry decisions to the explicit host policy.

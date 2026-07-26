# Architecture

Super Agent uses one small mental model:

```text
Provider provides intelligence
Runtime owns the lifecycle
Capability executes a mechanism
Skill carries content
Agent composes everything
```

## Provider

A Provider normalizes model calls. The runtime currently includes:

- A deterministic local mock provider.
- An OpenAI-compatible chat-completions adapter.
- An Anthropic-compatible messages adapter.

Providers do not discover Skills, manage memory, or decide how an Agent is composed.

## Runtime

The Runtime is the only lifecycle owner. Every run follows:

```text
discover -> disclose -> execute -> observe -> evaluate -> evolve
```

`AgentRuntime` creates one `RuntimeSession`. The session holds:

- The resolved Agent configuration and Provider.
- The Capability set selected by the Agent.
- The run identity and centralized Runtime store.
- The one Skill index prepared for that run.
- The central progressive-disclosure session.
- The Skill and Capability evaluation targets actually used.

This prevents a controller, tool router, evaluator, and runtime lock from describing different Skill trees.

## Capability

A Capability is executable mechanism code. Built-in slots include:

- Run controller.
- Skill disclosure.
- Skill executors selected by `capability` name.
- Run result evaluator.
- Skill updater.

Capabilities receive explicit requests or the shared `RuntimeSession`. They do not infer sibling directories from a path and do not own a second runtime lifecycle.

One `CapabilityRegistry` is the only executable mechanism registry. Each descriptor fixes the slot, name, version, implementation class, content SHA-256, dependencies, permissions, and update ownership. Runtime locks those descriptors and evaluation reuses them instead of recalculating a parallel identity. Capability candidates use the same Runtime evolution state machine as Skills, but execute in a separate process until promotion.

## Skill

A Skill is passive, versioned content. Its manifest declares:

- Stable identity as `capability:name`.
- Description and triggers.
- Optional instructions.
- Generic Capability configuration.
- Dependencies and provided functions.
- Agent creation and update permissions.

Prompt, MCP, memory, and workflow are built-in Capability names rather than separate storage systems.

## Agent

An Agent combines one Provider, one Capability set, Skill roots, and optional subagents. Configuration describes one Agent; Python code describes relationships between Agents.

Clear replacement methods include:

```python
agent.set_run_controller(controller)
agent.set_skill_disclosure(disclosure_capability)
agent.set_run_result_evaluator(evaluator)
agent.set_skill_updater(updater)
agent.add_skill_executor(executor)
agent.add_subagent(other_agent)
```

## Dependency Direction

The intended dependency direction is:

```text
Agent -> Runtime -> Capability contracts
  |         |             |
  |         +-> Provider  +-> Skill data
  +-> concrete default Capabilities
```

Generic evaluation records belong to `runtime.evaluation`, not to Skill evolution. Skill-specific code converts a Skill into a target-neutral evaluation identity.

## Runtime State

`RuntimeStore` is the only semantic state API. It operates over a replaceable `StorageBackend`:

```text
RuntimeSession -> RuntimeStore -> StorageBackend
                                  +-> JSONL (default)
                                  +-> SQLite (standard library)
                                  +-> MySQL (optional driver)
                                  +-> PostgreSQL (optional driver)
```

Conversations, runs, evaluations, memory, habits, and disclosure history use one canonical event schema. A backend only appends, queries, and deletes `StorageEvent` records; it does not implement domain behavior. Conversation state, run snapshots, and freshness are derived views, not parallel sources of truth.

## Core Invariants

- One `RuntimeSession` exists for one Agent run.
- One `RunIdentity` scopes user, Agent, conversation, run, and parent run.
- One `RuntimeStore` owns all mutable runtime semantics.
- One central Skill index is prepared per run.
- Every disclosure uses the same cache and history store.
- Every used Skill and Capability becomes an evaluation target automatically.
- Every executable Capability comes from the central registry and is locked by exact hash.
- Canonical evaluation records are append-only.
- Freshness data is rebuildable and never the source of truth.
- Evolution cannot bypass candidate validation and evaluation.
- Internal compatibility shells are intentionally absent during `0.0.x`.

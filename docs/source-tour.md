# Source Tour

Follow one ordinary call through these files:

1. `src/super_agent.py`: `Agent.run()` composes the optional dependencies.
2. `src/skill/task/runtime.py`: `Runtime.run_task()` owns one run and its visible events.
3. `src/skill/task/loop.py`: `ModelLoop` gives the model the Skill index and checked tools.
4. `src/skill/disclosure/core.py`: the shared index, disclosure, and cache path.
5. `src/core/provider/chat.py`: Provider implementations return one `ModelResponse`.

The active execution path is:

```text
Agent.run
  -> Runtime.run_task
  -> ModelLoop.run_task
  -> Provider.complete
  -> final text or checked tool calls
```

A simple task can finish on its first model turn. Storage, memory, conversations, safety
checks, subagents, learning, and evolution are attached only when requested or selected.

The model receives descriptions, not a trigger-word decision made by Python. It can open
more Skill content, activate a Skill, call an exposed Skill tool, recall or update
long-term memory, or delegate to a registered subagent. Every side effect goes through an
explicit action request.

For a subsystem, start at its one public owner:

- Skill discovery: `skill.skills.Skills`.
- Skill disclosure: `skill.disclosure.ProgressiveDisclosureCore`.
- Provider connections: `core.provider.pool.ProviderPool`.
- Stored user access: `adapter.user.UserAgent`.
- Action checks: `core.checks.ActionRunner`.
- Run events: `core.state.event_log.RunEventLog`.

There are no compatibility modules. Import an advanced type from the file that owns it.

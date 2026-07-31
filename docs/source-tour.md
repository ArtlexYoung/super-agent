# Source Tour

Follow one ordinary call through these files:

1. `src/super_agent.py`: `Agent.run()` composes the optional dependencies.
2. `src/core/runtime/runtime.py`: `Runtime.run_task()` owns one run and its visible events.
3. `src/core/runtime/loop.py`: `ModelLoop` gives the model the Skill index and checked tools.
4. `src/core/runtime/model_calls.py`: `ModelCalls` resolves a configured model and records use.
5. `src/core/provider/chat.py`: `call_chat_model()` measures one Provider call.
6. `src/skill/disclosure.py`: the shared index, disclosure, and cache path.

The active execution path is:

```text
Agent.run
  -> Runtime.run_task
  -> ModelLoop.run_task
  -> ModelCalls.call_model
  -> call_chat_model
  -> ChatProvider.send_chat_messages[_with_tools]
  -> final text or checked tool calls
```

A simple task can finish on its first model turn. Storage, memory, conversations, safety
checks, subagents, learning, and evolution are attached only when requested or selected.

The model receives descriptions, not a trigger-word decision made by Python. It can open
more Skill content, activate a Skill, call an exposed Skill tool, recall or update
long-term memory, delegate to a registered subagent, or give one subtask to another
configured model. Every side effect goes through an explicit action request.

For a subsystem, start at its one public owner:

- Skill discovery: `core.skill_use.skills.Skills`.
- Skill disclosure: `skill.disclosure.ProgressiveDisclosureCore`.
- Provider connections: `core.provider.pool.ProviderPool`.
- Stored user access: `adapter.user.UserAgent`.
- Action checks: `core.checks.ActionRunner`.
- Run events: `core.state.event_log.RunEventLog`.

There are no compatibility modules. Import an advanced type from the file that owns it.

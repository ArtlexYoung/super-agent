# Source Tour

Read one ordinary call in this order:

1. `src/super_agent.py` exports the small public `Agent` facade.
2. `src/core/runtime/agent.py` composes configuration, optional state, Skills, Providers,
   and subagents.
3. `src/core/runtime/run.py` owns one run identity, event log, result, and failure.
4. `src/core/runtime/loop.py` gives the model selected context and checked tools.
5. `src/skill/disclosure.py` builds the shared Skill index and opens requested content.
6. `src/core/provider.py` makes and measures the selected Provider call.
7. Checkpoints are recorded by `src/core/runtime/run.py` as content-free recovery facts.

The short execution path is:

```text
super_agent.Agent
  -> Agent.run
  -> Runtime.run_task
  -> ModelLoop.run_task
  -> Skill disclosure and activation
  -> Provider call
  -> final text or checked tool actions
```

There is one task loop. Python does not route with trigger words, run a separate planner,
or start hidden fallback engines. The model sees descriptions, opens useful Skill content,
and either returns text or requests a registered action.

A stateless run imports no storage, memory, evaluation, or Skill update implementation.
Optional layers
enter only at a visible boundary:

- `Agent(use_storage=True)` enables configured storage.
- `Agent.for_user(id)` exposes scoped conversations, runs, model Skills, and Skill updates.
- A selected memory Skill adds long-term memory tools.
- `Agent.add_subagent(...)` adds explicit model delegation.
- `Agent.add_tool(...)` binds passive MCP Skill content to trusted code.
- `user.runs.learn(run_id)` records evidence after a completed run.

For a subsystem, start at its owner:

- Skill index and disclosure: `skill.disclosure.ProgressiveDisclosureCore`.
- Skill handling: `skill.runtime.handlers.SkillCollection` and `SkillHandlers`.
- Provider selection: `core.provider.ProviderPool`.
- Stored user access: `adapter.user.UserAgent`.
- Scoped state access: `core.state.access.StateAccess`.
- Side-effect checks: `core.checks.ActionRunner`.
- Run events: `core.state.event_log.RunEventLog`.
- Explicit Skill changes: `skill.runtime.update.SkillUpdater`.

`src/cli.py` is the direct source-tree entry point. The CLI implementation belongs to
`adapter.cli_adapter.commands`, alongside the other external command adapters. These
modules may compose Core but do not own task execution. There are no compatibility
modules; import an advanced type from the file that owns it.

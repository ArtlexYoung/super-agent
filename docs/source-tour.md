# Source Tour

Do not start by reading every file. Read one ordinary call in this order:

1. `src/super_agent.py` exports the public `Agent` from `adapter/agent.py`.
2. `src/adapter/agent.py` lazily assembles Provider, storage, Skills, and Runtime only when used.
3. `src/core/runtime/run.py` owns one run identity and task lifecycle; `core/state/run.py`
   owns the ordered event log.
4. `src/core/runtime/loop.py` gives the model selected context and checked tools.
5. `src/skill/disclosure.py` builds the shared Skill index and opens requested content; its
   optional cache recorder is supplied by `adapter/storage`.
6. `src/core/provider.py` makes and measures the selected Provider call.
7. Checkpoints are recorded by `src/core/runtime/run.py` as content-free recovery facts.

Everything outside this path is optional state, a Skill mechanism, or an external adapter.
Open those owners only when the task needs them.

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
- `agent.skills` owns enabled Skill references and reviewed handler registration.
- `agent.events` owns named Runtime event subscriber registration.
- `user.runs.learn(run_id)` records evidence after a completed run.

For a subsystem, start at its owner:

- Agent actions and external wiring: `adapter.agent.Agent`.
- Agent composition and lazy startup: `adapter.agent.Agent`.
- Child Agent composition: `core.runtime.team.AgentTeam`.
- Run lifecycle: `core.runtime.run.Runtime`.
- Model loop and calls: `core.runtime.loop.ModelLoop` and `core.runtime.model_calls.ModelCalls`.
- Provider protocols and pooling: `core.provider`.
- State backend and store: `core.state.store`.
- Run event log: `core.state.run`.
- Skill index and disclosure: `skill.disclosure.ProgressiveDisclosureCore`.
- Storage creation, shared values, and explicit copying: `adapter.storage`.
- Disclosure persistence and backend creation: `adapter.storage`.
- AG-UI transport and event mapping: `adapter.ag_ui_adapter.server`.
- Web management operations and configuration updates: `adapter.ag_ui_adapter.web_api`.
- Skill handling: `skill.runtime.handlers.SkillCollection` and `SkillHandlers`.
- Model Skill management: `skill.runtime.model_skills.ModelSkillManager`.
- Skill file lifecycle: `skill.runtime.package.SkillPackageManager` and its explicit
  validation functions.
- Conversation state: `core.state.conversations`.
- Skill evidence and changes: `skill.learning`.
- Scoped state and audit: `core.state`.
- Side-effect checks: `core.checks.ActionRunner`.
- CLI, Web, and storage I/O: their modules under `adapter`.

`src/cli.py` is the direct source-tree entry point. The CLI implementation belongs to
`adapter.cli_adapter.commands`, while terminal settings and confirmation live in
`adapter.cli_adapter.configuration`, which also owns Agent and storage construction. These modules
use `adapter.agent` for the single explicit Agent access boundary and do not own task execution.
There are no compatibility modules; import an advanced type from the file that owns it. Release
tests import removed module paths in a fresh process and require them to fail, so obsolete owners
cannot return unnoticed. Core and Skill source are also checked to ensure they never import
Adapter code.

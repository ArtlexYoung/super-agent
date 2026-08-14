# Source Tour

Do not start by reading every file. Read one ordinary call in this order:

1. `src/super_agent.py` exports the public `Agent` from `adapter/agent.py`.
2. `src/adapter/agent.py` lazily assembles Provider, storage, Skills, and Runtime only when used.
3. `src/core/runtime.py` creates one `Run` that owns the task, identity, Skill snapshot,
   storage view, and ordered event log.
4. `src/core/loop.py` gives the model selected context; `src/core/tools.py` receives that
   same `Run` directly and executes checked tools.
5. `src/skill/discovery/catalog.py` builds the shared Skill index and opens requested
   content; its optional cache recorder is supplied by `adapter/storage_backends`.
6. `src/core/provider.py` makes and measures the selected Provider call.
7. Checkpoints are recorded by `src/core/runtime.py` as content-free recovery facts.

Everything outside this path is optional state, a Skill mechanism, or an external adapter.
Open those owners only when the task needs them.

The short execution path is:

```text
super_agent.Agent
  -> Agent.run
  -> Runtime.run_task
  -> TaskRunner.run_task
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
- Child Agent composition: `core.team.AgentTeam`.
- Run lifecycle: `core.runtime.Runtime`.
- Task execution and model calls: `core.loop.TaskRunner` and `core.model_calls.ModelCaller`.
- Provider protocols and pooling: `core.provider`.
- State backend and store: `core.records.store`.
- Run event log: `core.records.events`.
- Skill index and disclosure: `skill.discovery.catalog.ProgressiveDisclosureCore`.
- Storage creation and persistence: `adapter.storage_backends.storage`.
- AG-UI transport and event mapping: `adapter.http.agui`.
- Web management operations and configuration updates: `adapter.http.web`.
- Skill handling: `skill.handlers.runtime.Skills` and `SkillHandlers`.
- Model Skill management: `skill.handlers.model_management.ModelSkillManager`.
- Skill file lifecycle: `skill.handlers.package.SkillPackageManager` and its explicit
  validation functions.
- Conversation state: `core.records.conversations`.
- Long-term memory and usage-habit events: `skill.handlers.memory.Memory`.
- Skill evidence and changes: `skill.learning`.
- Scoped state and audit: `core.records.store` and `core.records.audit`.
- Side-effect checks: `core.checks.ActionRunner`.
- CLI, Web, and storage I/O: their modules under `adapter`.

`src/cli.py` is the direct source-tree entry point. The CLI implementation belongs to
`adapter.cli`, while terminal settings and confirmation live in
`adapter.cli_support.cli_config`, which also owns Agent and storage construction. These modules
use `adapter.agent` for the single explicit Agent access boundary and do not own task execution.
There are no compatibility modules; import an advanced type from the file that owns it. Release
tests import removed module paths in a fresh process and require them to fail, so obsolete owners
cannot return unnoticed. Core and Skill source are also checked to ensure they never import
Adapter code.

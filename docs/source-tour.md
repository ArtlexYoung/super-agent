# Source Tour

Read one ordinary call in this order:

1. `src/super_agent.py` wires external factories and exports the public `Agent` facade.
2. `src/core/runtime/agent.py` exposes Agent actions and owns lazy resources, while `team.py`
   owns child Agents.
3. `src/core/runtime/run.py` owns one run identity and task lifecycle; `core/state/run.py`
   owns the ordered event log.
4. `src/core/runtime/loop.py` gives the model selected context and checked tools.
5. `src/skill/disclosure.py` builds the shared Skill index and opens requested content; its
   optional cache recorder is supplied by `adapter/storage`.
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

- Agent actions: `core.runtime.agent.Agent`.
- Lazy run resources: `core.runtime.agent.AgentSetup`.
- Child Agent composition: `core.runtime.team.AgentTeam`.
- Run lifecycle: `core.runtime.run.Runtime`.
- Model loop and calls: `core.runtime.loop.ModelLoop` and `core.runtime.model_calls.ModelCalls`.
- Provider protocols and pooling: `core.provider`.
- State backend and store: `core.state.store`.
- Run event log: `core.state.run`.
- Skill index and disclosure: `skill.disclosure.ProgressiveDisclosureCore`.
- Storage creation, shared values, and explicit copying: `adapter.storage`.
- Disclosure persistence: `adapter.storage.disclosure.DisclosureStorage`.
- AG-UI transport and event mapping: `adapter.ag_ui_adapter.server`.
- Web management operations and configuration updates: `adapter.ag_ui_adapter.web_api`.
- Skill handling: `skill.runtime.handlers.SkillCollection` and `SkillHandlers`.
- Model Skill management: `skill.runtime.model_skills.ModelSkillManager`.
- Skill file lifecycle: `skill.runtime.files.package.SkillPackageManager` and its explicit
  validation functions.
- Conversation state: `core.state.conversations`.
- Skill evidence and changes: `skill.learning`.
- Scoped state and audit: `core.state`.
- Side-effect checks: `core.checks.ActionRunner`.
- External Agent access: `adapter.agent`.
- CLI, Web, and storage I/O: their modules under `adapter`.

`src/cli.py` is the direct source-tree entry point. The CLI implementation belongs to
`adapter.cli_adapter.commands`, while terminal settings and confirmation live in
`adapter.cli_adapter.configuration` and Agent/storage construction lives in
`adapter.cli_adapter.loaders`. These
modules use `adapter.agent` for the single explicit Agent access boundary and do not own task
execution. There are no compatibility modules; import an advanced type from the file that owns
it. The release tests also import every removed module path in a fresh process and require it to
fail, including the former `adapter.conversations` path, so an obsolete owner cannot return
unnoticed. Core and Skill source are also checked to ensure they never import Adapter code.

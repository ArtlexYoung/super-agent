# Super Agent

[中文说明](README_cn.md)

> Skill is all you need.

Super Agent is a **simple, lightweight, self-evolving, skill-first agent runtime**.

Prompts, tools, memory, workflows, model descriptions, and planning rules are all
Skills. One progressive-disclosure core discovers and loads them only when needed.
One Runtime schedules the task, records evidence, and improves eligible Agent-owned
Skills without introducing a second execution path.

Task scenes group the Skills needed for one kind of work. The included `common` scene
handles general tasks, while the optional `code` scene provides a repository coding
chain. Runtime selects a scene from the request, Agent configuration, or prompt triggers;
there is no setup step and no hidden fallback after selection.

The project is experimental and remains in `0.0.x`. Breaking changes are intentional
while the skill-first model is being validated.

## Quick Start

Python 3.11 or newer is required. The Python Runtime has no third-party dependency.

```bash
python3 -m pip install -e .
export OPENAI_API_KEY="..."
super-agent "Explain this repository"
```

`ANTHROPIC_API_KEY` and `OLLAMA_HOST` are also discovered automatically. No
`agent.toml` or initialization step is required. To run a deterministic offline demo,
select the mock Provider explicitly:

```bash
SUPER_AGENT_PROVIDER=mock super-agent "hello"
```

Super Agent never creates an implicit mock and never switches models after a Provider
failure. A missing model or failed call is returned as an explicit error.

Start an interactive conversation or the optional Web client:

```bash
super-agent
super-agent serve
```

The Web client opens at `http://127.0.0.1:8765/`. It includes the native AG-UI chat,
visual configuration, run trees, memory management, Skill freshness, and a CopilotKit
example that connects directly to `POST /ag-ui`.

## Python Library

```python
from super_agent import Agent

agent = Agent()
result = agent.run("Explain progressive Skill disclosure")
print(result.text)
print(result.run_id)
```

Runtime normally chooses a task scene automatically. Applications and CLI users can
override it for one run:

```python
result = agent.run("Inspect this change", scene="code")
```

For an embedded call that must not create a backend, conversation, memory, cache, or
evaluation state, disable storage explicitly. The same Runtime loop uses the shipped
storage-free scene and returns its ordered events in memory:

```python
agent = Agent(provider=my_provider, use_storage=False)
result = agent.run("Classify this text")
print(result.events)
```

Selecting a storage-dependent scene or passing a conversation ID in this mode is an
error; Runtime never removes the requested feature or creates storage as a substitute.

Application code may explicitly inject its own Provider, storage backend, action
rules, SkillRunners, and subagents. The CLI uses this same library; it is not a second
runtime.

## Add Only the Layers You Need

The zero-configuration path uses local JSONL, but neither lower nor higher layers are
mandatory. Each choice is explicit and keeps the same disclosure and execution core:

| Need | Choose | Added behavior |
| --- | --- | --- |
| Inspect Skill data | read-only progressive disclosure | No model, cache, history, or storage |
| Run one isolated task | `Agent(provider=..., use_storage=False)` | Provider execution and in-memory events |
| Keep local evidence | `Agent()` | JSONL traces, disclosure cache, evaluation, and freshness |
| Continue a conversation | pass `conversation_id` | Isolated messages plus temporary and long-term memory Skills |
| Add behavior | `add_skill_runner(...)` or `add_subagent(...)` | Explicit custom execution or delegation |
| Evolve private Skills | Agent-owned Skill with updates enabled | Candidate, evidence-bound evaluation, promotion, monitoring, and rollback |

Runtime validates the requirements for the selected layer before the first model call.
It reports a missing service or runner instead of silently removing the requested feature.

## One Mental Model

```text
Provider     connects model intelligence
Core         schedules tasks and owns state
SkillRunner  turns one Skill type into behavior
Skill        carries passive content and configuration
Agent        combines everything in readable Python code
```

Every task follows one path:

```text
Agent.run
  -> Core creates one complete Run context
  -> progressive disclosure selects one task scene and its Skills
  -> Core creates one RunPlan with exactly one model decision
  -> preflight checks every planned runner, service, tool, Provider, and subagent
  -> SkillRunners load the planned Skills and Core executes the run
  -> one scoped event path records the run; every view and learning layer consumes it
```

There are no separate memory, workflow, MCP, or planning engines. They are ordinary
Skill types loaded by registered SkillRunners. The progressive-disclosure core can also
be used independently for read-only discovery, with cache and history writes enabled
only when the caller asks for them.
Every runner returns the same `LoadedSkill` result. A scene includes its task-specific
Skills through that result instead of introducing a second scene execution interface.
Reading Skill source is pure. Cache disclosure, runner activation, memory organization,
and every other state change are separate explicit operations.

## Task Scenes

Included scene content lives outside Python source and remains ordinary passive Skill
data:

```text
skill_scenes/
  common/   stateful/stateless scenes + prompt + memory + planner + direct workflow
  code/     scene + coding prompt + project memory + planner + tool loop
```

Selection has one explicit order: the run's `scene`, one `scene:*` pinned in
`agent.skills`, one prompt-trigger match, then the single default scene. Multiple pinned
or matching scenes are errors. Once selected, Runtime uses only that scene's chain;
explicitly pinned memory, planner, or workflow Skills replace the same type from the
scene.

An Agent can create a complete user-private scene during a tool-using conversation with
`create_skill_scene`. Application code can perform the same explicit operation:

```python
from skill.kinds.scene import SkillSceneInput
from super_agent import Agent

alice = Agent().for_user("alice")
alice.skills.create_scene(
    SkillSceneInput(
        name="research",
        description="Investigate claims and cited sources",
        triggers=["source investigation"],
    )
)
```

Creation writes five Agent-owned, updateable Skills: `scene`, `prompt`, `memory`,
`planner`, and `workflow`. The prepared index for the current run is immutable, so the
new scene becomes available on the next run. Another user cannot read or select it.

## Create a Skill

```text
skills/prompt/concise/
  skill.toml
  SKILL.md
```

```toml
schema_version = 3
name = "concise"
type = "prompt"
description = "Answer with the smallest useful explanation"
version = "0.1.0"
triggers = ["brief", "concise"]
agent_created = false
agent_can_update = false

[entry]
instructions = "SKILL.md"
```

The stable key is `type:name`, such as `prompt:concise`, `memory:default`, or
`model:fast`. Custom types need only a matching `Agent.add_skill_runner(...)` call; Core
does not contain a fixed list of Skill types.

Executable MCP implementations stay in trusted application code. The passive MCP Skill
contains only instructions and an optional `[configuration].server` name; command,
arguments, environment, transport, and effects are rejected in Skill content:

```python
from super_agent import ActionEffect, Agent, StdioMcpServer

agent = Agent()
agent.add_mcp_server(
    "filesystem",
    StdioMcpServer(
        "npx",
        arguments=("-y", "@modelcontextprotocol/server-filesystem"),
    ),
    effects=(
        ActionEffect.READ,
        ActionEffect.CREATE,
        ActionEffect.UPDATE,
        ActionEffect.DELETE,
        ActionEffect.EXECUTE,
    ),
)
```

Missing registrations fail preflight before a model call or process start. Runtime checks
the declared effects first and records code/settings hashes without environment values.

Run `super-agent init --path my-agent` when you want an editable example project. It is
optional, not a prerequisite.

## Configure Only What You Need

`Agent()` first checks `SUPER_AGENT_CONFIG`, then `agent.toml`, then uses in-memory
defaults. A complete minimal file is:

```toml
[agent]
name = "demo"
system = "You are a concise, helpful agent."
skills = []
disabled_skills = []

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
```

`skills` pins Skills to every task. Unpinned Skills remain available for automatic
selection. Pin at most one `scene:*`; omit it for automatic scene selection.
`disabled_skills` excludes a type, key, or unambiguous name. Model profiles
are model Skills rather than special Agent fields, and secrets stay in environment
variables rather than TOML.

## Automatic Scheduling and Evolution

- Model Skills describe purpose, features, quality, latency, token cost, and connection
  environment names. Core chooses a ready model from those declared traits and
  user-scoped evidence before each model call.
- Runtime events record the selected model, disclosed Skills, tools, subagents, token
  estimates, action decisions, result, and failure without storing secret values.
- Every run records immutable learning evidence but does not update learned state. Call
  `Agent.learn_from_run(run_id)` or `runs learn` to explicitly evaluate the run, refresh
  routing and freshness evidence, and review eligible Skill evolution.
- Skill freshness is computed without a model from usage, outcome, recency, frequency,
  token cost, and successful same-function replacements.
- Agent-owned Skills with `agent_can_update = true` can create candidates, run
  non-regression evaluation, promote a complete Skill directory, monitor it, and roll it
  back. Promotion uses the exact recorded report and rejects changed candidates,
  baselines, cases, or report files. Activation verifies the copied directory and restores
  the previous Skill if Runtime refresh or state recording fails; shared project Skills
  remain read-only baselines.
- Temporary memory is keyed to the current conversation and cannot enter another
  conversation. Long-term memory is reserved for abstract, critical, important, stable,
  or habitual knowledge.
- During long-term organization, the model can inspect relevant temporary memory from the
  current conversation and explicitly promote an abstraction. The temporary source stays
  in its conversation, and its source IDs remain auditable.
- Recall can merge, replace, archive, forget, or promote only through validated operations.
  Every mutation is explicit; invalid model output is an error.

Inspect the evidence directly:

```bash
super-agent runs learn --run-id <run-id>
super-agent runs explain --run-id <run-id>
super-agent skills index --output json
super-agent skills freshness
super-agent evolution list
```

The explicit learning operation is idempotent. A failure records `learning.failed` with
its exact stage and raises the original error; retrying reuses already recorded evidence.
Application observers added with `Agent.add_event_subscriber(...)` remain part of the run
stream, and their failures are visible in `TaskResult.subscriber_failures`. Stateless
Agents can run but cannot learn because they have no persisted evidence store.

## Multiuser and Multi-Agent

Bind user state once with `Agent.for_user(...)`. Conversations, memory, Skill usage,
disclosure history, model evidence, Provider caches, user Skill overlays, and evolution
state are isolated by user and Agent.

```python
main = Agent("agents/main.toml")
coder = Agent("agents/coder.toml")
reviewer = Agent("agents/reviewer.toml")

main.add_subagent(coder, name="coder", triggers=["code", "implement"])
main.add_subagent(reviewer, triggers=["review"])
result = main.for_user("alice").run("Implement and review this change")
```

An omitted name becomes `subagent01`, `subagent02`, and so on. Deep or cyclic links
produce a readable chain warning before execution; they are not silently blocked. An
optional `max_agent_chain_depth` only controls that warning threshold. Workflow Skills
define stopping behavior.

## Storage and Explicit Effects

The default storage is readable, dependency-free JSONL under `.super-agent/`. SQLite is
also standard-library only. MySQL and PostgreSQL are optional extras installed only when
selected. All backends implement the same user-scoped event contract.

Every tool declares its resource and `read`, `create`, `update`, `delete`, `execute`,
`network`, or `delegate` effects. Before the first model call, preflight returns all
missing runners, services, invalid tools, unavailable Providers, and subagents together.
State changes then pass explicit `prepared` and `applying` stages before `applied`; reads
execute directly after their check. Skill text is untrusted context and cannot grant
itself execution rights. There is no undeclared fallback action.

## Documentation

- [Getting started](docs/getting-started.md)
- [Architecture](docs/architecture.md)
- [Skills and progressive disclosure](docs/skills.md)
- [SkillRunners](docs/skill-runners.md)
- [Configuration](docs/configuration.md)
- [Core, tracing, and multi-agent execution](docs/runtime.md)
- [Evaluation, memory, and evolution](docs/evolution.md)
- [Action rules](docs/safety.md)
- [CLI reference](docs/cli.md)
- [Web client](docs/web.md)
- [AG-UI](docs/ag-ui.md)
- [Roadmap](docs/roadmap.md)

## Development

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests python3 -m unittest discover -s tests -v
PYTHONPATH=src:tests python3 -m compileall -q src tests docs/experiments
pnpm --dir web lint
pnpm --dir web typecheck
pnpm --dir web build
```

`super_agent` exports the small everyday Runtime API. Advanced adapters, concrete storage
backends, and Skill-type management APIs are imported from their owning modules. Internal
import compatibility is intentionally not provided during `0.0.x`.

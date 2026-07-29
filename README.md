# Super Agent

[中文说明](README_cn.md)

**A simple, lightweight, self-evolving, skill-first agent runtime.**

> Skill is all you need.

Prompts, tools, memory, workflows, planners, and model descriptions use the same Skill
format. A central progressive-disclosure core finds the relevant content; one Runtime
plans and executes the task. Optional storage, memory, learning, and evolution layers use
that same path instead of introducing separate engines.

Super Agent is experimental and remains in `0.0.x`. Breaking changes are made directly;
old imports and silent compatibility behavior are not retained.

## Start in Three Commands

Python 3.11 or newer is required. The default Python Runtime has no third-party
dependencies.

```bash
python3 -m pip install -e .
export OPENAI_API_KEY="..."
super-agent "Explain this repository"
```

`ANTHROPIC_API_KEY` and `OLLAMA_HOST` are also discovered automatically. No project
initialization or `agent.toml` is required. For a deterministic offline check, select the
Mock Provider explicitly:

```bash
SUPER_AGENT_PROVIDER=mock super-agent "hello"
```

Without a usable model source, the run fails with a configuration error. A Provider
failure is returned unchanged; Runtime does not switch models or substitute a mock.

Run `super-agent` without a prompt for terminal chat. Run `super-agent serve` to open the
React client and AG-UI endpoint at `http://127.0.0.1:8765/`.

## Use the Python Library

```python
from super_agent import Agent

agent = Agent()
result = agent.run("Explain progressive Skill disclosure")

print(result.text)
print(result.run_id)
```

`Agent()` is lazy. It validates configuration immediately, then waits until first use to
open storage, scan Skills, discover models, and assemble Runtime. This leaves room to
register Skill runners, MCP servers, event subscribers, and subagents first.

Storage can be disabled when an embedded task needs no conversations, memory, cache, or
persisted evidence:

```python
from super_agent import Agent, MockProvider

agent = Agent(provider=MockProvider("offline result"), use_storage=False)
result = agent.run("Classify this text")
print(result.events)
```

Requesting a storage-dependent feature in this mode is an error. Runtime never drops the
requested feature to keep a task running.

## One Runtime Model

```text
Provider     connects model intelligence
Core         plans runs, executes tasks, and owns optional state
SkillRunner  turns one Skill type into behavior
Skill        stores passive content and configuration
Agent        combines Providers, runners, storage, and subagents in Python
```

Every run uses one path:

```text
Agent.run
  -> build one complete Run
  -> select one task scene and progressively disclose its Skills
  -> create one RunPlan with one model decision
  -> preflight every runner, service, tool, Provider, and subagent
  -> execute and emit one ordered event stream
  -> optionally learn from that immutable evidence
```

The design has four practical guarantees:

- **Progressive:** read-only Skill discovery works without storage; stateful features are
  added only when selected.
- **Explicit:** reads do not write. Mutations have visible prepare and apply stages.
  Missing services and invalid model output fail instead of triggering a smaller fallback.
- **Isolated:** conversations, memory, evidence, disclosure history, and user Skill
  overlays are scoped by user and Agent.
- **Evolvable:** an explicit `learn_from_run` call evaluates evidence. Agent-owned,
  updateable Skills can then pass through candidate, non-regression evaluation, promotion,
  monitoring, and rollback.

Skill content is passive data and cannot grant itself execution rights. Executable MCP
servers and custom Skill runners are registered in trusted application code. Tools declare
their effects before preflight.

## Skills and Scenes

The shipped scene trees are ordinary Skills outside Python source:

```text
skill_scenes/
  common/   general tasks, including a storage-free path
  code/     repository planning, tool use, verification, and review
```

Runtime selects one scene from an explicit request, Agent configuration, or prompt
triggers. Ambiguity is an error. Applications can add any Skill type by registering a
runner with `Agent.add_skill_runner(...)`; Core does not contain a fixed type list.

Use `super-agent init --path my-agent` only when you want an editable example. Model
profiles are model Skills, while API keys remain in environment variables. See
[Skills](docs/skills.md) and [Configuration](docs/configuration.md) for the format.

## Optional Layers

The default is local, readable JSONL storage under `.super-agent/`. SQLite also uses only
the standard library. MySQL and PostgreSQL drivers are optional extras loaded only when
their backend is selected.

Common entry points:

```bash
super-agent run --scene code "Inspect this change"
super-agent skills index --output json
super-agent runs explain --run-id <run-id>
super-agent runs learn --run-id <run-id>
super-agent skills freshness
```

Subagents are composed in Python with `Agent.add_subagent(...)`, not TOML. Memory,
workflow, planning, MCP, scenes, and model profiles remain ordinary Skill types.

## Documentation

- [Getting started](docs/getting-started.md)
- [Architecture](docs/architecture.md)
- [Skills and progressive disclosure](docs/skills.md)
- [Skill runners and MCP](docs/skill-runners.md)
- [Configuration and storage](docs/configuration.md)
- [Runtime, tracing, users, and subagents](docs/runtime.md)
- [Memory, evaluation, freshness, and evolution](docs/evolution.md)
- [Action rules](docs/safety.md)
- [CLI reference](docs/cli.md)
- [Web client](docs/web.md)
- [AG-UI](docs/ag-ui.md)
- [Roadmap](docs/roadmap.md)

## Development

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q src tests
pnpm --dir web lint
pnpm --dir web typecheck
pnpm --dir web build
```

The release tests enforce the source layout, import surface, function and file size,
control-flow, and directory-size budgets. Internal import compatibility is intentionally
absent during `0.0.x`.

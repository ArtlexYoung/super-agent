# Super Agent

[中文说明](README_cn.md)

> Skill is all you need.

Super Agent is a **simple, lightweight, self-evolving, skill-first agent runtime**.

Prompts, tools, memory, workflows, model descriptions, and planning rules are all
Skills. One progressive-disclosure core discovers and loads them only when needed.
One Runtime schedules the task, records evidence, and improves eligible Agent-owned
Skills without introducing a second execution path.

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

Application code may explicitly inject its own Provider, storage backend, action
rules, SkillRunners, and subagents. The CLI uses this same library; it is not a second
runtime.

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
  -> Core creates one run session
  -> progressive disclosure selects Skills
  -> SkillRunners load selected Skills
  -> Core selects a model and executes the task
  -> events, evaluation, freshness, and evolution evidence are recorded
```

There are no separate memory, workflow, MCP, or planning engines. They are ordinary
Skill types loaded by registered SkillRunners. The progressive-disclosure core can also
be used independently for read-only discovery, with cache and history writes enabled
only when the caller asks for them.

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
selection. `disabled_skills` excludes a type, key, or unambiguous name. Model profiles
are model Skills rather than special Agent fields, and secrets stay in environment
variables rather than TOML.

## Automatic Scheduling and Evolution

- Model Skills describe purpose, features, quality, latency, token cost, and connection
  environment names. Core chooses a ready model from those declared traits and
  user-scoped evidence before each model call.
- Runtime events record the selected model, disclosed Skills, tools, subagents, token
  estimates, action decisions, result, and failure without storing secret values.
- Skill freshness is computed without a model from usage, outcome, recency, frequency,
  token cost, and successful same-function replacements.
- Agent-owned Skills with `agent_can_update = true` can create candidates, run
  non-regression evaluation, promote a complete Skill directory, monitor it, and roll it
  back. Shared project Skills remain read-only baselines.
- Temporary memory is keyed to the current conversation and cannot enter another
  conversation. Long-term memory is reserved for abstract, critical, important, stable,
  or habitual knowledge.
- Recall organizes each memory type independently and can merge, replace, archive, or
  forget stale items. Every mutation is explicit; invalid model output is an error.

Inspect the evidence directly:

```bash
super-agent runs explain --run-id <run-id>
super-agent skills index --output json
super-agent skills freshness
super-agent evolution list
```

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

Every side-effecting tool declares its resource and `read`, `create`, `update`, `delete`,
`execute`, `network`, or `delegate` effects. Core checks those effects before invoking
the handler. Skill text is untrusted context and cannot grant itself execution rights.
There is no undeclared fallback action.

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

The public Python API is exported from `super_agent`. Internal import compatibility is
intentionally not provided during `0.0.x`.

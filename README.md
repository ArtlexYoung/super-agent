# Super Agent

[中文说明](README_cn.md)

**A simple, lightweight, self-evolving, skill-first agent runtime.**

> Skill is all you need.

Prompts, tools, memory, workflows, planners, and model descriptions use the same Skill
format. A central progressive-disclosure core finds the relevant content; one Runtime
plans and executes the task. Optional storage, memory, learning, and evolution layers use
that same path instead of introducing separate engines.

At the start of each run, one routing-model call reads compact descriptions and returns one
structured decision for the scene, Skills, planning mode, purpose, execution model, and
subagents. Runtime contains no keyword matching or hidden local fallback. Explicit caller
choices are constraints, and unknown or incompatible model choices fail visibly.

Super Agent is experimental and remains in `0.0.x`. Breaking changes are made directly;
old imports and silent compatibility behavior are not retained.

## Start in 60 Seconds

Python 3.11 or newer is required. The default Python Runtime has no third-party
dependencies.

```bash
python3 -m pip install -e .
export OPENAI_API_KEY="..."
super-agent "Explain this repository"
```

That is the complete default setup. `ANTHROPIC_API_KEY` and `OLLAMA_HOST` are also
discovered automatically; no initialization or `agent.toml` is required. For an offline
smoke test, choose the Mock Provider explicitly:

```bash
SUPER_AGENT_PROVIDER=mock super-agent "hello"
```

Without a usable model source, the run fails with a configuration error. A Provider
failure is returned unchanged; Runtime does not switch models or substitute a mock.

Run `super-agent` without a prompt for terminal chat.

## Use the Python Library

```python
from super_agent import Agent

agent = Agent()
result = agent.run("Explain progressive Skill disclosure")

print(result.text)
print(result.run_id)
```

`Agent()` is lazy: it discovers the model and relevant Skills on first use. It does not
open storage unless the application explicitly asks for it.

The Python library starts without storage. This is enough for an embedded task that needs
no conversations, memory, cache, or persisted evidence:

```python
from super_agent import Agent, MockProvider

agent = Agent(provider=MockProvider("offline result"))
result = agent.run("Classify this text")
print(result.events)
```

Requesting a storage-dependent feature without storage is an error. Runtime does not drop
the feature, retry another model, or substitute a mock to keep running.

## Add Specialization in Code

Scenes are optional Skill sets for a task domain. Runtime can select one automatically, or
each Agent can choose its own scope:

```python
from super_agent import Agent

main = Agent()
coder = Agent()
coder.use_only_scenes("code")

main.add_subagent(coder, name="coder")
result = main.run("Implement and test this change")
```

The routing model decides whether to delegate and which subagent to use from their natural
language descriptions. No keyword or trigger list is involved. Use `disable_scenes()` for
direct model execution. Use `super-agent init --path my-agent` only when you want editable
Skill examples. Prompts, tools, memory, workflows, planners, scenes, and model descriptions
all use the same progressively disclosed Skill format.

## Opt In to State and Learning

CLI and Web commands use readable local JSONL under `.super-agent/`. The Python library
opts in with `Agent(use_storage=True)`. SQLite is dependency-free; MySQL and PostgreSQL
drivers are optional and load only when selected. Every stateful record is isolated by
user and Agent.

```python
agent = Agent(use_storage=True)
alice = agent.for_user("alice")
result = alice.run("Remember my preferred response style")
learning = alice.runs.learn(result.run_id)
```

`learn_from_run` is the explicit boundary for evaluation and evolution. Updateable,
Agent-owned Skills move through separate candidate, non-regression evaluation, promotion,
monitoring, and rollback stages. Preparing or evaluating a candidate never activates it.

## Web and Inspection

```bash
super-agent serve
super-agent run --scene code "Inspect this change"
super-agent runs explain --run-id <run-id>
super-agent skills index --output json
```

The React client, CopilotKit example, and AG-UI endpoint are served at
`http://127.0.0.1:8765/`.

## Design Guarantees

- **Progressive:** only relevant Skill content is loaded.
- **Model-decided:** one structured model route selects every optional task component.
- **Explicit:** reads do not write; missing requirements fail before model execution.
- **Isolated:** users and Agents do not share conversations, memory, evidence, or overlays.
- **Evolvable:** every change is evaluated, recorded, promoted explicitly, and reversible.
- **Passive by default:** Skill content cannot grant itself code execution or permissions.

## Documentation

- [Getting started](docs/getting-started.md)
- [Architecture](docs/architecture.md)
- [Skills and progressive disclosure](docs/skills.md)
- [Skill loaders and MCP](docs/skill-loaders.md)
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

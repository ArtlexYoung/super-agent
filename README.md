# Super Agent

[中文说明](README_cn.md)

**A simple, lightweight, self-evolving, skill-first agent runtime.**

> Skill is all you need.

Super Agent gives one model a small Skill index. The model opens only the Skills it needs,
uses their tools and instructions, and returns a result. Prompts, tools, memory, workflows,
task scenes, and model descriptions all use the same Skill format and the same progressive
disclosure path.

The default Python runtime has no third-party dependencies. Storage, conversations,
memory, safety rules, MCP, learning, and evolution are optional. If an enabled feature is
missing a requirement, the run fails clearly instead of silently using something else.

Super Agent is experimental and remains in `0.0.x`; breaking changes do not keep old
imports or compatibility wrappers.

## Start

Python 3.11 or newer is required.

```bash
python3 -m pip install -e .
export OPENAI_API_KEY="..."
super-agent "Explain this repository"
```

Run `super-agent` with no arguments for an interactive conversation. OpenAI-compatible,
Anthropic-compatible, and Ollama environment settings are discovered automatically. An
offline smoke test must select the mock Provider explicitly:

```bash
SUPER_AGENT_PROVIDER=mock super-agent "hello"
```

No `agent.toml` is required. Run `super-agent init --path my-agent` only when you want an
editable project and example Skill.

## Python

The common API contains one class:

```python
from super_agent import Agent

agent = Agent()
result = agent.run("Explain progressive Skill disclosure")
print(result.text)
```

`Agent()` starts without storage and creates no files. Advanced integrations use their
real modules so the source of each contract remains visible:

```python
from core.provider.chat import MockProvider
from super_agent import Agent

agent = Agent(provider=MockProvider("offline result"))
print(agent.run("Classify this text").text)
```

## Skills

A Skill starts with a compact `skill.toml`. Instructions and resources are opened only
after the model chooses them.

```toml
schema_version = 3
name = "research"
type = "prompt"
description = "Research a question and report cited findings"
version = "0.1.0"
agent_created = false
agent_can_update = false

[entry]
instructions = "SKILL.md"
```

There are no trigger words. The model selects Skills and task scenes from their
descriptions during its normal turn. A scene is just a named Skill group, so different
Agents can specialize without another execution system:

```python
from super_agent import Agent

main = Agent()
coder = Agent()
coder.use_only_scenes("code")
main.add_subagent(coder, name="coder", description="Implements and verifies code changes")
```

Model descriptions are Skills too. Mark one model Skill as the default; any other ready
model is exposed to it through `use_model(model, prompt, reason)`. The default model can
then assign an explicit subtask using each model's declared support and strengths. A
delegated call never changes the main loop and never falls back to another Provider.

## Optional State

The CLI and Web server explicitly use the configured storage. Embedded Python opts in:

```python
agent = Agent(use_storage=True)
alice = agent.for_user("alice")
result = alice.run("Remember that I prefer concise answers")
learning = alice.runs.learn(result.run_id)
```

Conversation messages are short-term context. Long-term memory stores only durable,
important information and can be reviewed, organized, or forgotten by model actions.
Users and Agents have separate conversations, memory, run evidence, and Skill overlays.

Local JSONL is the default backend. SQLite also uses the standard library; MySQL and
PostgreSQL drivers are optional extras.

## CLI and Web

The CLI has five top-level groups:

```bash
super-agent init --path my-agent
super-agent run "one prompt"
super-agent run --chat --user-id alice
super-agent skills list
super-agent data runs status
super-agent serve
```

`skills models` manages model Skills and `skills evolution` shows Skill revisions.
`data` contains conversations, long-term memory, saved runs, and storage copy commands.
The React client, CopilotKit example, and AG-UI endpoint are served at
`http://127.0.0.1:8765/`.

## Guarantees

- One central progressive disclosure path for every Skill type.
- No keyword matching, hidden Provider switch, mock substitution, or storage fallback.
- Reads do not write; state changes pass through explicit checked actions.
- Skill content is passive unless application code registers an executable tool.
- Candidate Skill changes are evaluated before explicit promotion and can be rolled back.
- A basic run does not require storage, memory, conversations, safety, or evolution.

## Documentation

- [Getting started](docs/getting-started.md)
- [Architecture](docs/architecture.md)
- [Source tour](docs/source-tour.md)
- [Skills](docs/skills.md)
- [Configuration](docs/configuration.md)
- [Runtime](docs/runtime.md)
- [CLI](docs/cli.md)
- [Memory and evolution](docs/evolution.md)
- [Safety](docs/safety.md)
- [Web](docs/web.md) and [AG-UI](docs/ag-ui.md)

## Development

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests python3 -m unittest discover -s tests -p 'test_*.py'
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m compileall -q src
pnpm --dir web typecheck
pnpm --dir web lint
pnpm --dir web build
```

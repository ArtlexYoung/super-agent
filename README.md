# Super Agent

[中文说明](README_cn.md)

> Skill is all you need.

Super Agent is a **simple, lightweight, self-evolving, skill-first Agent runtime**.

Prompts, tools, memory, workflows, model descriptions, and other Agent behavior use one
Skill format and one lifecycle. Runtime progressively discloses only relevant content,
executes it through registered Capabilities, records real outcomes, and can improve
Agent-owned Skills from that evidence.

The project is experimental (`0.0.x`). Breaking changes are explicit while the core
model is being validated.

## Start

Python 3.11 or newer is required. From the repository root:

```bash
python3 -m pip install -e .
super-agent "Explain this repository"
```

That is the complete beginner path. It needs no configuration, dependency, API key, or
project initialization. When no real model is available, the built-in deterministic mock
keeps the Runtime usable.

Start a continuous conversation:

```bash
super-agent
```

Open the React Web client:

```bash
super-agent serve
```

Then visit `http://127.0.0.1:8765/`. The same server exposes the AG-UI stream at
`/ag-ui` and Runtime-backed management routes under `/api`.

Use the Python library directly:

```python
from super_agent import Agent

result = Agent().run("Explain progressive Skill disclosure")
print(result.text)
```

## Connect a Model

For the common case, set one environment variable and run the same command:

```bash
export OPENAI_API_KEY="..."
super-agent "Summarize this project"
```

`ANTHROPIC_API_KEY` and `OLLAMA_HOST` are also discovered automatically. The Runtime
uses the built-in mock only when it finds no configured model.

Create a model Skill when you need a persistent name, description, routing traits, or a
custom endpoint:

```toml
# skills/model/fast/skill.toml
schema_version = 2
name = "fast"
capability = "model"
description = "Low-latency model for summaries"
version = "0.1.0"
triggers = ["summary"]

[configuration]
provider = "openai-compatible"
model = "gpt-4.1-mini"
api_key_env = "OPENAI_API_KEY"
supports = ["text", "tools"]
purposes = ["summary"]
default = true
```

The Web client edits the same model Skills visually. Skill files contain only the
credential variable name, never the credential value. See
[Configuration](docs/configuration.md) for every optional trait.

## One Mental Model

Super Agent keeps five responsibilities separate:

```text
Provider    provides model intelligence
Runtime     owns scheduling and the task lifecycle
Capability  executes a trusted mechanism
Skill       carries passive content and configuration
Agent       composes models, Capabilities, storage, and subagents
```

Every task follows one central path:

```text
discover -> disclose -> execute -> observe -> evaluate -> evolve
```

There is one progressive disclosure core, one adaptive task loop, one action safety
boundary, and one canonical event store. Workflow and Planner Skills are policies, not
parallel execution engines. AG-UI and the Web client project the same Runtime state.

## Create a Skill

A Skill is a directory with `skill.toml` and optional content files:

```text
skills/prompt/concise/
  skill.toml
  SKILL.md
```

```toml
schema_version = 2
name = "concise"
capability = "prompt"
description = "Answer clearly with minimal wording"
version = "0.1.0"
triggers = ["brief", "concise"]

[entry]
instructions = "SKILL.md"
```

```markdown
Prefer short sentences. Keep only information needed to answer the request.
```

Prompt, MCP, memory, workflow, Planner, model, and custom declarative Skills share this
index and lifecycle. Their stable keys use `capability:name`, such as
`prompt:concise`, `memory:default`, or `model:fast`.

Initialize an editable example only when you need one:

```bash
super-agent init --path my-agent
super-agent run --config my-agent/agent.toml "Give a concise answer"
```

## Automatic Behavior

- **Progressive disclosure**: Runtime loads a compact index first, then manifests,
  instructions, configuration, and resources only when needed. Disclosed paths and cache
  hits remain in the run history.
- **Planning and scheduling**: simple work becomes one direct plan step; complex work can
  use `planner:default`. Each step selects compatible models and subagents from declared
  traits plus user-scoped quality, reliability, latency, token, and cost evidence.
- **Memory maintenance**: recall can merge, supersede, archive, or forget stale facts.
  Every change is validated and retained as an event, while the active view stays clean.
- **Self-evolution**: Agent-owned, updateable Skills can become candidates, pass
  evaluation, be promoted into the user's overlay, and roll back after regressions.
- **Mandatory Safety**: every executable Tool declares effects such as `read`, `update`,
  `execute`, or `network`. The default policy keeps internal work automatic and requires
  approval for risky external effects before a handler runs.

Inspect a run without learning another API:

```bash
super-agent runs explain --run-id <run-id>
super-agent skills index --output json
super-agent evolution list
```

## Multiuser and Multi-Agent

Bind stateful operations once with `Agent.for_user(...)`. Conversations, memory, routing
evidence, Skill overlays, disclosure caches, evolution, model profiles, Provider caches,
and optional secrets remain in that user scope.

```python
secrets = {
    ("alice", "OPENAI_API_KEY"): "...",
    ("bob", "OPENAI_API_KEY"): "...",
}

agent = Agent(secret_lookup=lambda user_id, name: secrets.get((user_id, name)))
alice = agent.for_user("alice")
result = alice.run("Summarize my private project")
```

Create Agents independently and attach them in readable Python code:

```python
main = Agent("agents/main.toml")
coder = Agent("agents/coder.toml")
reviewer = Agent("agents/reviewer.toml")

main.add_subagent(coder, name="coder", triggers=["code", "implement"])
main.add_subagent(reviewer, triggers=["review"])
result = main.run("Implement and review this change")
```

Omitting a subagent name creates `subagent01`, `subagent02`, and so on. Nested and cyclic
graphs produce explicit path warnings; workflow rules decide when execution ends.

## Storage

The default backend is readable, dependency-free JSONL under `.super-agent/`. Select
SQLite by changing one setting and adding no dependency; install only the driver needed
for optional MySQL or PostgreSQL deployments.

All backends store the same canonical events. Shared project Skills are read-only
baselines, while user-created, edited, installed, and evolved Skills live in isolated
user overlays resolved in this order:

```text
user > project > builtin
```

See [Configuration](docs/configuration.md) for backend settings and
[Runtime](docs/runtime.md) for state semantics.

## Unified Proof

The maintained [v0.0.61 unified Runtime proof](docs/experiments/v0.0.61.md) runs one real
Agent with isolated Alice and Bob scopes. It verifies user model Skills and credentials,
automatic model scheduling, progressive disclosure cache reuse, memory organization and
forgetting, pre-handler Safety blocking, canonical events and locks without secrets, and
automatic Skill promotion only for the affected user.

The proof is deterministic, local, standard-library only, and requires no real API key:

```bash
PYTHONPATH=src python3 docs/experiments/run_v0_0_61.py
```

The [machine-readable report](docs/experiments/v0.0.61.json) contains all seven passing
checks. Earlier reports are retained as [release history](docs/experiments/README.md),
not as compatibility targets for the current `0.0.x` API.

## Documentation

- [Getting Started](docs/getting-started.md)
- [Architecture](docs/architecture.md)
- [Skills and Progressive Disclosure](docs/skills.md)
- [Capabilities](docs/capabilities.md)
- [Runtime, Tracing, and Multi-Agent](docs/runtime.md)
- [Evaluation, Memory, and Evolution](docs/evolution.md)
- [Runtime Safety](docs/safety.md)
- [CLI Reference](docs/cli.md)
- [Configuration](docs/configuration.md)
- [Web Client](docs/web.md)
- [AG-UI Bridge](docs/ag-ui.md)
- [Roadmap](docs/roadmap.md)

## Development

```bash
PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .
python3 -m compileall -q src tests docs/experiments
pnpm --dir web lint
pnpm --dir web build
```

The Python Runtime has no third-party dependency. The public API is exported from
`super_agent`; internal compatibility facades are intentionally absent during `0.0.x`.

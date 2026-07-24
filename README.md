# Super Agent

[Chinese documentation](README_cn.md)

> Skill is all you need.

Super Agent is a **simple, lightweight, self-evolving, skill-first Agent runtime**.

It explores one idea: prompts, tools, memory, workflows, and other Agent behavior can all be declared as Skills, progressively disclosed, executed by Capabilities, evaluated from real runs, and improved over time.

The project is currently experimental (`0.0.x`). It favors a small, inspectable runtime and explicit breaking changes over premature API compatibility.

## Why Super Agent

- **Zero-configuration start**: `Agent()` and the CLI run immediately with a local mock model.
- **One Skill format**: prompt, MCP, memory, and workflow content share the same manifest and discovery path.
- **Progressive disclosure**: the model sees a compact index first and opens only the Skills it needs.
- **One runtime lifecycle**: discovery, disclosure, execution, observation, evaluation, and evolution share one session.
- **Code-composed Agents**: create Agents independently and attach them with `Agent.add_subagent(...)`.
- **Standard-library runtime**: the Python core has no third-party runtime dependencies.

## Start in 30 Seconds

Python 3.11 or newer is required. From the repository root:

```bash
python3 -m pip install -e .
super-agent run "hello"
```

No configuration or API key is required. Without a discovered model, Super Agent uses its deterministic local mock provider.

Start an interactive conversation:

```bash
super-agent
```

Use it as a Python library:

```python
from super_agent import Agent

result = Agent().run("Explain progressive Skill disclosure")
print(result.text)
```

## Use a Real Model

Model settings are discovered automatically. For example:

```bash
export OPENAI_API_KEY="..."
super-agent models resolve
super-agent run "Summarize this project"
```

`ANTHROPIC_API_KEY` and `OLLAMA_HOST` are also discovered. Explicit TOML configuration is optional and always takes priority. See [Configuration](docs/configuration.md).

## Create a Project

Only initialize a project when you want editable configuration or Skills:

```bash
super-agent init --path my-agent
super-agent run --config my-agent/agent.toml "hello"
```

The generated project contains one Agent configuration and example prompt, MCP, memory, and workflow Skills.

## Create a Skill

A Skill is a directory with a `skill.toml` manifest and optional content files:

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

Run a prompt that matches the Skill:

```bash
super-agent run --config agent.toml "Give me a concise explanation"
```

Stable Skill identities use `capability:name`, such as `prompt:concise`, `memory:default`, or `workflow:direct`. Configuration-only Skills do not need `SKILL.md`.

## How It Works

Super Agent keeps five responsibilities separate:

```text
Provider   provides model intelligence
Runtime    owns the shared lifecycle
Capability executes a mechanism
Skill      carries content and configuration
Agent      composes everything
```

Every run follows one central lifecycle:

```text
discover -> disclose -> execute -> observe -> evaluate -> evolve
```

`RuntimeSession` is the single context for a run. It holds the resolved state paths, run trace, one Skill index, progressive disclosure session, and every Skill or Capability that affected the result. Capabilities consume this session instead of creating their own stores or rescanning the Skill tree.

## Self-Evolution

Agent-created Skills can opt into updates:

```toml
agent_created = true
agent_can_update = true
```

Updates use an evidence-based loop:

```text
create candidate -> validate -> evaluate -> promote -> rollback
```

```bash
super-agent skills evolve \
  --config agent.toml \
  --name concise \
  --goal "make answers clearer" \
  --cases evaluation-cases.json
```

Candidates are isolated from active Skills. Promotion requires a passing evaluation and an unchanged parent version; every promoted revision can be rolled back. `v0.0.25` supports this complete loop for instruction-based Skills. Unified evolution for every Skill type is planned for `v0.0.26`.

Freshness does not call a model. It is derived from runtime evaluation records using quality, recency, frequency, token cost, latency, reliability, replacement behavior, and sample confidence.

## Multi-Agent Composition

Agent relationships live in readable Python code rather than TOML:

```python
from super_agent import Agent

main = Agent.load_from_config_file("agents/main.toml")
coder = Agent.load_from_config_file("agents/coder.toml")
reviewer = Agent.load_from_config_file("agents/reviewer.toml")

main.add_subagent(coder, name="coder", triggers=["code", "implement"])
main.add_subagent(reviewer, triggers=["review"])

result = main.run("Implement and review this feature")
```

If `name` is omitted, names are generated as `subagent01`, `subagent02`, and so on. Nested and cyclic Agent graphs produce clear warnings but are not forcibly stopped; workflow rules decide when execution ends.

## Runtime State

All runtime-owned files have explicit locations under the configured memory root:

```text
.super-agent/memory/
  runs/          ordered events, snapshots, and runtime locks
  disclosure/    central Skill index, cache, and disclosure history
  evaluations/   canonical Skill and Capability evaluation records
  derived/       rebuildable freshness statistics
  evolution/     candidates, reports, revisions, and rollbacks
```

The runtime lock captures the effective Provider, Capability versions, Skill versions, and Skill directory hashes for each run without storing secret values.

## Documentation

- [Getting Started](docs/getting-started.md)
- [Architecture](docs/architecture.md)
- [Skills and Progressive Disclosure](docs/skills.md)
- [Runtime, Workflows, Tracing, and Multi-Agent](docs/runtime.md)
- [Evaluation, Freshness, Memory, and Evolution](docs/evolution.md)
- [CLI Reference](docs/cli.md)
- [Configuration](docs/configuration.md)
- [macOS App](docs/macos.md)
- [Roadmap](docs/roadmap.md)

## Development

Run the Python test suite:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Check Python imports and the Swift frontend:

```bash
PYTHONPATH=src python3 -m compileall -q src
swift build --package-path src/frontend/mac
```

The public Python API is exported from `super_agent`. Internal modules intentionally have no compatibility facades during the `0.0.x` series.

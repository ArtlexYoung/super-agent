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

Persist a multi-turn conversation only when you need one:

```python
agent = Agent()
conversation = agent.create_conversation()
agent.run("Remember that my project uses Python", conversation_id=conversation.conversation_id)
result = agent.run("Which language does it use?", conversation_id=conversation.conversation_id)
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

`RuntimeSession` is the single context for a run. It holds one `RunIdentity`, one centralized `RuntimeStore`, one Skill index, the progressive-disclosure session, and every Skill or Capability that affected the result. Capabilities consume this session instead of creating their own stores or rescanning the Skill tree.

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

Candidates are isolated from active Skills. Promotion requires a passing evaluation and an unchanged parent version; every promoted revision can be rolled back. The current loop is complete for instruction-based Skills; unified evolution for every Skill type is planned for `v0.0.30`.

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

All mutable runtime state uses one storage backend and one semantic API. The default needs no dependency or configuration:

```toml
[storage]
backend = "jsonl"
path = ".super-agent"
```

JSONL stores one readable canonical event stream per user under `.super-agent/users/<user-hash>/events.jsonl`. Conversations, runs, evaluations, memory, usage habits, Skill freshness, evolution evidence, and disclosure history are isolated by user and Agent inside that stream. `RuntimeStore` derives domain views from those events; the progressive-disclosure cache and evolution workspace remain rebuildable, user-scoped local artifacts.

SQLite is the optional standard-library backend for concurrent local use. It keeps the same Runtime semantics and still adds no package dependency:

```toml
[storage]
backend = "sqlite"
path = ".super-agent"
```

The database is stored at `.super-agent/events.sqlite3` in WAL mode. JSONL remains the default because it is directly readable and requires no database file.

For shared deployments, install only the database driver you use. Connection URLs stay in environment variables and are never written to TOML:

```bash
python3 -m pip install 'super-agent[postgresql]'
export SUPER_AGENT_POSTGRESQL_URL='postgresql://user:password@host/super_agent'
```

```toml
[storage]
backend = "postgresql"
path = ".super-agent"
```

MySQL works the same way with `super-agent[mysql]` and `SUPER_AGENT_MYSQL_URL`. Set `url_env` only when you want a different environment-variable name. With a remote backend, `path` still owns local disclosure caches and evolution workspaces; canonical events live in the database.

Every state-sensitive Python and CLI operation accepts a user identity. Omitting it uses the zero-configuration `local` user:

```bash
super-agent run --user-id alice --conversation-id project-a "Continue the task"
super-agent conversations list --user-id alice
```

Copy selected users between any configured backends without changing domain data:

```bash
super-agent storage copy \
  --config agent.toml \
  --to-backend sqlite \
  --to-path .super-agent-sqlite \
  --user-id alice
```

For a remote destination, use `--to-backend mysql` or `postgresql` and optionally pass `--to-url-env CUSTOM_DATABASE_URL`.

The runtime lock is stored as a run event. It captures the effective Provider, storage backend, Capability versions, Skill versions, and Skill directory hashes without storing secret values.

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

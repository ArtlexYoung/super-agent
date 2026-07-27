# Super Agent

[Chinese documentation](README_cn.md)

> Skill is all you need.

Super Agent is a **simple, lightweight, self-evolving, skill-first Agent runtime**.

It explores one idea: prompts, tools, memory, workflows, models, and other Agent behavior can all be declared as Skills, progressively disclosed, executed by Capabilities, evaluated from real runs, and improved over time.

The project is currently experimental (`0.0.x`). It favors a small, inspectable runtime and explicit breaking changes over premature API compatibility.

## Why Super Agent

- **Zero-configuration start**: `Agent()` and the CLI run immediately with a local mock model.
- **One Skill format**: prompt, MCP, memory, workflow, models, and executable mechanisms share one manifest and lifecycle.
- **Progressive disclosure**: the model sees a compact index first and opens only the Skills it needs.
- **One runtime lifecycle**: discovery, disclosure, execution, observation, evaluation, and evolution share one session.
- **Automatic evolution signals**: Runtime turns real failures, quality, freshness, replacement, cost, and latency into deduplicated recommendations.
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

The zero-configuration path discovers model credentials from the environment:

```bash
export OPENAI_API_KEY="..."
super-agent models resolve
super-agent run "Summarize this project"
```

`ANTHROPIC_API_KEY` and `OLLAMA_HOST` are also discovered. The runtime creates an ephemeral mock profile only when it finds no configured model.

Create a model Skill when the model needs a persistent name, description, default status, or routing traits:

```text
skills/model/fast/skill.toml
```

```toml
schema_version = 2
name = "fast"
capability = "model"
description = "Low-latency model for summaries and simple questions"
version = "0.1.0"
triggers = ["fast", "summary"]
agent_can_update = true

[configuration]
provider = "openai-compatible"
model = "gpt-4.1-mini"
api_key_env = "OPENAI_API_KEY"
supports = ["text", "tools", "json"]
purposes = ["summary"]
default = true
```

Model Skills use the same central index, validation, evidence, candidate, promotion, and rollback path as every other Skill. See [Configuration](docs/configuration.md).

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

Stable Skill identities use `capability:name`, such as `prompt:concise`, `memory:default`, `workflow:direct`, or `model:fast`. Configuration-only Skills do not need `SKILL.md`.

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

`Agent.run(...)` creates one internal `TaskRequest` and sends it through `AgentRuntime.run_task(...)`. `RuntimeSession` holds one identity, store, Skill index, progressive-disclosure session, and evidence tracker for that task. Workflow Skills contain only instructions and stopping rules; Runtime owns the model and tool loop.

`CapabilityRegistry` contains only executable Skill handlers. Replace one explicitly with `agent.add_skill_executor(...)`. A handler that must be installed or evolved is a standard `capability` Skill, so it uses the same disclosure, evaluation, promotion, and rollback path as every other Skill. Runtime lifecycle mechanisms are intentionally not replaceable parallel controllers.

## Reproducible Proof

The [v0.0.34 experiment](docs/experiments/v0.0.34.md) and its [generated JSON report](docs/experiments/v0.0.34.json) compare no-Skill, eager, and progressive context and exercise the complete lifecycle plus storage isolation. The proof orchestration was removed from the shipped Runtime after publication, keeping benchmark code out of the user-facing core.

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

The complete Skill directory is the candidate unit, so prompt, memory, workflow, MCP, model, executable Capability, and custom Skills use the same lifecycle. The model returns explicit full-file writes and deletions; Runtime validates paths, identity, permissions, and type-specific configuration before evaluation. Candidates stay isolated from active Skills. Promotion requires a passing evaluation and an unchanged parent version, and every promoted revision can be rolled back.

For model Skills, connection fields remain user-owned by default. Agent evolution may improve descriptions, triggers, strengths, purposes, and routing traits, but may change `provider`, `model`, `base_url`, or `api_key_env` only when the user sets `agent_can_update_connection = true`.

Executable mechanisms use `capability` Skills and the ordinary Skill command:

```bash
super-agent skills evolve \
  --config agent.toml \
  --name capability:careful \
  --goal "reduce failed runs" \
  --cases evaluation-cases.json
```

Freshness does not call a model. It is derived from runtime evaluation records using quality, recency, frequency, token cost, latency, reliability, replacement behavior, and sample confidence.

After each evaluated run, Runtime reviews every updateable Skill, including Skill-backed executable mechanisms. This deterministic step does not call a model and needs no new configuration. It records an evolution recommendation only when the current evidence crosses a quality or efficiency threshold, and the same unchanged evidence cannot create a repeated recommendation.

```bash
super-agent evolution list --config agent.toml
super-agent evolution show --config agent.toml --schedule-id <id> --output json
super-agent evolution create-candidate --config agent.toml --schedule-id <id>
```

Creating a candidate invokes the configured model and stores its exact added, modified, and deleted files. The candidate must still pass the existing isolated evaluation before promotion; scheduling never edits or activates the live target by itself.

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

JSONL stores one readable canonical event stream per user under `.super-agent/users/<user-hash>/events.jsonl`. Conversations, runs, evaluations, memory, usage habits, Skill freshness, evolution recommendations, and disclosure history are isolated by user and Agent inside that stream. `RuntimeStore` derives domain views from those events; the progressive-disclosure cache and evolution workspace remain rebuildable, user-scoped local artifacts.

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

The runtime lock is stored as a run event. It captures the selected model profile and Provider adapter, storage backend, Capability versions, Skill versions, and Skill directory hashes without storing secret values.

## Documentation

- [Getting Started](docs/getting-started.md)
- [Architecture](docs/architecture.md)
- [Capabilities](docs/capabilities.md)
- [Skills and Progressive Disclosure](docs/skills.md)
- [Runtime, Workflows, Tracing, and Multi-Agent](docs/runtime.md)
- [Evaluation, Freshness, Memory, and Evolution](docs/evolution.md)
- [Reproducible v0.0.34 Experiment](docs/experiments/v0.0.34.md)
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

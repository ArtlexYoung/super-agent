# Getting Started

Super Agent is designed to run before you create any configuration.

## Requirements

- Python 3.11 or newer.
- No third-party Python runtime dependencies.
- Node.js 20 or newer and pnpm only when developing the optional Web client.

## Install From the Repository

```bash
python3 -m pip install -e .
```

This installs the `super-agent` command and the `super_agent` Python module.

## Run Without Configuration

```bash
super-agent run "hello"
```

```bash
super-agent
```

The first command runs one prompt. The second starts an interactive conversation. When no model Skill or environment profile is available, the runtime uses its local deterministic mock provider, so both commands work without an API key.

## Use a Real Model

With no model Skill, the automatic resolution order is:

1. `SUPER_AGENT_PROVIDER` and related environment variables.
2. `OLLAMA_HOST`.
3. `OPENAI_API_KEY`.
4. `ANTHROPIC_API_KEY`.
5. The built-in mock provider.

Example:

```bash
export OPENAI_API_KEY="..."
super-agent models list
super-agent models resolve
super-agent run "Explain this repository"
```

The CLI reports environment-variable names but never prints secret values.

For a persistent, described model profile, add `skills/model/<name>/skill.toml` using the example in [Configuration](configuration.md). Model Skills take priority over ephemeral environment profiles. The Web client can create and edit these Skills visually; it stores only credential environment-variable names.

## Open the Web Client

```bash
super-agent serve
```

Open `http://127.0.0.1:8765/`. The same standard-library server hosts the production React build, the Runtime management API, and the AG-UI stream. No Node.js process is needed to use the packaged client.

## Optional SQLite Storage

JSONL is the zero-configuration default. For concurrent local processes, change only the backend name:

```toml
[storage]
backend = "sqlite"
path = ".super-agent"
```

SQLite uses the Python standard library, enables WAL mode, and writes `.super-agent/events.sqlite3`.

## Optional Shared SQL Storage

Install only the backend used by a shared deployment:

```bash
python3 -m pip install 'super-agent[mysql]'
export SUPER_AGENT_MYSQL_URL='mysql://user:password@host/super_agent'
```

```toml
[storage]
backend = "mysql"
path = ".super-agent"
```

PostgreSQL uses `super-agent[postgresql]` and `SUPER_AGENT_POSTGRESQL_URL`. No connection value is stored in the configuration file.

## Create an Editable Project

```bash
super-agent init --path my-agent
```

Generated layout:

```text
my-agent/
  agent.toml
  skills/
    prompt/echo/
    mcp/filesystem/
    memory/default/
    workflow/direct/
```

Run it with:

```bash
super-agent run --config my-agent/agent.toml "echo this briefly"
```

## Use the Python API

```python
from super_agent import Agent

agent = Agent()
result = agent.run("hello")

print(result.text)
print(result.run_id)
```

For stored multi-turn history:

```python
conversation = agent.create_conversation()
agent.run("first turn", conversation_id=conversation.conversation_id)
result = agent.run("second turn", conversation_id=conversation.conversation_id)
```

Pass `user_id` to isolate conversations and every other Runtime state view:

```python
conversation = agent.create_conversation(user_id="alice")
agent.run(
    "private turn",
    user_id="alice",
    conversation_id=conversation.conversation_id,
)
```

Load an explicit project:

```python
from super_agent import Agent

agent = Agent.load_from_config_file("my-agent/agent.toml")
result = agent.run("Summarize this task")
```

## Inspect What Happened

```bash
super-agent skills index --config my-agent/agent.toml --output json
super-agent runs status --config my-agent/agent.toml
super-agent runs explain --config my-agent/agent.toml
super-agent skills freshness --config my-agent/agent.toml
super-agent evolution list --config my-agent/agent.toml
```

`runs explain` now includes the task schedule, model attempts, token and cost estimates, routing evidence, relevant Skill freshness, and automatic evolution state. Use `--output json` for integrations.

The reproducible [v0.0.41 task and evolution proof](experiments/v0.0.41.md) verifies multi-model scheduling, user and Agent isolation, automatic promotion, and regression rollback through the normal Runtime path. The archived [v0.0.34 experiment](experiments/v0.0.34.md) covers progressive context and storage backends.

Continue with [Skills](skills.md), [Architecture](architecture.md), or the [CLI reference](cli.md).

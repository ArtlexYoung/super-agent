# Getting Started

Super Agent can run without a project file. It still requires an explicit model source:
a discovered real model, an environment-selected Provider, or a Provider passed in code.

## Requirements

- Python 3.11 or newer.
- No third-party Python Runtime dependencies.
- Node.js and pnpm only when developing the optional React client.

## Install

```bash
python3 -m pip install -e .
```

This installs the `super-agent` command and the `super_agent` Python module.

## Run a Real Model

Use any one of the supported environment sources:

```bash
export OPENAI_API_KEY="..."
super-agent "Explain this repository"
```

```bash
export ANTHROPIC_API_KEY="..."
super-agent "Explain this repository"
```

```bash
export OLLAMA_HOST="http://127.0.0.1:11434"
export OLLAMA_MODEL="llama3.2"
super-agent "Explain this repository"
```

The first matching environment source becomes the cold-start default. Runtime still
records the selected model and can route later calls from model Skill traits and
user-scoped evidence. A Provider failure is returned to the caller; Runtime does not
silently switch to another model.

## Run an Offline Demo

The deterministic Mock Provider is useful for installation checks and tests. Select it
explicitly:

```bash
SUPER_AGENT_PROVIDER=mock super-agent "hello"
```

Without a model source, `Agent.run(...)` fails with instructions for configuring one.
This keeps development behavior from being mistaken for a successful real model call.

## Chat and Web

Start a stored terminal conversation:

```bash
super-agent
```

Start the local Web and AG-UI server:

```bash
super-agent serve
```

Open `http://127.0.0.1:8765/`. The same standard-library server hosts the React build,
the management API, and `POST /ag-ui`. The Web client includes a native chat and a
CopilotKit example over the same endpoint.

## Use Python

```python
from super_agent import Agent

result = Agent().run("hello")
print(result.text)
```

For a deterministic test, inject the Provider explicitly instead of modifying process
environment:

```python
from super_agent import Agent, MockProvider

agent = Agent(provider=MockProvider("test answer"))
assert agent.run("hello").text == "test answer"
```

For stored multi-turn history, create a conversation in one user scope:

```python
agent = Agent()
alice = agent.for_user("alice")
conversation = alice.conversations.create("Project notes")
alice.run("first turn", conversation_id=conversation.conversation_id)
result = alice.run("second turn", conversation_id=conversation.conversation_id)
```

## Create an Editable Project

```bash
super-agent init --path my-agent
```

The command creates only an `agent.toml` and one prompt Skill. Built-in memory, workflow,
and planner Skills remain available through the same progressive index, so the generated
project does not copy configuration it does not need.

```bash
SUPER_AGENT_PROVIDER=mock \
  super-agent run --config my-agent/agent.toml "answer briefly"
```

## Choose Storage

Readable JSONL is the default. SQLite needs no extra dependency and is better for
concurrent local processes:

```toml
[storage]
backend = "sqlite"
path = ".super-agent"
```

MySQL and PostgreSQL are optional extras for shared services:

```bash
python3 -m pip install 'super-agent[postgresql]'
export SUPER_AGENT_POSTGRESQL_URL='postgresql://user:password@host/super_agent'
```

Connection values stay in environment variables, not TOML. See
[Configuration](configuration.md) for backend details.

## Inspect a Run

```bash
super-agent runs status
super-agent runs explain
super-agent skills index --output json
super-agent skills freshness
super-agent evolution list
```

Continue with [Architecture](architecture.md), [Skills](skills.md), or the
[CLI reference](cli.md).

# Configuration

Configuration is optional. `Agent()` checks `SUPER_AGENT_CONFIG`, then `agent.toml`, then
uses an in-memory default. Model configuration still must come from the environment, a
model Skill, or Python.

## Minimal File

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

Unknown tables and fields are rejected. There is no migration or old-field conversion.

`agent.skills` pins ordinary Skills to every run. `disabled_skills` excludes a type or a
specific `type:name`. Scene visibility and subagent links are configured in Python because
code is clearer for dynamic composition.

## Models

The shortest model setup uses environment variables:

```text
OPENAI_API_KEY and optional SUPER_AGENT_MODEL
ANTHROPIC_API_KEY and optional SUPER_AGENT_MODEL
OLLAMA_HOST and optional OLLAMA_MODEL
```

The explicit generic form uses `SUPER_AGENT_PROVIDER`, `SUPER_AGENT_MODEL`,
`SUPER_AGENT_BASE_URL`, and `SUPER_AGENT_API_KEY_ENV`. Provider names are `mock`,
`openai-compatible`, and `anthropic-compatible`.

For persistent model metadata, create a model Skill:

```toml
type = "model"
description = "Low-latency model for summaries"

[configuration]
provider = "openai-compatible"
model = "gpt-4.1-mini"
api_key_env = "OPENAI_API_KEY"
supports = ["text", "tools"]
purposes = ["summary"]
strengths = ["low-latency"]
default = true
quality_score = 0.8
expected_latency_ms = 500
agent_can_update_connection = false
```

Secrets never belong in a Skill. If model Skills exist, they are the complete configured
set; environment discovery is not mixed in. At most one can be the default. Failure never
causes an implicit switch to another profile.

The default model owns the task loop. Every ready non-default model is available through
the checked `use_model` tool, including its description, `supports`, `purposes`, and
`strengths`. The default model chooses whether to delegate and must provide the target,
subtask prompt, and reason explicitly. Delegation returns a tool result; it does not change
the model used by later task turns.

## Storage

```toml
[storage]
backend = "jsonl" # jsonl, sqlite, mysql, postgresql
path = ".super-agent"
# url_env = "DATABASE_URL"
```

JSONL is the readable, dependency-free default. SQLite uses the standard library. Install
`super-agent[mysql]` or `super-agent[postgresql]` only for those backends. Remote URLs are
read from the named environment variable.

The Python library ignores storage until `use_storage=True` or `storage=` is passed. CLI
and Web adapters enable it explicitly. Changing a live Agent's storage configuration is
rejected; use `data storage copy` and restart.

## Code-First Integration

Provider objects, action authority, MCP implementations, secret lookup, storage injection,
and subagent graphs are Python choices. Scene selection belongs to each run:

```python
from core.checks import ActionEffect, ActionMode, ActionRules
from core.skill_use.mcp import StdioMcpServer
from super_agent import Agent

agent = Agent(action_rules=ActionRules(ActionMode.READ_ONLY))
agent.add_tool(
    "filesystem",
    StdioMcpServer("npx", arguments=("-y", "@modelcontextprotocol/server-filesystem")),
    effects=(ActionEffect.READ,),
)
agent.add_subagent(Agent(), name="worker")
agent.run("Inspect this repository", skill="code")
```

An MCP Skill names a code-registered server; executable commands in Skill TOML are
rejected. This keeps untrusted content separate from process and network authority.

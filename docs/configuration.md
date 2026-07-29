# Configuration

Configuration is optional. `Agent()` checks `SUPER_AGENT_CONFIG`, then `agent.toml` in
the current directory, then uses an in-memory default. Missing model configuration is not
silently replaced: a model source must come from the environment or application code.
The Python library does not open storage unless `use_storage=True` or `storage=` is
supplied. CLI and Web entry points explicitly enable the configured backend.

## Minimal `agent.toml`

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

## Agent Fields

- `name`: stable Agent name used in run and storage scopes.
- `system`: smallest instruction shared by every task. Put specialized behavior in Skills.
- `skills`: Skill keys or unambiguous names pinned to every task. At most one may be a
  `scene:*` key.
- `disabled_skills`: types, keys, or unambiguous names excluded from selection.
- `max_agent_chain_depth`: optional positive warning threshold. Omit it for no threshold.

Memory, workflow, planning, MCP, and models are Skill types, not Agent fields. Subagent
links are Python composition and are never declared in TOML:

```python
main.add_subagent(worker, name="worker", triggers=["implement"])
```

## Skill Paths

`[paths].skills` is an array of project Skill roots relative to `agent.toml`. Core scans
them recursively. User overlays and built-in Skills are added centrally; they do not need
extra path entries.

```toml
[paths]
skills = ["skills", "../shared-skills"]
```

Stable Skill keys use `type:name`. A bare name is accepted only when it is unambiguous.
`disabled_skills = ["mcp"]` disables a whole type, while
`disabled_skills = ["memory:default"]` disables one Skill.

## Task Scene Selection

No scene setting is required. Runtime automatically chooses the `code` scene when its
manifest triggers match a coding prompt and otherwise uses the default `common` scene.
Pin one scene only when an Agent should always use that task chain:

```toml
[agent]
skills = ["scene:code"]
```

For a one-run override, use `Agent.run(..., scene="code")` or CLI `--scene code` instead
of changing persistent configuration. Explicitly pinned memory, planner, or workflow
Skills replace that type from the selected scene. Configuring two scenes is rejected;
Runtime never chooses one by list order.

## Model Environment

The shortest real-model setup uses one recognized environment source:

```text
OLLAMA_HOST and optional OLLAMA_MODEL
OPENAI_API_KEY and optional SUPER_AGENT_MODEL
ANTHROPIC_API_KEY and optional SUPER_AGENT_MODEL
```

The generic explicit form is:

```text
SUPER_AGENT_PROVIDER=mock|openai-compatible|anthropic-compatible
SUPER_AGENT_MODEL=<model name>
SUPER_AGENT_BASE_URL=<optional URL>
SUPER_AGENT_API_KEY_ENV=<optional environment variable name>
```

`SUPER_AGENT_PROVIDER=mock` is the only environment path to the built-in Mock Provider.
OpenAI, Anthropic, and explicit Mock profiles declare their implemented text and tool
protocols. An automatically discovered Ollama profile declares text only because local
model tool support cannot be inferred. No model is created when all model sources are
absent.

## Model Skill

Use a model Skill for a persistent description and routing traits:

```toml
# skills/model/fast/skill.toml
schema_version = 3
name = "fast"
type = "model"
description = "Low-latency model for summaries"
version = "0.1.0"
triggers = ["summary"]
agent_created = false
agent_can_update = false

[configuration]
provider = "openai-compatible"
model = "gpt-4.1-mini"
api_key_env = "OPENAI_API_KEY"
supports = ["text", "tools", "json"]
purposes = ["summary"]
strengths = ["low-latency"]
default = true
quality_score = 0.8
expected_latency_ms = 500
input_cost_per_million = 0.4
output_cost_per_million = 1.6
agent_can_update_connection = false
```

`provider` and `model` are required. Supported Provider names are `mock`,
`openai-compatible`, and `anthropic-compatible`. `base_url` and `api_key_env` are
optional when defaults are valid. Secret values never belong in a Skill.

If any enabled model Skill exists, those Skills are the complete model set; ephemeral
environment profiles are not mixed in. At most one model Skill may set `default = true`.
That flag is a cold-start preference, not permission to switch after a failed call.

`agent_can_update` controls ordinary Skill evolution. Model connection fields remain
user-owned unless `agent_can_update_connection = true` was already granted.

## Storage

```toml
[storage]
backend = "jsonl" # jsonl, sqlite, mysql, or postgresql
path = ".super-agent"
# url_env = "MY_DATABASE_URL"
```

- `jsonl`: default, readable files, no dependency.
- `sqlite`: `.super-agent/events.sqlite3`, standard library, WAL mode.
- `mysql`: install `super-agent[mysql]`.
- `postgresql`: install `super-agent[postgresql]`.

Remote connection strings are read from `url_env`. When omitted, the backend-specific
default is used. Changing storage on a running Agent is rejected; restart with the new
backend or copy data explicitly with `super-agent storage copy`.

## Code-Only Runtime Options

Options with behavior or authority stay in readable Python code rather than TOML:

```python
from super_agent import (
    ActionEffect,
    ActionMode,
    ActionRules,
    Agent,
    StdioMcpServer,
)

agent = Agent(
    action_rules=ActionRules(ActionMode.READ_ONLY),
    secret_lookup=lambda user_id, name: lookup_secret(user_id, name),
)
agent.add_skill_loader(custom_loader)
agent.add_mcp_server(
    "filesystem",
    StdioMcpServer(
        "npx",
        arguments=("-y", "@modelcontextprotocol/server-filesystem"),
    ),
    effects=(
        ActionEffect.READ,
        ActionEffect.CREATE,
        ActionEffect.UPDATE,
        ActionEffect.DELETE,
        ActionEffect.EXECUTE,
    ),
)
agent.add_subagent(worker, name="worker")
```

Provider instances, custom SkillLoaders, MCP implementations, action authority, secret
lookup, storage object injection, and subagent graphs are deliberately code-first. An MCP
Skill contains only optional `[configuration].server`; executable settings in Skill TOML
are rejected.

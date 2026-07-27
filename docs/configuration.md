# Configuration

Configuration is optional. `Agent()` first checks `SUPER_AGENT_CONFIG`, then `agent.toml` in the current directory, and otherwise creates an in-memory default configuration.

## Minimal `agent.toml`

```toml
[agent]
name = "demo"
system = "You are a concise, helpful agent."
workflow = "direct"
memory = "default"
skills = []

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
```

## Agent Settings

- `name`: run-trace identity for this Agent.
- `system`: base system instruction.
- `workflow`: name of the selected workflow Skill.
- `memory`: name of the selected memory Skill.
- `skills`: explicit Skill names or `capability:name` keys.
- `use_features`: enabled feature names; defaults to `["skill"]`.
- `disable_names`: Capability names, Skill keys, or bare Skill names to disable.
- `max_agent_chain_depth`: optional warning threshold; omission means unlimited depth.

Subagent relationships are never declared in TOML. Use `Agent.add_subagent(...)` in Python.

## Planner Skill

Planning requires no Agent setting. Runtime progressively discloses the built-in `planner:default` Skill as a fallback. A simple task stays on the direct path; a `plan` workflow, a structured or explicitly multi-step prompt, a long prompt, or extra required features enables planning.

`super-agent init` creates an editable, Agent-updateable Planner Skill at `skills/planner/default`. A project Skill with the same `planner:default` key replaces the built-in fallback without adding its path to `agent.toml`. Its optional configuration fields are:

- `max_steps`: maximum accepted plan length; defaults to `6`.
- `minimum_prompt_characters`: deterministic long-prompt threshold; defaults to `320`.
- `planning_terms`: text fragments that explicitly request decomposition.

`SKILL.md` tells the selected model how to produce the plan. Runtime accepts only one JSON object with a non-empty `steps` array. Every step must contain exactly `instruction`, `purpose`, `required_features`, and `subagent`; unknown subagents and plans above `max_steps` are rejected before step execution.

## Model Skills

Supported provider names:

- `mock`
- `openai-compatible`
- `anthropic-compatible`

Persistent model configuration is an ordinary Skill, not an `agent.toml` table:

```toml
schema_version = 2
name = "fast"
capability = "model"
description = "Low-latency model for summaries"
version = "0.1.0"
triggers = ["fast", "summary"]
agent_can_update = true

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

Place it at a path such as `skills/model/fast/skill.toml`. `provider` and `model` are required. All other configuration fields are optional. Local OpenAI-compatible servers may omit `api_key_env` when `base_url` is localhost. Remote OpenAI and Anthropic defaults infer `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` respectively.

Exactly zero or one model Skill may set `default = true`. The default is the deterministic cold-start preference, not a fixed Agent-wide model. For every task and model step, the adaptive task loop filters profiles by required features and scores purpose, prompt traits, quality, latency, and cost. Failed calls automatically try the remaining compatible profiles in score order. If any enabled model Skill exists, model Skills are the complete profile set and environment discovery is not mixed into it.

`agent_can_update` controls whether the Skill may evolve. Connection fields remain user-owned unless `agent_can_update_connection = true`; an Agent cannot grant itself that permission. Descriptions, triggers, supports, purposes, strengths, and numeric routing traits remain evolvable under ordinary Skill permissions.

## Environment Discovery

Generic model environment variables:

```text
SUPER_AGENT_PROVIDER
SUPER_AGENT_MODEL
SUPER_AGENT_BASE_URL
SUPER_AGENT_API_KEY_ENV
```

Automatically recognized environments:

```text
OLLAMA_HOST
OPENAI_API_KEY
ANTHROPIC_API_KEY
```

Environment profiles are ephemeral and used only when no enabled model Skill exists. Selection order is `SUPER_AGENT_PROVIDER`, `OLLAMA_HOST`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, then the built-in mock. Use `super-agent models list` and `super-agent models resolve` to inspect available profiles and the selected default. These commands print environment-variable names but never secret values.

The Web model page manages real model Skills through the same Runtime operations. Metadata is written under the first configured Skill root. The browser sends only an environment-variable name such as `OPENAI_API_KEY`; actual key values remain in the server process environment. For automation, send model Skill metadata as JSON on stdin:

```bash
printf '%s' '{"name":"fast","description":"Fast answer model","provider":"openai-compatible","model":"gpt-4.1-mini","api_key_env":"OPENAI_API_KEY","supports":["text","tools"],"purposes":["answer"],"default":true}' \
  | super-agent models save --config agent.toml --request-stdin

super-agent models remove --config agent.toml --name fast
```

`models save` creates, updates, or atomically renames a model Skill. Include `previous_name` when renaming. It never accepts or stores a secret value; configure the named environment variable separately.

## Paths

`paths.skills` is a list of recursively scanned Skill roots. Relative paths resolve from the configuration file directory.

## Storage

Storage configuration is optional. The defaults are equivalent to:

```toml
[storage]
backend = "jsonl"
path = ".super-agent"
```

Four backends share the exact same `StorageEvent` contract:

| Backend | Intended use | Installation | Default connection variable |
| --- | --- | --- | --- |
| `jsonl` | Readable, zero-configuration local state | Base package | None |
| `sqlite` | Concurrent local processes with WAL transactions | Base package | None |
| `mysql` | Shared service deployment | `super-agent[mysql]` | `SUPER_AGENT_MYSQL_URL` |
| `postgresql` | Shared service deployment | `super-agent[postgresql]` | `SUPER_AGENT_POSTGRESQL_URL` |

Remote drivers are imported only when their backend is selected. Install one with:

```bash
python3 -m pip install 'super-agent[postgresql]'
export SUPER_AGENT_POSTGRESQL_URL='postgresql://user:password@host/super_agent'
```

The default variable name requires no extra TOML. To use a different name, configure only the name, never the secret value:

```toml
[storage]
backend = "postgresql"
path = ".super-agent"
url_env = "MY_DATABASE_URL"
```

MySQL URLs use `mysql://` or `mysql+pymysql://`. Supported query options are `charset`, `connect_timeout`, `read_timeout`, `write_timeout`, `unix_socket`, `ssl_ca`, `ssl_cert`, `ssl_key`, `ssl_verify_cert`, and `ssl_verify_identity`. PostgreSQL URLs are passed directly to psycopg and may use its standard connection options.

`path` is resolved from the configuration file directory. The default JSONL layout is:

```text
.super-agent/
  users/<user-hash>/events.jsonl
  users/<user-hash>/agents/<agent-hash>/cache/
  users/<user-hash>/agents/<agent-hash>/evolution/
```

For SQLite, `path` is still the shared local state directory rather than a database filename:

```text
.super-agent/
  events.sqlite3
  users/<user-hash>/agents/<agent-hash>/cache/
  users/<user-hash>/agents/<agent-hash>/evolution/
```

For MySQL and PostgreSQL, canonical events live in `super_agent_storage_events`. The `path` setting remains the local root for user- and Agent-scoped disclosure caches and evolution workspaces; it is not a database path.

On first connection, Runtime creates `super_agent_storage_schema`, `super_agent_storage_events`, and their indexes. The database user therefore needs schema creation permission initially and `SELECT`, `INSERT`, and `DELETE` afterward. Schema version `1` is recorded centrally. Unknown versions fail before event access; the `0.0.x` series never performs an implicit migration. Full identifiers remain in text columns, while SHA-256 helper columns provide fixed-size indexes without narrowing the storage contract.

The shared storage contract suite runs against a disposable remote database when `SUPER_AGENT_TEST_MYSQL_URL` or `SUPER_AGENT_TEST_POSTGRESQL_URL` is present and its matching driver is installed. These test variables are intentionally separate from normal runtime connection variables.

Conversations, run snapshots, evaluations, memory, usage habits, and disclosure history are semantic views over the canonical event stream. Cache files and evolution workspaces are local artifacts owned by the same user and Agent scope.

## Disable Examples

```toml
[agent]
disable_names = [
  "mcp",
  "memory:default",
  "prompt:experimental",
]
```

`mcp` disables every MCP Skill. `memory:default` disables one stable key. A bare name disables matching Skills unless another enabled Capability with the same name remains selectable.

## Strict Parsing

Configuration and Skill readers reject unsupported fields and schema versions. The `0.0.x` series does not silently convert old configuration names or retain compatibility aliases. The removed `[model]` table is rejected; use a `model` Skill. The removed `paths.memory` setting is also rejected; use `[storage].path`.

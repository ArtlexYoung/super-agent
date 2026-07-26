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

[model]
provider = "auto"
model = ""

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

## Model Settings

Supported provider names:

- `auto`
- `mock`
- `openai-compatible`
- `anthropic-compatible`

Explicit example:

```toml
[model]
provider = "openai-compatible"
model = "gpt-4.1-mini"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
```

Local OpenAI-compatible servers may omit `api_key_env` when `base_url` is localhost.

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

Use `super-agent models resolve` to see the selected settings and their source.

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

Configuration and Skill readers reject unsupported fields and schema versions. The `0.0.x` series does not silently convert old configuration names or retain compatibility aliases. In particular, the removed `paths.memory` setting is rejected; use `[storage].path`.

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
memory = ".super-agent/memory"
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

`paths.memory` is the runtime state root. `RuntimeStatePaths` derives these explicit child locations:

```text
runs/
disclosure/
evaluations/
derived/
evolution/
```

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

Configuration and Skill readers reject invalid types and unsupported schema versions. The `0.0.x` series does not silently convert old configuration names or retain compatibility aliases.

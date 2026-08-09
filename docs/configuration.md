# Configuration

Configuration is optional. Shared Runtime and Agent settings belong only in `common.toml`.
`Agent()` checks `SUPER_AGENT_COMMON_CONFIG`, then `common.toml`, then uses an in-memory
default. Model configuration still must come from the environment, a model Skill, or
Python.

## Minimal File

```toml
schema_version = 1
kind = "common"

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

[storage.audit]
detailed_days = 180
critical_days = 365
```

Unknown tables and fields are rejected. There is no migration or old-field conversion.
Every file declares `schema_version = 1` and its exact `kind`; configuration scopes are
never merged and a file of one kind cannot be loaded as another.

Terminal defaults belong in `cli.toml`, not this file. See [CLI](cli.md). The CLI uses
`--common-config` when an explicit shared configuration path is needed.

Coding workspace behavior belongs in a separate optional `code.toml`:

```toml
schema_version = 1
kind = "code"

[workspace]
root = "."
ignore = [".git", ".super-agent", "node_modules", "__pycache__"]

[actions]
read = "allow"
write = "ask"
execute = "ask"

[verification]
commands = [["python3", "-m", "unittest"], ["git", "diff", "--check"]]
```

Commands are argument arrays, not shell strings. `code.toml` describes requested behavior;
it does not grant process or file authority by itself. The CLI reads it lazily only when
`task:code` is selected; ordinary tasks do not depend on its presence or validity. Reads
follow the configured read setting. With no `code.toml`, the current directory is used and
`.git`, `.super-agent`, `node_modules`, and `__pycache__` are ignored. An explicit `ignore`
list replaces those defaults. Writes, patches, deletion, and numbered verification commands
pass through the central action runner and require terminal confirmation before they run.
Replacing, patching, or deleting an existing file also requires its current SHA-256.
Declared commands start asynchronously with a process ID. Polling returns bounded stdout,
stderr, state, return code, elapsed time, and explicit timeout, output-limit, stop, and decode
facts. Stopping is a separate checked action. The model cannot supply executable arguments.
Repository maps are read-only and in-memory. They cap file count, per-file bytes, total bytes,
skipped paths, and Python symbols; exceeding a limit fails explicitly. A refresh hashes every
bounded file but only reparses files whose content hash changed.
For a direct verification loop, `run_declared_check` waits for one configured command and
returns `passed = true` only for exit code zero. The model must make and verify any repair as
separate explicit actions.

The progressive disclosure core uses a 24,000-character per-run context budget by default.
The budget is shared across model context, tool results, memory context, subagent results,
and reference reads. It is a Runtime bound rather than a separate Skill setting, so optional
features cannot create competing context policies.

`agent.skills` pins ordinary Skills to every run. `disabled_skills` excludes a type or a
specific `type:name`. Task selection is per run, while subagent links are configured in
Python because code is clearer for dynamic composition.

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

[storage.audit]
detailed_days = 180 # six months by default
critical_days = 365 # twelve months by default
```

JSONL is the readable, dependency-free default. SQLite uses the standard library. Install
`super-agent[mysql]` or `super-agent[postgresql]` only for those backends. Remote URLs are
read from the named environment variable.

The Python library ignores storage until `use_storage=True` or `storage=` is passed. CLI
and Web adapters enable it explicitly. Changing a live Agent's storage configuration is
rejected; use `data storage copy` and restart.

Runtime audit content is bounded by explicit cleanup. Detailed events include model turns,
tool calls, and Skill disclosure paths and use `detailed_days`. Critical events include run
completion, checked actions, learning, and Skill changes and use `critical_days`. Canonical
events keep complete model text, tool payloads, prompts, and errors so internal learning and
review do not lose evidence. Run status, explanation, export, and Web views dynamically
replace those fields with SHA-256 and size summaries by default. CLI callers must add
`--include-sensitive` to request complete values. Dynamic redaction is not encryption; access
to JSONL files or database tables must be protected separately. Conversation and long-term
memory are active state rather than disposable audit logs.

Cleanup is preview-only unless `--apply` is supplied:

```bash
super-agent data storage prune --common-config common.toml --user-id alice --output json
super-agent data storage prune --common-config common.toml --user-id alice --apply
```

Unknown event types and state streams are protected and reported instead of being guessed or
deleted. The cleanup itself writes an `audit.pruned` event for each changed Agent scope.

## Code-First Integration

Provider objects, action authority, MCP implementations, secret lookup, storage injection,
and subagent graphs are Python choices. Task Skill selection belongs to each run:

```python
from core.checks import ActionEffect, ActionMode, ActionRules
from skill.runtime.mcp import StdioMcpServer
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

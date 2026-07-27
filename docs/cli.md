# CLI Reference

## Conversation

```bash
super-agent
super-agent chat --config agent.toml
super-agent chat --config agent.toml --user-id alice --conversation-id <id>
super-agent run "hello"
super-agent run --config agent.toml --output json "hello"
super-agent run --user-id alice --conversation-id <id> "continue"
```

`run --output` accepts `text`, `json`, or `jsonl`. Use `--request-stdin` with JSON input for desktop and service integrations.

## Conversation Management

```bash
super-agent conversations list --config agent.toml --user-id alice
super-agent conversations create --config agent.toml --user-id alice --title "Project"
super-agent conversations show --config agent.toml --user-id alice --conversation-id <id>
super-agent conversations rename --config agent.toml --user-id alice --conversation-id <id> --title "New title"
super-agent conversations clear --config agent.toml --user-id alice --conversation-id <id>
super-agent conversations delete --config agent.toml --user-id alice --conversation-id <id>
```

Conversation history is loaded by Runtime. Do not combine `conversation_id` with an explicit `messages` array in the stdin protocol.

## Project Initialization

```bash
super-agent init --path my-agent
```

Initialization writes files only when they are missing.

## Model Profiles

```bash
super-agent models list
super-agent models list --output json
super-agent models list --config agent.toml --output json
super-agent models resolve
super-agent models resolve --config agent.toml --output json
```

The commands list model Skills when the project contains them. Otherwise they list ephemeral environment profiles or the built-in mock. Output includes connection environment-variable names and readiness, never secret values.

## Skill Inspection

```bash
super-agent skills list --config agent.toml
super-agent skills index --config agent.toml --output json
super-agent skills validate --config agent.toml
super-agent skills explain --config agent.toml --prompt "research this"
super-agent skills freshness --config agent.toml
```

## Skill Composition

```bash
super-agent skills graph --config agent.toml --name research
super-agent skills lock --config agent.toml --name research --output skill.lock
```

Repeat `--name` to resolve more than one requested Skill.

## Skill Evolution

```bash
super-agent skills propose --config agent.toml --name prompt:concise --goal "make it clearer"
super-agent skills evaluate --config agent.toml --candidate-id <id> --cases cases.json
super-agent skills promote --config agent.toml --candidate-id <id>
super-agent skills evolve --config agent.toml --name memory:default --goal "improve recall" --cases cases.json
super-agent skills rollback --config agent.toml --name memory:default
```

Bare names are accepted when unique. Use `capability:name` or `--capability <name>` when creating a non-prompt Skill or when multiple Capabilities contain the same name.

## Skill Packages

```bash
super-agent skills pack --config agent.toml --name research --output research.zip
super-agent skills install --config agent.toml --source ./research.zip
super-agent skills update --config agent.toml --name research --source ./new-research
super-agent skills remove --config agent.toml --name research
```

Install and update accept `--expected-sha256`.

Executable mechanisms are standard `capability` Skills:

```bash
super-agent skills install --config agent.toml --source ./careful-controller
super-agent skills evolve --config agent.toml --name capability:careful \
  --goal "reduce failures" --cases cases.json
super-agent skills rollback --config agent.toml --name capability:careful
```

There is no separate Capability package or command namespace.

## Automatic Evolution Inspection

```bash
super-agent evolution list --config agent.toml --user-id alice
super-agent evolution list --config agent.toml --user-id alice --decision candidate_recommended --output json
super-agent evolution show --config agent.toml --user-id alice --schedule-id <id> --output json
```

`list` and `show` are read-only and never call a model. Runtime owns recommendation, candidate creation, evaluation, promotion, monitoring, and rollback after normal task evaluation. Manual experiments remain available under `skills`.

## Memory

```bash
super-agent memory habits --config agent.toml
super-agent memory list --config agent.toml --scope agent
super-agent memory add --config agent.toml --text "Remember this" --scope agent
super-agent memory recall --config agent.toml --query "this" --scope agent --limit 5
super-agent memory forget --config agent.toml --item-id <id>
super-agent memory consolidate --config agent.toml
```

## Run Inspection

```bash
super-agent runs status --config agent.toml
super-agent runs status --config agent.toml --limit 10 --output json
super-agent runs explain --config agent.toml --run-id <run-id>
super-agent runs export --config agent.toml --run-id <run-id> --output run.json
super-agent runs feedback --config agent.toml --run-id <run-id> --score 0.8 --reason "Useful"
```

When `--run-id` is omitted, explain and export use the latest run. Feedback scores are explicit model-routing quality evidence and must be between `0` and `1`.

## Storage Copy

```bash
super-agent storage copy \
  --config agent.toml \
  --to-backend sqlite \
  --to-path .super-agent-sqlite \
  --user-id local
```

The source backend, path, and connection environment name come from the resolved Agent configuration. Repeat `--user-id` to copy more users. Existing identical event IDs are skipped, while conflicting content fails clearly. Relative destination paths resolve from the source configuration directory.

Remote destinations use the same command:

```bash
super-agent storage copy \
  --config agent.toml \
  --to-backend postgresql \
  --to-url-env ARCHIVE_DATABASE_URL \
  --user-id alice
```

`--to-path` defaults to `.super-agent-copy`; remote event storage does not use it. Database URLs are read from the selected environment variable and are never accepted as command arguments.

All commands that read or write user Runtime state accept `--user-id`; the default is `local`. This includes run, chat, conversations, memory, run inspection, Skill inspection/evolution, and autonomous evolution recommendations.

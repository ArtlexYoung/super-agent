# CLI Reference

A real run needs a model source. Configure one through the environment or a model Skill;
use `SUPER_AGENT_PROVIDER=mock` only for an explicit offline demo.

## Start and Run

```bash
super-agent init --path my-agent
super-agent "hello"
super-agent
super-agent chat --config agent.toml --user-id alice --conversation-id <id>
super-agent run --config agent.toml --user-id alice --output json "hello"
```

A bare prompt is the short one-shot form and a bare command starts interactive chat.
`run --output` accepts `text`, `json`, or `jsonl`. Use `--request-stdin` for a
JSON request containing `prompt`, optional messages, `user_id`, and
`conversation_id`. Initialization writes only missing files.

## Models

```bash
super-agent models list --config agent.toml --user-id alice --output json
super-agent models resolve --config agent.toml --user-id alice
printf '%s' '<model-skill-json>' \
  | super-agent models save --config agent.toml --user-id alice --request-stdin
super-agent models remove --config agent.toml --user-id alice --name fast
```

`list` and `resolve` show enabled model Skills or environment-discovered profiles.
`save` writes a user model Skill; it stores an environment-variable name, never a secret
value. No command creates an implicit Mock Provider.

## Skills

Inspect the shared progressive index:

```bash
super-agent skills list --config agent.toml --user-id alice
super-agent skills index --config agent.toml --user-id alice --output json
super-agent skills validate --config agent.toml --user-id alice
super-agent skills explain --config agent.toml --user-id alice --prompt "research this"
super-agent skills freshness --config agent.toml --user-id alice
super-agent skills graph --config agent.toml --user-id alice --name prompt:research
super-agent skills lock --config agent.toml --user-id alice \
  --name prompt:research --output skill.lock
```

Use `type:name` whenever a bare name could be ambiguous.

Manage passive packages:

```bash
super-agent skills pack --config agent.toml --name prompt:research --output research.zip
super-agent skills install --config agent.toml --source ./research.zip
super-agent skills update --config agent.toml --name prompt:research --source ./new-research
super-agent skills remove --config agent.toml --name prompt:research
```

Install and update accept `--expected-sha256`. They write the user overlay only and never
load executable Python. Custom SkillRunners are registered in application code.

Run manual evolution through the same candidate lifecycle used by automation:

```bash
super-agent skills propose --config agent.toml --name prompt:concise --goal "make it clearer"
super-agent skills evaluate --config agent.toml --candidate-id <id> --cases cases.json
super-agent skills promote --config agent.toml --candidate-id <id>
super-agent skills rollback --config agent.toml --name prompt:concise
super-agent evolution list --config agent.toml --user-id alice --output json
super-agent evolution show --config agent.toml --user-id alice \
  --evolution-id <id> --output json
```

## Conversations and Memory

```bash
super-agent conversations list --config agent.toml --user-id alice
super-agent conversations create --config agent.toml --user-id alice --title "Project"
super-agent conversations show --config agent.toml --user-id alice --conversation-id <id>
super-agent conversations rename --config agent.toml --user-id alice \
  --conversation-id <id> --title "New title"
super-agent conversations clear --config agent.toml --user-id alice --conversation-id <id>
super-agent conversations delete --config agent.toml --user-id alice --conversation-id <id>

super-agent memory habits --config agent.toml --user-id alice
super-agent memory list --config agent.toml --user-id alice --type long-term
super-agent memory add --config agent.toml --user-id alice \
  --type long-term --text "Prefer concise answers" --scope agent
super-agent memory add --config agent.toml --user-id alice \
  --type temporary --conversation-id <id> --text "This task uses Python 3.12"
super-agent memory recall --config agent.toml --user-id alice \
  --type temporary --conversation-id <id> --query "Python" --limit 5
super-agent memory forget --config agent.toml --user-id alice \
  --conversation-id <id> --item-id <memory-id>
super-agent memory consolidate --config agent.toml --user-id alice --type long-term
```

Conversation history is owned by Core. Do not combine a conversation ID with an explicit
message list. `memory add` defaults to `long-term`; `temporary` operations require a
conversation ID. Memory commands require an enabled memory Skill and use the same action
rules as Agent runs.

## Runs and Storage

```bash
super-agent runs status --config agent.toml --user-id alice --output json
super-agent runs explain --config agent.toml --user-id alice --run-id <run-id>
super-agent runs export --config agent.toml --user-id alice \
  --run-id <run-id> --output run.json
super-agent runs feedback --config agent.toml --user-id alice \
  --run-id <run-id> --score 0.8 --reason "Useful"

super-agent storage copy --config agent.toml --to-backend sqlite \
  --to-path .super-agent-sqlite --user-id alice
```

Explain includes scheduling reasons, model evidence, Skill freshness, and evolution
results. Feedback must be between `0` and `1`. Storage copy skips identical event IDs
and fails on conflicting content. Remote database URLs are read only from the environment
name supplied with `--to-url-env`.

## Web and AG-UI

```bash
super-agent serve
super-agent serve --config agent.toml --user-id alice
super-agent serve --host 127.0.0.1 --port 9000 \
  --allow-origin http://localhost:5173
```

The Web app is at `/`, AG-UI runs use `POST /ag-ui`, management routes use
`/api/*`, and `GET /health` is a dependency-free health check. The server fixes one
user ID at startup and has no built-in authentication or TLS. Keep it on loopback or put
an authenticated TLS reverse proxy in front of it.

# CLI Reference

The CLI has five top-level groups: `init`, `run`, `skills`, `data`, and `serve`. A bare
prompt is a short form of `run`; no arguments starts interactive chat.

## Run

```bash
super-agent "hello"
super-agent run --config agent.toml --user-id alice "hello"
super-agent run --chat --config agent.toml --user-id alice
super-agent run --scene code --output json "inspect this repository"
```

`--output` accepts `text`, `json`, or event-streaming `jsonl`. `--request-stdin` reads one
JSON object with `prompt` and optional `messages`, `user_id`, `conversation_id`, and
`scene`. Unknown scenes and missing model requirements fail visibly.

## Skills

```bash
super-agent skills list --config agent.toml
super-agent skills index --config agent.toml --output json
super-agent skills validate --config agent.toml
super-agent skills graph --config agent.toml --name prompt:research
super-agent skills freshness --config agent.toml
```

Package commands are `pack`, `install`, `update`, and `remove`. Candidate evolution uses
`propose`, `evaluate`, `promote`, and `rollback`. These commands never execute Python from
a Skill package.

Models are Skills and therefore live below the same group:

```bash
super-agent skills models list --output json
super-agent skills models resolve
printf '%s' '<model-skill-json>' | \
  super-agent skills models save --config agent.toml --request-stdin
super-agent skills models remove --config agent.toml --name fast
```

Saved model Skills contain an environment-variable name, never a secret value. Skill
revision records are available through `skills evolution list` and
`skills evolution show --evolution-id <id>`.

## Data

All persisted user data is under one group:

```bash
super-agent data conversations list --user-id alice
super-agent data conversations create --user-id alice --title Project
super-agent data memory list --user-id alice
super-agent data memory recall --user-id alice --query "response style"
super-agent data runs status --user-id alice --output json
super-agent data runs explain --run-id <id>
super-agent data runs feedback --run-id <id> --score 0.8
super-agent data runs learn --run-id <id>
```

Conversation commands also provide `show`, `rename`, `clear`, and `delete`. Long-term
memory provides `add` and `forget`. Run data provides `export`. All commands are scoped by
user and Agent.

Copy selected users between explicit storage backends with:

```bash
super-agent data storage copy --to-backend sqlite \
  --to-path .super-agent-copy --user-id alice
```

Existing identical events are skipped; conflicting event content fails.

## Web

```bash
super-agent serve
super-agent serve --host 127.0.0.1 --port 9000 --user-id alice
```

The Web app is at `/`, AG-UI is at `POST /ag-ui`, and management routes are under
`/api/*`. The built-in server has no authentication or TLS; keep it on loopback or place
an authenticated TLS proxy in front of it.

# CLI Reference

The CLI has six top-level commands: `setup`, `check`, `run`, `skills`, `data`, and `serve`.
A bare prompt is short for `run`; no arguments start interactive chat.

## Check and Run

```bash
super-agent check
super-agent check --config agent.toml --output json
super-agent "hello"
super-agent run --config agent.toml --user-id alice "hello"
super-agent run --chat --save --config agent.toml --user-id alice
super-agent run --scene code --output json "inspect this repository"
```

`check` is read-only. It validates configuration, the central Skill index, configured
references, and default model readiness without opening storage or calling a model.

`run --output` accepts `text`, `json`, or streaming `jsonl`. Text output explains the
actual model, scene, workflow, Skills, stop reason, and run ID. `--request-stdin` reads a
JSON object with `prompt` and optional `messages`, `user_id`, `conversation_id`, and
`scene`. One-shot runs and chat are file-free by default. `--save` explicitly enables the
configured storage; supplying a conversation ID also makes that requirement explicit.

## Skills

```bash
super-agent skills list --config agent.toml
super-agent skills index --config agent.toml --output json
super-agent skills validate --config agent.toml
super-agent skills graph --config agent.toml --name prompt:research
super-agent skills freshness --config agent.toml
```

Passive package commands are `pack`, `install`, `update`, and `remove`. They validate
paths, identities, and hashes and never execute Python from a Skill package.

Models are Skills and live under the same group:

```bash
super-agent skills models list --output json
super-agent skills models resolve
super-agent skills models save --config agent.toml --request-stdin < model.json
super-agent skills models remove --config agent.toml --name fast
```

Saved model Skills name an environment variable; they never contain its secret value.

Skill content changes use separate operations:

```bash
super-agent skills propose-change --name prompt:research --goal "make it clearer"
super-agent skills test-change --change-id <id> --cases cases.json
super-agent skills apply-change --change-id <id>
super-agent skills undo-change --change-id <id>
super-agent skills list-changes
```

Proposal and testing do not activate content. Application requires the latest matching
test to pass. Undo restores or removes the exact user overlay created by that change.

## Data

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
memory provides `add` and `forget`. Run data provides `export`. Every operation is scoped
by user and Agent.

Copy selected users between explicit storage backends with:

```bash
super-agent data storage copy --to-backend sqlite \
  --to-path .super-agent-copy --user-id alice
```

Identical events are skipped; conflicting content fails.

## Web

```bash
super-agent serve
super-agent serve --host 127.0.0.1 --port 9000 --user-id alice
```

The Web app is at `/`, AG-UI is at `POST /ag-ui`, and management routes are under
`/api/*`. The built-in server has no authentication or TLS; keep it on loopback or use an
authenticated TLS proxy.

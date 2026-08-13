# CLI Reference

The CLI keeps common actions shallow. A prompt runs one task directly; no arguments
start an interactive conversation.

CLI behavior is isolated in optional `cli.toml`; it is never merged with `common.toml`:

```toml
schema_version = 1
kind = "cli"

[run]
user_id = "local"
output = "text"
save = false
show_summary = true
```

The CLI checks `SUPER_AGENT_CLI_CONFIG`, then `cli.toml`, then uses in-memory defaults.
It never creates this file automatically. Inspect or validate it without writing:

```bash
super-agent config show
super-agent config validate --cli-config cli.toml
```

Interactive conversations support `/help`, `/clear`, and `/exit`. Clearing starts a new
conversation when saving is enabled; it never deletes the previous conversation.

## Check and Run

```bash
super-agent check
super-agent check --common-config common.toml --output json
super-agent "hello"
super-agent --common-config common.toml --user-id alice "hello"
super-agent --save --common-config common.toml --user-id alice
super-agent --skill code --code-config code.toml --output json "inspect this repository"
```

`check` is read-only. It validates configuration, the central Skill index, configured
references, and default model readiness without opening storage or calling a model.

`--output` accepts `text` or `json`. Text output explains the actual model, task Skill,
workflow, Skills, stop reason, and run ID. One-shot runs and chat are file-free by default. `--save` explicitly enables the
configured storage; supplying a conversation ID also makes that requirement explicit.
Terminal flags override `cli.toml`, including `--no-save` and `--no-show-summary`. Shared
Runtime settings always use `--common-config`; the removed generic `--config` name has no
compatibility alias. Code settings use `--code-config` and are loaded only if `task:code` is
activated. That task adds a bounded directory tree, ranged file reads, search, fixed Git
status and diff reads, plus explicit file writes, structured patches, deletion, and numbered
verification commands. Existing-file changes require the SHA-256 returned by a read. All
non-read tools ask for terminal confirmation through the central action runner; refusal or
EOF blocks the action. Paths outside the configured root and undeclared verification
commands fail visibly. Declared commands run without a shell and expose separate start,
poll, and stop tools with bounded time and output.
The same code Skill can refresh an in-memory repository map. It reports file hashes and
Python AST symbols, reuses unchanged parses, and states when another file type has no parser.
`run_declared_check` is the bounded synchronous form for a single check. Its `passed` value
is derived from the process exit code; a failed check does not trigger an automatic edit.
The Python API exposes `user.runs.list_checkpoints(run_id)` and
`user.runs.resume(run_id, prompt, checkpoint_id=...)`. Resume creates a new run and records
the source run and checkpoint; it does not restore model output.

## Skills

```bash
super-agent skills list --common-config common.toml
super-agent skills index --common-config common.toml --output json
super-agent skills validate --common-config common.toml
super-agent skills graph --common-config common.toml --name prompt:research
super-agent skills freshness --common-config common.toml
```

Passive package commands are `pack`, `install`, `update`, and `remove`. They validate
paths, identities, and hashes and never execute Python from a Skill package.

Models are Skills and live under the same group:

```bash
super-agent skills models list --output json
super-agent skills models resolve
super-agent skills models save --common-config common.toml --request-stdin < model.json
super-agent skills models remove --common-config common.toml --name fast
```

Saved model Skills name an environment variable; they never contain its secret value.

Skill content changes use separate operations:

```bash
super-agent skills changes propose --name prompt:research --goal "make it clearer"
super-agent skills changes test --change-id <id> --cases cases.json
super-agent skills changes apply --change-id <id>
super-agent skills changes undo --change-id <id>
super-agent skills changes list
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

Run status, explanation, and export dynamically redact prompts, model output, tool payloads,
and error messages. The canonical event remains complete. Request the original values only
when needed:

```bash
super-agent data runs explain --run-id <id> --include-sensitive --output json
super-agent data runs export --run-id <id> --include-sensitive --output run.json
```

The Web API does not expose an unredacted run route. Dynamic redaction is not encryption;
direct JSONL or database access still reads complete canonical events.

Copy selected users between explicit storage backends with:

```bash
super-agent data storage copy --to-backend sqlite \
  --to-path .super-agent-copy --user-id alice
```

Identical events are skipped; conflicting content fails.

Preview expired audit events, then apply the exact same plan explicitly:

```bash
super-agent data storage prune --common-config common.toml \
  --user-id alice --output json
super-agent data storage prune --common-config common.toml \
  --user-id alice --apply
```

The command uses `[storage.audit]` retention settings. It never deletes conversation,
memory, habit, evaluation, or unknown event streams. A successful applied cleanup leaves a
small `audit.pruned` record with counts and the settings used.

## Web

```bash
super-agent serve
super-agent serve --host 127.0.0.1 --port 9000 --user-id alice
```

The Web app is at `/`, AG-UI is at `POST /ag-ui`, and management routes are under
`/api/*`. The built-in server has no authentication or TLS; keep it on loopback or use an
authenticated TLS proxy.

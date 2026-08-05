# Super Agent

[中文说明](README_cn.md)

**A simple, lightweight, self-evolving, skill-first agent runtime.**

> Skill is all you need.

Super Agent gives a model a compact Skill index. The model decides what it needs, opens
only that content, and runs the selected instructions or registered tools. Prompts,
task instructions, run policies, memory behavior, tools, and model descriptions all use the same
Skill format and the same progressive disclosure path.

The default Python install has no third-party runtime dependencies. A basic `Agent()` is
stateless and writes no files. Storage, conversations, memory, Skill updates, MCP, and Web
are optional layers that fail clearly when their requirements are missing.

Super Agent is experimental pre-`1.0` software. Breaking changes do not keep compatibility
aliases or migration wrappers.

## Start in One Minute

Python 3.11 or newer is required.

```bash
python3 -m pip install -e .
export OPENAI_API_KEY="..."
super-agent check
super-agent "Explain this repository"
```

`check` only reads configuration, Skills, and model settings. It does not create storage
or call a model. OpenAI-compatible, Anthropic-compatible, and Ollama settings can be
discovered from the environment. An offline smoke test must be explicit:

```bash
SUPER_AGENT_PROVIDER=mock super-agent check
SUPER_AGENT_PROVIDER=mock super-agent "hello"
```

Run `super-agent` without arguments for an interactive conversation. No project files are
generated; add `common.toml`, `cli.toml`, `code.toml`, or local Skills only when needed.

Inside a conversation, use `/help`, `/clear`, or `/exit` for terminal controls.

## Add a Skill

A Skill can be only a directory, a compact manifest, and optional instructions:

```text
skills/prompt/research/
  skill.toml
  SKILL.md
```

```toml
description = "Research a question and report cited findings"
```

The directory name becomes the Skill name, and `type` defaults to `prompt`. There are no
trigger words. The model sees descriptions and decides which Skills to disclose or
activate during its normal turn.

## Use Python

The common module exports one class:

```python
from super_agent import Agent

agent = Agent()
result = agent.run("Explain progressive Skill disclosure")
print(result.text)
```

`Agent` has six direct actions: `run`, `for_user`, `add_subagent`, `add_skill_path`,
`add_tool`, and `add_model`. Advanced contracts are imported from the module that owns
them.

Compose specialized Agents in code. A task Skill selection belongs to one run and does
not silently change later runs:

```python
from super_agent import Agent

main = Agent()
coder = Agent()
main.add_subagent(coder, name="coder", description="Implements and verifies code changes")
result = coder.run("Fix the failing test", skill="code")
```

## Add State Only When Needed

```python
agent = Agent(use_storage=True)
alice = agent.for_user("alice")
conversation = alice.conversations.create("Project")
result = alice.run("Remember my response style", conversation_id=conversation.conversation_id)
alice.runs.learn(result.run_id)
```

Conversation messages are short-term context. Long-term memory stores durable facts,
preferences, and abstractions, and can be explicitly organized or forgotten. User and
Agent scopes isolate conversations, memory, runs, and Skill overlays.

JSONL is the readable default backend. SQLite also uses the standard library. MySQL and
PostgreSQL drivers are optional extras.

Audit records are bounded and configurable. Detailed records are kept for 180 days and
critical records for 365 days by default. Canonical events stay complete for learning and
review, while CLI and Web run views dynamically replace prompts, model output, tool payloads,
and error messages with hashes and size summaries. CLI users can explicitly add
`--include-sensitive` to run status, explanation, or export commands. Dynamic redaction is
not storage encryption, so protect access to the selected backend. Preview cleanup with
`super-agent data storage prune`; add `--apply` when you explicitly want deletion.

## Update a Skill Explicitly

Learning records evaluation, freshness, and model-use evidence. It never changes a Skill.
Skill changes use four visible steps:

```bash
super-agent manage skill-changes propose --name prompt:research --goal "make citations clearer"
super-agent manage skill-changes test --change-id <id> --cases cases.json
super-agent manage skill-changes apply --change-id <id>
super-agent manage skill-changes undo --change-id <id>
```

Proposal and testing cannot activate a candidate. Only `apply` changes the user
overlay, and failed tests block it.

## CLI and Web

```bash
super-agent check
super-agent "one task"
super-agent --skill code "inspect this repository"
super-agent config show
super-agent skills list
super-agent data runs status
super-agent serve
```

One-shot runs are stateless unless `--save` or a conversation ID is explicit. Text runs
print the answer plus the actual model, task Skill, workflow, Skills, stop reason, and run ID.
Use `--output json` for integrations. The React client,
CopilotKit example, and AG-UI endpoint are served at `http://127.0.0.1:8765/`.

Optional `cli.toml` controls terminal defaults only. Shared Runtime settings stay in
`common.toml`, coding workspace settings stay in `code.toml`, and model connections stay
in model Skills or environment variables. These files are validated separately and are
never deep-merged.

The code task exposes a bounded directory tree, ranged UTF-8 file reads, text search, and
fixed-argument Git status and diff reads. File replacement, structured exact patches, and
deletion require the SHA-256 returned by a prior read, so a concurrent change fails instead
of being overwritten. Paths must remain under the configured workspace; ignored, escaping,
oversized, and non-text reads fail visibly. Every non-read tool asks for terminal
confirmation before it runs, and refusal or end-of-input stops the action without a hidden
fallback. Verification commands are declared as argument arrays, then started, polled, or
stopped by ID with explicit time and output limits; shell strings are never accepted.
`refresh_repository_map` provides a bounded path and metadata map and reuses unchanged
symbol parsing within the current run. Python symbols use the standard AST parser; other
file types report no parser rather than receiving guessed symbols.

## Guarantees

- Skills, files, tool output, memory context, and subagent results use one central progressive
  disclosure path. Large content is referenced and read in bounded pages instead of silently
  truncated.
- Routing is model judgment, not keyword matching.
- Reads do not write, and state changes are explicit checked actions.
- Skill content is passive and cannot register code, permissions, or secrets.
- Provider, storage, and optional-feature failures are visible; there is no hidden fallback.
- A basic run does not require storage, memory, conversations, safety rules, or learning.

## Read Next

- [Getting started](docs/getting-started.md)
- [Source tour](docs/source-tour.md)
- [Skills](docs/skills.md)
- [Configuration](docs/configuration.md)
- [Runtime](docs/runtime.md)
- [CLI](docs/cli.md)
- [Learning, memory, and Skill changes](docs/evolution.md)
- [Safety](docs/safety.md)
- [Web](docs/web.md) and [AG-UI](docs/ag-ui.md)

Runnable examples are in `examples/minimal.py`, `examples/custom_skill.py`, and
`examples/team.py`.

## Verify the Repository

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests python3 -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m compileall -q src
pnpm --dir web typecheck
pnpm --dir web lint
pnpm --dir web build
```

For the complete local release gate, including version and package-shape checks, see
[Local release](docs/releasing.md).

The dependency-free comparison runner and its reproducibility contract are documented in
[Benchmarks](docs/benchmarks.md).

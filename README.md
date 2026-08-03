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

Super Agent is experimental `0.0.x` software. Breaking changes do not keep compatibility
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
Use `--output json` or `--output jsonl` for integrations. The React client,
CopilotKit example, and AG-UI endpoint are served at `http://127.0.0.1:8765/`.

Optional `cli.toml` controls terminal defaults only. Shared Runtime settings stay in
`common.toml`, coding workspace settings stay in `code.toml`, and model connections stay
in model Skills or environment variables. These files are validated separately and are
never deep-merged.

The code task exposes bounded UTF-8 file reading and text search. Paths must remain under
the configured workspace; ignored, escaping, oversized, and non-text reads fail visibly.

## Guarantees

- Every Skill type uses one central progressive disclosure path.
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

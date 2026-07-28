# Super Agent

[Chinese documentation](README_cn.md)

> Skill is all you need.

Super Agent is a **simple, lightweight, self-evolving, skill-first Agent runtime**.

It explores one idea: prompts, tools, memory, workflows, models, and other Agent behavior can all be declared as Skills, progressively disclosed, executed by Capabilities, evaluated from real runs, and improved over time.

The project is currently experimental (`0.0.x`). It favors a small, inspectable runtime and explicit breaking changes over premature API compatibility.

## Why Super Agent

- **Zero-configuration start**: `Agent()` and the CLI run immediately with a local mock model.
- **One Skill format**: prompt, MCP, memory, workflow, models, and executable mechanisms share one manifest and lifecycle.
- **Progressive disclosure**: the model sees a compact index first and opens only the Skills it needs.
- **Automatic planning**: complex tasks are decomposed by a progressively disclosed Planner Skill, while simple tasks keep one direct model call.
- **Automatic task scheduling**: Runtime selects compatible models, Skills, and subagents, then learns from user-scoped quality, reliability, latency, and cost evidence.
- **One runtime lifecycle**: discovery, disclosure, execution, observation, evaluation, and evolution share one session.
- **One safety boundary**: Runtime records every declared action effect before a Capability can execute it.
- **Secure defaults**: internal memory and owned Skill evolution stay automatic; external execution and unknown network actions require approval.
- **Automatic evolution loop**: Runtime turns real evidence into candidates, evaluates and promotes them, then monitors and rolls back regressions.
- **Memory that remembers and forgets**: recall also organizes duplicate, outdated, and low-value memory through validated, traceable events.
- **Built-in Web client**: one command serves Chinese conversation, configuration, memory, Skill, model, and run views over the same Runtime state.
- **AG-UI ready**: the dependency-free HTTP/SSE bridge streams canonical Runtime events without creating a second execution path.
- **Code-composed Agents**: create Agents independently and attach them with `Agent.add_subagent(...)`.
- **Standard-library runtime**: the Python core has no third-party runtime dependencies.

## Start in 30 Seconds

Python 3.11 or newer is required. From the repository root:

```bash
python3 -m pip install -e .
super-agent run "hello"
```

No configuration or API key is required. Without a discovered model, Super Agent uses its deterministic local mock provider.

Start an interactive conversation:

```bash
super-agent
```

Use it as a Python library:

```python
from super_agent import Agent

result = Agent().run("Explain progressive Skill disclosure")
print(result.text)
```

Persist a multi-turn conversation only when you need one:

```python
agent = Agent()
conversation = agent.for_user("local").conversations.create()
agent.run("Remember that my project uses Python", conversation_id=conversation.conversation_id)
result = agent.run("Which language does it use?", conversation_id=conversation.conversation_id)
```

Open the built-in Web client:

```bash
super-agent serve
```

Visit `http://127.0.0.1:8765/`. The same server exposes the live AG-UI endpoint at `/ag-ui`; the Web client uses Runtime-backed conversations and configuration rather than browser-owned copies. See [Web Client](docs/web.md) and [AG-UI Bridge](docs/ag-ui.md).

## Use a Real Model

The zero-configuration path discovers model credentials from the environment:

```bash
export OPENAI_API_KEY="..."
super-agent models resolve
super-agent run "Summarize this project"
```

`ANTHROPIC_API_KEY` and `OLLAMA_HOST` are also discovered. The runtime creates an ephemeral mock profile only when it finds no configured model.

Create a model Skill when the model needs a persistent name, description, default status, or routing traits:

```text
skills/model/fast/skill.toml
```

```toml
schema_version = 2
name = "fast"
capability = "model"
description = "Low-latency model for summaries and simple questions"
version = "0.1.0"
triggers = ["fast", "summary"]
agent_can_update = true

[configuration]
provider = "openai-compatible"
model = "gpt-4.1-mini"
api_key_env = "OPENAI_API_KEY"
supports = ["text", "tools", "json"]
purposes = ["summary"]
default = true
```

Model Skills use the same central index, validation, evidence, candidate, promotion, and rollback path as every other Skill. See [Configuration](docs/configuration.md).

The Web client provides a Chinese visual editor for model Skills. It writes model metadata to the current user's Skill overlay and stores only the environment-variable name for a credential, never the credential value.

## Create a Project

Only initialize a project when you want editable configuration or Skills:

```bash
super-agent init --path my-agent
super-agent run --config my-agent/agent.toml "hello"
```

The generated project contains one Agent configuration and example prompt, MCP, memory, workflow, and Planner Skills.

## Create a Skill

A Skill is a directory with a `skill.toml` manifest and optional content files:

```text
skills/prompt/concise/
  skill.toml
  SKILL.md
```

```toml
schema_version = 2
name = "concise"
capability = "prompt"
description = "Answer clearly with minimal wording"
version = "0.1.0"
triggers = ["brief", "concise"]

[entry]
instructions = "SKILL.md"
```

```markdown
Prefer short sentences. Keep only information needed to answer the request.
```

Run a prompt that matches the Skill:

```bash
super-agent run --config agent.toml "Give me a concise explanation"
```

Stable Skill identities use `capability:name`, such as `prompt:concise`, `memory:default`, `workflow:direct`, or `model:fast`. Configuration-only Skills do not need `SKILL.md`.

## How It Works

Super Agent keeps five responsibilities separate:

```text
Provider   provides model intelligence
Runtime    owns the shared lifecycle
Capability executes a mechanism
Skill      carries content and configuration
Agent      composes everything
```

Every run follows one central lifecycle:

```text
discover -> disclose -> execute -> observe -> evaluate -> evolve
```

`Agent.run(...)` creates one internal `TaskRequest` and sends it through `AgentRuntime.run_task(...)`. One adaptive task loop selects an ordered model fallback list, progressively matched Skills, and matching subagents, then advances the model and tool steps. Every reason is stored in `task.scheduled`; each model attempt records selection, completion or failure, latency, estimated tokens, and cost. Workflow Skills contain only instructions and stopping rules.

The built-in `planner:default` Skill keeps planning optional and zero-configuration. Every task uses one `TaskPlan`; simple work gets a deterministic one-step plan without a planning model call. Complex prompts, structured multi-step requests, extra feature requirements, and `plan` workflows ask the selected model for a strict plan. Every step then runs through the same model, Skill, tool, and subagent loop. A project-created `planner:default` replaces the built-in fallback through the same central Skill index.

Routing starts deterministically and uses bounded exploration only after evidence exists for the effective task purpose. Evidence is isolated by user, Agent, model Skill, and purpose. Record an explicit quality score when useful:

Each route exposes its confidence and evidence sufficiency in the run trace. Runtime automatically prefers an evidence-backed model over a low-confidence candidate, or records that it must rely on the ordinary fallback chain. No routing thresholds need to be configured.

```python
alice = agent.for_user("alice")
result = alice.run("Summarize the report")
alice.runs.record_feedback(result.run_id, 0.25, "Missed the conclusion")
```

```bash
super-agent runs feedback --run-id <run-id> --score 0.25 --reason "Missed the conclusion"
```

Within a stored conversation, deterministic correction and repeated-request signals also lower the previous task's quality. Explicit feedback always overrides an implicit signal and no model call is needed to classify either one.

Inspect the same central evidence from the CLI or the task tree in the Web client:

```bash
super-agent runs explain --config agent.toml --run-id <run-id>
super-agent runs explain --config agent.toml --run-id <run-id> --output json
```

The projection includes scheduler reasons, the task plan and completed steps, every step's model and subagent route, model attempts, latency, estimated tokens and cost, learned routing evidence, relevant Skill freshness, and automatic evolution decisions. A child Agent `run_id` can be inspected through the parent project because the lookup remains restricted to the same user and storage backend.

`CapabilityRegistry` contains only executable Skill mechanisms registered by application
code. Replace one explicitly with `agent.add_capability(...)`. Every Capability exposes
only `load_skill(request)` and returns
one `SkillContribution` containing any model context, prompt context, tools, task policy,
and completion recorder it provides. Runtime therefore does not know concrete Memory,
MCP, or Workflow classes. Skill directories are untrusted declarative content and are
never imported or executed as Python.

Every tool declares `read`, `create`, `update`, `delete`, `execute`, `network`, or
`delegate` effects. Runtime checks them through one safety policy before calling the
handler. There is no implicit action declaration: an incomplete Capability fails before
its handler can run. The default `standard` preset keeps internal operations automatic and blocks
risky external actions until approved. See [Runtime Safety](docs/safety.md).

## Reproducible Proof

The [v0.0.53 end-to-end Runtime proof](docs/experiments/v0.0.53.md) and its [generated JSON report](docs/experiments/v0.0.53.json) start the real AG-UI HTTP server and execute one canonical task path. The run organizes and forgets memory during recall, blocks an external Tool before its handler executes, streams the failure as `RUN_ERROR`, and automatically promotes the failed Agent-owned Skill from `0.1.0` to `0.1.1`. The proof is deterministic, local, dependency-free, and requires no API key.

The [v0.0.46 proof](docs/experiments/v0.0.46.md) covers planned model and subagent routing plus Planner/model Skill evolution. The earlier [v0.0.41 proof](docs/experiments/v0.0.41.md) verifies user and Agent evidence isolation plus automatic rollback, while the [v0.0.34 lifecycle experiment](docs/experiments/v0.0.34.md) compares no-Skill, eager, and progressive context. Experiment orchestration remains outside the shipped Runtime so proof code does not become a second execution framework.

## Self-Evolution

Agent-created Skills can opt into updates:

```toml
agent_created = true
agent_can_update = true
```

Updates use an evidence-based loop:

```text
create candidate -> validate -> evaluate -> promote -> rollback
```

```bash
super-agent skills evolve \
  --config agent.toml \
  --name concise \
  --goal "make answers clearer" \
  --cases evaluation-cases.json
```

The complete Skill directory is the candidate unit, so prompt, memory, workflow, MCP,
model, and custom declarative Skills use the same lifecycle. The model returns explicit
full-file writes and deletions; Runtime validates paths, identity, action policy, and
type-specific configuration before evaluation. Candidates stay isolated from active
Skills. Promotion requires a passing evaluation and an unchanged parent version, and
every promoted revision can be rolled back.

For model Skills, connection fields remain user-owned by default. Agent evolution may improve descriptions, triggers, strengths, purposes, and routing traits, but may change `provider`, `model`, `base_url`, or `api_key_env` only when the user sets `agent_can_update_connection = true`.

Executable Capability code is trusted application code and must be registered explicitly
with `Agent.add_capability(...)`. Runtime rejects `capability = "capability"` Skills;
agents may evolve the declarative Skills consumed by registered code, but cannot promote
downloaded or generated Python into the Runtime process.

Freshness does not call a model. It is derived from runtime evaluation records using quality, recency, frequency, token cost, latency, reliability, replacement behavior, and sample confidence.

Memory organization happens during recall. Runtime first ranks candidates and merges exact duplicates deterministically, then asks the selected model for strict `merge`, `supersede`, `archive`, or `forget` operations. Every operation is validated against the recalled candidates and executed through the same Safety policy. Archived and forgotten items leave the active memory view without being physically deleted from the append-only event history. Set `organize_on_recall = false` in a memory Skill only when read-only retrieval is required.

After each evaluated run, Runtime reviews every updateable Agent-owned Skill. The
deterministic evolution scheduler creates at most one recommendation for an unchanged
evidence snapshot. The automatic evolution service then uses the same adaptive model-call
path to create a complete-directory candidate, evaluates it against up to three prompts
from the triggering runs, and promotes it only when the existing evidence gate passes.

```bash
super-agent evolution list --config agent.toml
super-agent evolution show --config agent.toml --evolution-id <id> --output json
```

The `evolution` commands only inspect automatic state. A promoted version is monitored using later real runs: any failure triggers rollback, three healthy samples mark it stable, and an average score below `0.75` after three samples also triggers rollback. Automation errors are recorded in the task trace and never replace the main task result.

## Multi-Agent Composition

Agent relationships live in readable Python code rather than TOML:

```python
from super_agent import Agent

main = Agent("agents/main.toml")
coder = Agent("agents/coder.toml")
reviewer = Agent("agents/reviewer.toml")

main.add_subagent(coder, name="coder", triggers=["code", "implement"])
main.add_subagent(reviewer, triggers=["review"])

result = main.run("Implement and review this feature")
```

If `name` is omitted, names are generated as `subagent01`, `subagent02`, and so on. Nested and cyclic Agent graphs produce clear warnings but are not forcibly stopped; workflow rules decide when execution ends.

## Runtime State

All mutable runtime state uses one storage backend and one semantic API. The default needs no dependency or configuration:

```toml
[storage]
backend = "jsonl"
path = ".super-agent"
```

JSONL stores one readable canonical event stream per user under `.super-agent/users/<user-hash>/events.jsonl`. Conversations, runs, evaluations, memory, usage habits, Skill freshness, evolution recommendations, and disclosure history are isolated by user and Agent inside that stream. `RuntimeStore` owns the common scope and run operations; `store.disclosure` owns cache/history operations and `store.memory` owns memory/habit operations over the same backend. The progressive-disclosure cache and evolution workspace remain rebuildable, user-scoped local artifacts.

Mutable Skills use the same scope. Runtime resolves one central index in `user > project > builtin` order. Installs, model edits, and promoted candidates are written under `.super-agent/users/<user-hash>/agents/<agent-hash>/skills`; shared project Skills remain unchanged. Removing a user Skill reveals the shared Skill below it, while shared Skills cannot be removed through a user-scoped manager.

SQLite is the optional standard-library backend for concurrent local use. It keeps the same Runtime semantics and still adds no package dependency:

```toml
[storage]
backend = "sqlite"
path = ".super-agent"
```

The database is stored at `.super-agent/events.sqlite3` in WAL mode. JSONL remains the default because it is directly readable and requires no database file.

For shared deployments, install only the database driver you use. Connection URLs stay in environment variables and are never written to TOML:

```bash
python3 -m pip install 'super-agent[postgresql]'
export SUPER_AGENT_POSTGRESQL_URL='postgresql://user:password@host/super_agent'
```

```toml
[storage]
backend = "postgresql"
path = ".super-agent"
```

MySQL works the same way with `super-agent[mysql]` and `SUPER_AGENT_MYSQL_URL`. Set `url_env` only when you want a different environment-variable name. With a remote backend, `path` still owns local disclosure caches and evolution workspaces; canonical events live in the database.

Every state-sensitive Python and CLI operation accepts a user identity. Omitting it uses the zero-configuration `local` user:

```bash
super-agent run --user-id alice --conversation-id project-a "Continue the task"
super-agent conversations list --user-id alice
```

Each run resolves that user's Skill index, model profiles, routing evidence, Provider cache, and secrets together. A service can supply secrets without putting values in TOML or Runtime state:

```python
secrets = {
    ("alice", "OPENAI_API_KEY"): "...",
    ("bob", "OPENAI_API_KEY"): "...",
}
agent = Agent(secret_lookup=lambda user_id, name: secrets.get((user_id, name)))
```

The lookup receives only the validated user ID and requested environment-variable name. Its view cannot be enumerated, credential-backed Provider instances are not reused across users, and secret values never enter events or runtime locks. Providers explicitly registered by application code remain application-owned. Without `secret_lookup`, the existing process environment remains the zero-configuration behavior.

Copy selected users between any configured backends without changing domain data:

```bash
super-agent storage copy \
  --config agent.toml \
  --to-backend sqlite \
  --to-path .super-agent-sqlite \
  --user-id alice
```

For a remote destination, use `--to-backend mysql` or `postgresql` and optionally pass `--to-url-env CUSTOM_DATABASE_URL`.

The runtime lock is stored as a run event. It captures the selected model profile and Provider adapter, storage backend, Capability versions, Skill versions, and Skill directory hashes without storing secret values.

## Documentation

- [Getting Started](docs/getting-started.md)
- [Architecture](docs/architecture.md)
- [Capabilities](docs/capabilities.md)
- [Skills and Progressive Disclosure](docs/skills.md)
- [Runtime, Workflows, Tracing, and Multi-Agent](docs/runtime.md)
- [Evaluation, Freshness, Memory, and Evolution](docs/evolution.md)
- [Reproducible v0.0.53 End-to-End Runtime Proof](docs/experiments/v0.0.53.md)
- [Reproducible v0.0.46 Planning and Evolution Proof](docs/experiments/v0.0.46.md)
- [Reproducible v0.0.41 Task and Evolution Proof](docs/experiments/v0.0.41.md)
- [Reproducible v0.0.34 Experiment](docs/experiments/v0.0.34.md)
- [CLI Reference](docs/cli.md)
- [Configuration](docs/configuration.md)
- [Web Client](docs/web.md)
- [AG-UI Bridge](docs/ag-ui.md)
- [Roadmap](docs/roadmap.md)

## Development

Run the Python test suite:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Check Python imports and build the optional Web client:

```bash
PYTHONPATH=src python3 -m compileall -q src
cd web
pnpm lint
pnpm build
```

The public Python API is exported from `super_agent`. Internal modules intentionally have no compatibility facades during the `0.0.x` series.

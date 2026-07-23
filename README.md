# Super Agent

[中文说明](README_cn.md)

> Skill is all you need.

Super Agent is a simple, lightweight, configurable **skill-first agent runtime** built to test whether an agent architecture can be fully skill-based, autonomous, and evolvable.

- **Everything is a Skill**: prompts, tools, memory, and workflows use one format for declaration, composition, and reuse.
- **Fully autonomous**: the agent selects, discloses, and executes capabilities for each task without duplicate application-level orchestration.
- **Fully evolvable**: skills can be evaluated, updated, promoted, and rolled back, while runtime data continuously updates memory and freshness.

The project is currently experimental in the `0.0.x` series. Its priority is a real, verifiable execution loop, not 1.0 API stability.

## Why Super Agent

- **CLI first**: initialize, run, inspect, and manage skills for local use, automation, and open-source distribution.
- **Python core**: the CLI is only an entry point; the same runtime embeds into services, scripts, notebooks, CI, or desktop apps.
- **Centralized progressive disclosure**: every Skill enters one shared index before its manifest, instructions, or Capability configuration is loaded on demand.
- **Code-composed multi-agent systems**: create each agent independently, then connect them naturally with `Agent.add_subagent(...)`.
- **Traceable execution**: main agents, subagents, model steps, and tool calls all emit one run-event protocol.
- **Lightweight dependencies**: the Python runtime uses only the standard library for straightforward installation, auditing, and extension.

## Quick Start

Python 3.11 or newer is required.

```bash
python3 -m pip install -e .
super-agent
```

The bare command opens an interactive chat with built-in workflow and memory skills. It uses the local `mock` provider, so the first run needs no configuration, project files, or API key. Run a single prompt just as directly:

```bash
super-agent run "hello"
```

Python has the same zero-configuration path:

```python
from super_agent import Agent

result = Agent().run("hello")
print(result.text)
```

Create a project only when you want to customize the model or skills:

```bash
super-agent init --path demo-agent
super-agent run --config demo-agent/agent.toml "hello"
```

The generated project includes editable agent configuration and example prompt, MCP, memory, and workflow skills.

You can also run directly from the source tree without installing the command:

```bash
PYTHONPATH=src python3 -m cli init --path demo-agent
PYTHONPATH=src python3 -m cli run --config demo-agent/agent.toml "hello"
```

## Architecture

Super Agent keeps five responsibilities explicit:

- **Provider provides intelligence** through one model-facing protocol.
- **Runtime schedules capabilities** and owns no concrete skill behavior.
- **Capability executes mechanisms** such as retrieval, execution, evaluation, updating, and recording.
- **Skill carries content** and configuration consumed by those mechanisms.
- **Agent composes everything** and exposes clear replacement methods such as `set_run_controller(...)`, `set_skill_retriever(...)`, and `add_skill_executor(...)`.

`Agent()` assembles tested defaults automatically. Advanced users can replace one Capability without rebuilding the Runtime or introducing another configuration system.

Useful inspection commands:

```bash
super-agent skills list --config demo-agent/agent.toml
super-agent skills index --config demo-agent/agent.toml --output json
super-agent skills validate --config demo-agent/agent.toml
super-agent skills explain --config demo-agent/agent.toml --prompt "hello"
super-agent skills freshness --config demo-agent/agent.toml
super-agent memory habits --config demo-agent/agent.toml
```

## Agent Configuration

An `agent.toml` file configures one agent. Parent-child relationships belong in code, not TOML.

```toml
[agent]
name = "super-agent"
system = "You are a concise, helpful agent."
workflow = "direct"
memory = "default"
skills = ["echo"]
use_features = ["skill"]
disable_names = []
# Optional. Omitting it allows unlimited depth; exceeding it only emits a warning.
max_agent_chain_depth = 5

[model]
provider = "mock"
model = "mock"

[paths]
skills = ["skills"]
memory = ".super-agent/memory"
```

`workflow` and `memory` select Skills by name. `paths.skills` is the shared recursive scan root for every Capability; `paths.memory` is runtime data, not Skill content.

`use_features` defaults to `["skill"]`. `disable_names` can disable a whole Capability, a stable key, or every Skill sharing a bare name:

```toml
[agent]
disable_names = ["memory:default", "workflow:direct", "prompt:echo", "mcp"]
```

Stable identities always use `capability:name`. A bare name such as `echo` matches every Skill named `echo`.

### Model Providers

Current providers:

- `mock`: local development and tests without network requests.
- `openai-compatible`: calls `<base_url>/chat/completions`.
- `anthropic-compatible`: calls `<base_url>/v1/messages`.

```toml
[model]
provider = "openai-compatible"
model = "gpt-4.1-mini"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
```

Secrets are read only from the environment variable named by `api_key_env`; they are never stored in the configuration file.

## Unified Skill Model

Every Skill declares the execution mechanism it needs through `capability`:

- `prompt`: instructions disclosed to the model.
- `mcp`: discoverable and callable MCP tools.
- `memory`: memory policy, while memory data remains separate in the runtime directory.
- `workflow`: agent execution and termination behavior.

A minimal prompt skill:

```text
skills/prompt/echo/
  skill.toml
  SKILL.md
```

```toml
schema_version = 2
name = "echo"
capability = "prompt"
description = "Minimal example skill"
version = "0.1.0"
triggers = ["echo", "brief"]
agent_created = false
agent_can_update = false
freshness = 70
function_group = "general"
provides = ["echo"]
requires = []

[entry]
instructions = "SKILL.md"
```

`SKILL.md` contains instructions disclosed to the model. `[entry]` is optional for configuration-only Skills. Arbitrary Capability names are discovered automatically; registering a matching Skill executor is enough to make a new model-facing Capability runnable.

### Composition and Dependencies

`provides` declares capabilities and `requires` declares capability dependencies. If `provides` is omitted, the skill provides its own name. The runtime loads the full dependency graph in topological order and reports missing capabilities, cycles, or ambiguous providers explicitly.

```toml
name = "research"
provides = ["facts"]
requires = ["http"]
```

```bash
super-agent skills graph --config agent.toml --name report
super-agent skills lock --config agent.toml --name report --output skill.lock
```

`skill.lock` records Capability names, versions, dependency edges, and directory SHA-256 values without timestamps or absolute paths, making identical inputs byte-for-byte reproducible.

## Centralized Progressive Disclosure

`ProgressiveDisclosureCore` is the only read path for all skills. Agents, the CLI, benchmarks, dependency resolution, evolution, package management, and the macOS app all consume the same central index. Only the central source parser reads `skill.toml`.

Each run discloses capabilities in four stages:

1. `index`: writes summaries, freshness, and cache paths for all enabled skills without reading instruction bodies.
2. `manifest`: writes a normalized manifest on demand and distinguishes same-named Skills by `capability:name`.
3. `instructions`: reads `SKILL.md` only for selected skills.
4. `configuration`: reads the shared `[configuration]` table only when a Capability needs it.

Default cache layout:

```text
.super-agent/memory/disclosure/
  index.json
  history.jsonl
  skills/<capability>/<name>/manifest.json
  skills/<capability>/<name>/instructions.md
  skills/<capability>/<name>/configuration.json
```

Unchanged content reuses the existing cache file and path based on SHA-256. Every disclosure appends to `history.jsonl`, and the model can call `read_disclosed_content` with a cache path already present in the index.

Core Python APIs:

- `prepare_skill_index()`: prepares the central skill index.
- `select_skill_references_for_prompt(...)`: selects skills through configuration, triggers, and dependencies.
- `open_skill(...)`: opens a staged disclosure handle by name and optional Capability.
- `read_disclosed_content(...)`: reads content from an existing disclosure cache path.
- `read_disclosure_history()`: reads the complete disclosure-path history.

## Workflows and Real Tool Loops

A workflow is itself a skill:

```toml
schema_version = 2
name = "react"
capability = "workflow"
description = "Tool-using workflow"
version = "0.1.0"
triggers = []

[configuration]
mode = "react"
max_steps = 8
instruction = "Finish as soon as the task is complete."
```

Built-in modes:

- `direct`: one model request.
- `plan`: produces a compact plan and executes it in one request.
- `react`: lets the model use runtime tools on demand and finishes when no more tools are requested.
- `loop`: keeps using runtime tools until the model finishes or reaches `max_steps`.

`react` and `loop` use one shared tool protocol:

- `list_skills`, `read_skill_manifest`, `read_skill_instructions`, and `read_skill_configuration`.
- `read_disclosed_content`, `list_skill_tools`, and `run_skill`.
- `list_memory_items`, `add_memory_item`, `recall_memory`, `forget_memory`, and `consolidate_memory`.
- `list_subagents` and `run_subagent`.

## Code-Composed Multi-Agent Systems

Each agent has its own configuration, model, skills, memory, and workflow. Clear code relationships attach them to a parent:

```python
from super_agent import Agent

main = Agent.load_from_config_file("agents/main.toml")
coder = Agent.load_from_config_file("agents/coder.toml")
reviewer = Agent.load_from_config_file("agents/reviewer.toml")

main.add_subagent(
    coder,
    name="coder",
    description="Implement code and tests",
    triggers=["code", "implement", "test"],
)
main.add_subagent(reviewer, description="Review risks and boundaries", triggers=["review"])

result = main.run("Implement a lightweight agent feature and review it")
print(result.text)
```

`name` is optional. Omitting it generates `subagent01`, `subagent02`, and so on; explicit names must be unique. Pass `created_by_agent=True` for a dynamically created subagent. Run results preserve this marker and the complete nested result tree for frontends.

`direct` and `plan` run subagents before the main request when triggers match; a subagent without `triggers` runs every time. `react` and `loop` instead expose `list_subagents` and `run_subagent`, allowing the model to choose when to delegate and which prompt to send.

Subagents may contain more subagents. A pre-run link check emits warnings only:

- Exceeding `max_agent_chain_depth` reports the depth and complete path.
- A cycle reports its full path, such as `main -> coder -> reviewer -> main`.
- Omitting the maximum allows unlimited nesting. The runtime does not force termination; the workflow defines when execution ends.

Call `main.check_subagent_links()` directly to inspect these warnings in code.

## Run Tracing

Every `Agent.run(...)` creates a unique `run_id` and writes ordered events to:

```text
.super-agent/memory/runs/<run-id>/events.jsonl
```

Each subagent has its own `run_id` and points to its parent through `parent_run_id`. Events cover skill disclosure, model steps, tool calls, memory updates, and results, allowing consumers to reconstruct the complete execution tree.

```bash
super-agent run --output json --config agent.toml "hello"
```

Desktop apps and other processes can use the JSONL protocol:

```bash
printf '%s' '{"prompt":"hello","messages":[]}' \
  | super-agent run --config agent.toml --request-stdin --output jsonl
```

Each line has a `type` of either `event` or `result`.

## Self-Updating Memory

A memory skill defines policy, while user memory and usage habits remain in the runtime directory:

```toml
schema_version = 2
name = "default"
capability = "memory"
description = "Default memory behavior"
version = "0.1.0"
triggers = []

[configuration]
default_scope = "agent"
recall_limit = 20
include_in_prompt = true
include_usage_habits = true
```

```text
.super-agent/memory/
  memory_events.jsonl
  habits.json
```

Memory events are append-only and never destroy historical evidence. Recall first isolates a scope, then applies deterministic lexical ranking across English words, numbers, and Chinese characters. Consolidation merges only normalized duplicates in the same scope. Every successful run also updates workflow and skill usage habits for inclusion in future context according to policy.

```bash
super-agent memory habits --config agent.toml
super-agent memory list --config agent.toml --scope agent
super-agent memory add --config agent.toml --text "Prefer concise answers." --scope agent
super-agent memory recall --config agent.toml --query "answer style" --scope agent --limit 5
super-agent memory forget --config agent.toml --item-id <memory-id>
super-agent memory consolidate --config agent.toml
```

## Skill Freshness

Freshness scoring does not call a model. The runtime appends skill-use events to `skill_events.jsonl` and writes aggregates to `skill_stats.json`. The score combines explainable signals:

- `quality`: an EWMA of recent success.
- `recency`: time since last use with a seven-day half-life.
- `frequency`: call count and call rate.
- `efficiency`: approximate input/output token cost.
- `reliability`: success and empty-output rates.
- `replacement`: whether another successful skill in the same `function_group` is used shortly afterward.
- `confidence`: regression toward the default when samples are sparse, reducing cold-start errors.

```bash
super-agent skills freshness --config agent.toml
```

The central index and macOS app both expose dynamic freshness and its runtime statistics.

## Skill Evolution Loop

Two manifest fields control evolution permission:

- `agent_created`: whether the agent created the skill.
- `agent_can_update`: whether the agent may update it; when omitted, it defaults to `agent_created`.

Human-created skills are immutable by default, while agent-created skills can keep improving. Updates never overwrite live files directly. The runtime creates an isolated candidate, runs deterministic evaluation cases through the real provider, and atomically promotes it only after it passes and its parent version remains unchanged. Every promotion stores an immutable snapshot for rollback.

```python
from super_agent import Agent, EvaluationCase

manager = Agent.load_from_config_file("agent.toml").create_skill_evolution_manager()
candidate = manager.create_skill_candidate("research-note", "Summarize with sources")
report = manager.evaluate_skill_candidate(
    candidate.candidate_id,
    [
        EvaluationCase(
            name="contains source",
            prompt="Summarize this note.",
            expected_output_contains=["source"],
            forbidden_output_contains=["unknown"],
        )
    ],
)
if report.passed:
    manager.promote_skill_candidate(candidate.candidate_id)

manager.rollback_skill("research-note")
```

Evaluation uses explicit string assertions without an additional model-as-judge call:

```json
[
  {
    "name": "contains source",
    "prompt": "Summarize this note.",
    "expected_output_contains": ["source"],
    "forbidden_output_contains": ["unknown"],
    "evaluator_instruction": "Return a concise answer."
  }
]
```

```bash
super-agent skills propose --config agent.toml --name research-note --goal "summarize with sources"
super-agent skills evaluate --config agent.toml --candidate-id <id> --cases evaluation-cases.json
super-agent skills promote --config agent.toml --candidate-id <id>
super-agent skills evolve --config agent.toml --name research-note --goal "make it clearer" --cases evaluation-cases.json
super-agent skills rollback --config agent.toml --name research-note
```

Candidates, evaluation reports, and history are stored under `.super-agent/memory/evolution/` by default.

## MCP Skills

MCP is an ordinary Skill with `capability = "mcp"`:

```toml
schema_version = 2
name = "filesystem"
capability = "mcp"
description = "Example stdio MCP server"
version = "0.1.0"
triggers = ["filesystem", "files"]

[entry]
instructions = "SKILL.md"

[configuration]
transport = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem"]

[configuration.env]
ROOT_PATH = "/tmp"
```

The current runtime executes stdio MCP servers. Environment variable values are passed only to the MCP child process; the model context sees variable names. `react` and `loop` discover tools with `list_skill_tools` and invoke them with `run_skill`.

## Skill Package Management

Skills can be packed into deterministic ZIP files or installed from a local directory, ZIP archive, or Git repository:

```bash
super-agent skills pack --config agent.toml --name research --output research.zip
super-agent skills install --config agent.toml --source ./research.zip
super-agent skills install --config agent.toml --source 'git+https://github.com/example/skills.git#skills/research'
super-agent skills update --config agent.toml --name research --source ./new-research
super-agent skills remove --config agent.toml --name research
```

`--expected-sha256` checks the normalized skill-directory hash used by `skill.lock`. Installation rejects symlinks, ZIP path traversal, name conflicts, and hash mismatches. Updates switch directories only after full validation and restore the old version on failure.

## Progressive Disclosure Benchmark

The benchmark compares context costs for “capability index plus every instruction” against “capability index plus selected instructions.” It never calls a model:

```bash
super-agent benchmark \
  --config examples/basic/agent.toml \
  --cases examples/basic/benchmark-cases.json \
  --output report.json
```

Reports contain `eager_context_tokens`, `progressive_context_tokens`, `saved_context_tokens`, and `context_savings_ratio`. Tokens use the deterministic approximation `ceil(character count / 4)`, which is suitable for reproducible comparisons between commits but does not represent a specific model tokenizer's bill.

## Python API

Third-party code imports all public APIs from `super_agent`:

```python
from super_agent import Agent, AgentConfig

config = AgentConfig.load_from_file("agent.toml")
agent = Agent(config)
result = agent.run("Summarize this project")

print(result.text)
```

Common entry points:

- `Agent.load_from_config_file(...)`, `Agent.run(...)`, and `Agent.add_subagent(...)`.
- `Agent.list_subagents()` and `Agent.check_subagent_links()`.
- `Agent.create_skill_evolution_manager()`.
- `ProgressiveDisclosureCore.prepare_skill_index()` and `open_skill(...)`.
- `MiniMemory.add_memory_item(...)`, `recall_memory(...)`, `forget_memory(...)`, and `consolidate_memory()`.
- `SkillBenchmark.run_cases(...)`.
- `run_event_to_dict(...)`, `run_event_from_dict(...)`, and `skill_manifest_to_dict(...)`.

Internal code imports definition modules directly without intermediate facades. `src/super_agent.py` is the only public aggregate entry point.

## Schemas and Compatibility

A Skill manifest must explicitly set `schema_version = 2` and provide `name`, `capability`, `description`, `version`, and `triggers`. `[entry]` optionally points to instructions, while one generic `[configuration]` table carries settings for any Capability. Schema v1 and legacy Capability-specific tables are rejected instead of converted implicitly.

Run event v1 has exactly eight fields: `schema_version`, `run_id`, `sequence`, `event_type`, `created_at`, `agent_name`, `parent_run_id`, and `data`. Readers reject missing fields, unknown fields, invalid types, and unsupported schema versions instead of applying silent compatibility behavior.

Python APIs may still change during the `0.0.x` series. Incompatible persisted-format changes require explicit migration and are never guessed into a new representation. Current provider names are limited to `mock`, `openai-compatible`, and `anthropic-compatible`.

## macOS App

`src/frontend/mac` contains a SwiftUI desktop app with conversation management, visual TOML configuration, a model list, skill toggles and freshness, default memory/workflow selection, and a main/subagent run tree.

```bash
cd src/frontend/mac
swift run SuperAgentMac
```

The app calls the Python runtime through its JSONL protocol and reads the central index through `skills index --output json`; it does not parse skill manifests independently. See `src/frontend/mac/README.md` for details.

## Project Layout

```text
src/
  agents/        # Code-first Agent composition
  capability/    # Replaceable execution mechanisms
  provider/      # Model provider adapters
  runtime/       # Capability-only scheduling, configuration, events, and results
  cli.py         # Zero-configuration CLI entry point
  cli_commands/  # Advanced commands grouped by domain
  builtin_skills/ # Zero-configuration workflow and memory content
  skill/         # Shared Skill content model
    disclosure/  # Central parser, index, cache, and history
    ecosystem/   # Dependency lock and package management
    evolution/   # Candidates, evaluation, freshness, and history
    kinds/       # MCP, memory, and workflow implementations
  frontend/mac/  # SwiftUI desktop app
```

## Development and Verification

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
python3 -m compileall -q src tests
```

## Design Principles

- Runtime schedules configured Capability contracts without importing concrete implementations.
- Agent composes Provider, Runtime, Capability, Skill, and subagents through code-first APIs.
- Skill is the only capability-extension boundary; prompts, tools, memory, and workflows do not get parallel systems.
- Configuration describes one agent, while code expresses clear and flexible agent relationships.
- Failures are explicit; implicit fallbacks and compatibility shells do not hide configuration errors.
- Autonomous evolution requires isolated candidates, real evaluation, conditional promotion, and rollback history.
- New capabilities complete a minimal real loop before becoming a stable schema.

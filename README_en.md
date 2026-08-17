# Super Agent

[Bilingual overview](README.md) | [中文使用文档](README_cn.md)

**A simple, lightweight, highly intelligent, self-evolving, skill-first agent runtime.**

> Skill is all you need.

Super Agent gives a model a compact Skill index. The model decides what it needs, opens
only that content, and runs the selected instructions or registered tools. Prompts,
task instructions, run policies, memory methods, and tool-use methods all use the same
Skill format and progressive disclosure path. Model connections and secrets remain explicit configuration.

The default Python install has no third-party runtime dependencies. A basic `Agent()` is
stateless and writes no files. Storage, conversations, memory, Skill updates, and MCP are
optional layers that fail clearly when their requirements are missing.

Super Agent is experimental pre-`1.0` software. Breaking changes do not keep compatibility
aliases or migration wrappers.

## Start in One Minute

Python 3.11 or newer is required.

```bash
python3.11 -m pip install -e .
export OA3_SILICONFLOW_API_KEY="..."
super-agent check
super-agent "Explain this repository"
```

`check` only reads configuration, Skills, and model settings. It does not create storage
or call a model. `OA3_SILICONFLOW_API_KEY` selects the documented free model; other endpoints
use generic environment variables or `common.toml`. An offline smoke test must be explicit:

```bash
SUPER_AGENT_PROVIDER=mock super-agent check
SUPER_AGENT_PROVIDER=mock super-agent "hello"
```

Run `super-agent` without arguments for an interactive conversation. No project files are
generated; add `common.toml`, `cli.toml`, `code.toml`, or local Skills only when needed.

Inside a conversation, use `/help`, `/clear`, or `/exit` for terminal controls.

## Add a Skill

A Skill is one Markdown file with TOML front matter:

```markdown
+++
name = "research"
type = "prompt"
description = "Research a question and report cited findings"
version = "1.0.0"
created_by = "user"
agent_can_update = false
categories = ["research"]
+++

Confirm the question and evidence scope, then report conclusions with sources.
```

Place the file under any directory listed in `skill_paths`. There are no trigger words.
The model decides which Skills to disclose or activate from their descriptions. `requires`
lists tools that must exist; `optional_tools` lists tools mounted when registered without
making the method unavailable when they are absent.

## Use Python

The common module exports one class:

```python
from super_agent import Agent, model_from_environment

agent = Agent(model_from_environment())
result = agent.run("Explain progressive Skill disclosure")
print(result.text)
```

The most common direct `Agent` actions are `run`, `for_user`, `add_group`, `add_subagent`,
`add_skill_path`, `add_tool`, and `add_model`. Advanced contracts are imported from the
module that owns them.

Compose specialized Agents in code. A task Skill selection belongs to one run and does
not silently change later runs:

```python
from super_agent import Agent, model_from_environment

main = Agent(model_from_environment())
coder = Agent(model_from_environment())
engineering = main.add_group("engineering")
engineering.add_subagent(coder, name="coder", description="Implements and verifies code changes")
result = main.run("Ask engineering to fix the failing test", skill="common-multi-producer-consumer")
```

Level 1 is always the root group. Structural groups organize Agents without calling a model,
and an Agent keeps its existing subtree when attached. Sibling Agents exchange stable references
through their parent board. One user-scoped tree runtime owns tasks, sleep and wake events, price
routing, circuit retries, adaptive compression, and multi-model decisions. It is not created when
the Agent has no groups or subagents.

## Add State Only When Needed

```python
from adapter.storage import JsonlStorage
from super_agent import Agent, model_from_environment

agent = Agent(model_from_environment())
agent.use_storage(JsonlStorage(".super-agent/data"))
agent.enable_memory()
alice = agent.for_user("alice")
conversation = alice.conversations.create("Project")
alice.memory.remember_long_term("The user prefers concise answers", labels=("preference",))
result = alice.run("Continue analyzing the project", conversation_id=conversation.conversation_id)
print(alice.runs.explain(result.run_id))
```

Conversation messages are short-term context. Long-term memory stores durable facts,
preferences, and abstractions, and can be explicitly organized or forgotten. User and
Agent scopes isolate conversations, memory, runs, and Skill overlays.

JSONL is the readable default backend. SQLite also uses the standard library. MySQL and
PostgreSQL drivers are optional extras.

Audit records are bounded and configurable. Detailed records are kept for 180 days and
critical records for 365 days by default. Canonical events stay complete for learning and
review, while `alice.runs.explain(run_id)` dynamically replaces prompts, model output, tool
payloads, and errors with hashes and size summaries. Passing `include_sensitive=True` in code
is required to read the original fields. Dynamic redaction is not storage encryption, so
protect access to the selected backend; retention cleanup runs from the backend's explicit
policy when records are written.

## Update a Skill Explicitly

Learning records evaluation, freshness, and model-use evidence. It never changes a Skill.
`SkillEvolution` separates updates into explicit `propose`, `test`, `apply`, and `undo`
actions. Proposal and testing cannot activate a candidate; only `apply` changes the user
overlay, and failed tests block it. See [Evolution](docs/evolution.md) for the complete flow.

## CLI

```bash
super-agent check
super-agent "one task"
super-agent --skill code "inspect this repository"
super-agent config show
super-agent skills list
super-agent data storage verify --config common.toml
super-agent data conversations list --config common.toml --user alice
```

One-shot runs are stateless unless `--save` is explicit. Text runs print the answer, stop
reason, and run ID; use `--output json` for the complete result.

Optional `cli.toml` controls terminal defaults only. Shared Runtime settings stay in
`common.toml`, coding workspace settings stay in `code.toml`, and model connections stay
in `common.toml` or environment variables. These files are validated separately and are
never deep-merged.

The code task exposes a bounded directory tree, ranged UTF-8 file reads, text search, and
fixed-argument Git status and diff reads. File replacement, structured exact patches, and
deletion require the SHA-256 returned by a prior read, so a concurrent change fails instead
of being overwritten. Paths must remain under the configured workspace; ignored, escaping,
oversized, and non-text reads fail visibly. Actions in `code.toml` accept `deny`, `ask`, or
`allow`: `deny` omits the tool, `ask` confirms each call, and `allow` follows the user's
explicit permission without another prompt. Verification commands are declared as argument arrays, then started, polled, or
stopped by ID with explicit time and output limits; shell strings are never accepted.
`repository_map` provides a bounded path, hash, and symbol map. Python symbols use the
standard AST parser; other file types do not receive guessed symbols. `run_check` waits for
one declared check and returns its actual exit code. A failed check is evidence for the next
explicit model edit; the runtime never repairs files or hides a failed verification.

## Guarantees

- Skills, files, tool output, memory context, and subagent results use one central progressive
  disclosure path. Large content is referenced and read in bounded pages instead of silently
  truncated.
- One per-run context budget is shared by Skill instructions, tool results, memory context,
  subagent results, and reference reads. When it is full, the model receives only a stable
  reference and hash and must request the next page explicitly.
- Routing is model judgment, not keyword matching.
- Reads do not mutate domain state; an explicit disclosure cache writes only bounded, disposable cache files.
- Skill content is passive and cannot register code, permissions, or secrets.
- Provider, storage, and optional-feature failures are visible; there is no hidden fallback.
- A basic run does not require storage, memory, conversations, safety rules, or learning.

## Benchmark Results

All agents used `THUDM/GLM-4-9B-0414` with their respective default configurations.
Scores are passed tasks / total tasks (pass rate).

| Agent | Model | HumanEval+ | LiveCodeBench Codegen |
| --- | --- | ---: | ---: |
| Codex | `THUDM/GLM-4-9B-0414` | 96 / 164 (58.54%) | 166 / 612 (27.12%) |
| Claude Code | `THUDM/GLM-4-9B-0414` | 100 / 164 (60.98%) | 151 / 612 (24.67%) |
| Super Agent | `THUDM/GLM-4-9B-0414` | 103 / 164 (62.80%) | 156 / 612 (25.49%) |

Full task-level reports, isolated runners, and local evaluation asset guidance live under
[`tests/eval/`](tests/eval/README.md).

## Read Next

- [Getting started](docs/getting-started.md)
- [Source tour](docs/source-tour.md)
- [Skills](docs/skills.md)
- [Configuration](docs/configuration.md)
- [Runtime](docs/runtime.md)
- [CLI](docs/cli.md)
- [Learning, memory, and Skill changes](docs/evolution.md)
- [Safety](docs/safety.md)

Runnable examples are in `examples/minimal.py`, `examples/custom_skill.py`, and
`examples/team.py`.

## Verify the Repository

```bash
python3.11 scripts/verify_release.py --version 0.2.1 --full
```

For the complete local release gate, including version and package-shape checks, see
[Local release](docs/releasing.md).

The dependency-free comparison runner and its reproducibility contract are documented in
[Benchmarks](docs/benchmarks.md).

## Acknowledgements

The projects, modules, protocols, and research that informed Super Agent are documented in the
[bilingual acknowledgements](README.md#致谢与借鉴).

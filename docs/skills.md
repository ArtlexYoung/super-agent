# Skills and Progressive Disclosure

A Skill is passive content that describes something an Agent can use. Prompts, MCP
servers, memory rules, workflows, planners, model profiles, and custom types all use the
same manifest, identity, source order, disclosure cache, evidence, and update lifecycle.

## Directory Format

The recommended layout is:

```text
skills/<type>/<name>/
  skill.toml
  SKILL.md
  additional resources...
```

Core scans Skill roots recursively, so the directory names are organizational. The
`type` and `name` in `skill.toml` define the stable identity. Only `skill.toml` is
required. Configuration-only Skills may omit `SKILL.md`.

## Manifest

```toml
schema_version = 3
name = "research"
type = "prompt"
description = "Research with explicit sources"
version = "0.1.0"
triggers = ["research", "source"]
agent_created = false
agent_can_update = false
freshness = 70
function_group = "research"
provides = ["facts"]
requires = ["http"]

[entry]
instructions = "SKILL.md"

[configuration]
style = "concise"
```

Schema 3 rejects unknown fields. Every Skill type uses the same `[configuration]` table;
the code that consumes that type validates its contents.

Common types are:

- `prompt`: model instructions.
- `mcp`: an MCP server declaration and tool guidance.
- `memory`: recall, organization, and forgetting rules.
- `workflow`: direct, plan, react, or loop task rules.
- `planner`: task decomposition rules.
- `model`: Provider connection metadata and routing traits.

Custom type names are allowed. Register a matching SkillRunner in Python when the type
needs executable behavior. The manifest type `runner` is reserved because executable
code cannot come from a Skill directory.

## Identity and Selection

A Skill key is always `type:name`, for example:

```text
prompt:concise
memory:default
workflow:react
model:fast
search:adaptive
```

A bare name is accepted only when it identifies one Skill. Core selects Skills from
`agent.skills`, prompt trigger matches, and dependencies declared by `requires`.
Dependency resolution is deterministic and fails on missing providers, ambiguous
providers, or cycles.

```bash
super-agent skills explain --config agent.toml --prompt "research this"
super-agent skills graph --config agent.toml --name prompt:research
super-agent skills lock --config agent.toml --name prompt:research --output skill.lock
```

Core resolves one source hierarchy in `user > project > builtin` order. User Skills live
inside the current user's private Agent scope and override matching shared Skills. Invalid
lower layers are still reported instead of being silently ignored.

## One Disclosure Core

`ProgressiveDisclosureCore` is the only Skill read path. It provides five stages:

1. `index`: compact identities, summaries, freshness, hashes, and cache paths.
2. `manifest`: normalized metadata for one selected Skill.
3. `instructions`: instruction text for that Skill.
4. `configuration`: its generic configuration table.
5. `files`: the full inventory; UTF-8 files include content and binary files include size
   and SHA-256 only.

During a run, disclosure writes content-addressed cache entries under:

```text
.super-agent/users/<user-hash>/agents/<agent-hash>/cache/
  index.json
  history.json
  skills/<type>/<name>/manifest.json
  skills/<type>/<name>/instructions.md
  skills/<type>/<name>/configuration.json
  skills/<type>/<name>/files.json
```

The model receives the index and stable cache paths first. It can use
`read_disclosed_content` to read a previously disclosed path without repeating
selection. Every run-time disclosure is recorded in the canonical event stream;
`history.json` is a rebuildable view. Offline listing and validation do not write cache
or history unless recording is explicitly enabled.

## Packages

```bash
super-agent skills pack --config agent.toml --name prompt:research --output research.zip
super-agent skills install --config agent.toml --source ./research.zip
super-agent skills update --config agent.toml --name prompt:research --source ./new-research
super-agent skills remove --config agent.toml --name prompt:research
```

Local directories, ZIP files, and `git+...#subdirectory` sources are supported. Install
and update write only the selected user's overlay. Package validation rejects symlinks,
path traversal, identity changes, and unexpected content hashes. Packages contain passive
Skill data only; executable extensions are always registered in application code.

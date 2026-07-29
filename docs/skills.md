# Skills and Progressive Disclosure

A Skill is passive content that describes something an Agent can use. Task scenes,
prompts, MCP tool guidance, memory rules, workflows, planners, model profiles, and custom types
all use the same manifest, identity, source order, disclosure cache, evidence, and update
lifecycle.

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

- `scene`: references the Skill set for one kind of task.
- `prompt`: model instructions.
- `mcp`: tool guidance and the name of a server registered in application code.
- `memory`: recall, organization, and forgetting rules.
- `workflow`: direct, plan, react, or loop task rules.
- `planner`: task decomposition rules.
- `model`: Provider connection metadata and routing traits.

Custom type names are allowed. Register a matching SkillLoader in Python when the type
needs executable behavior. The manifest type `runner` is reserved because executable
code cannot come from a Skill directory.

An MCP Skill has no executable connection settings:

```toml
schema_version = 3
name = "filesystem"
type = "mcp"
description = "Filesystem tools"
version = "0.1.0"
triggers = ["filesystem", "files"]

[entry]
instructions = "SKILL.md"

[configuration]
server = "filesystem"
```

`server` defaults to the Skill name. Fields such as `command`, `arguments`, `environment`,
and `transport` are rejected because a Skill is untrusted passive content. The matching
implementation and its effects are attached to an Agent in code.

## Identity and Selection

A Skill key is always `type:name`, for example:

```text
scene:code
prompt:concise
memory:default
workflow:react
model:fast
search:adaptive
```

A bare name is accepted only when it identifies one Skill. Core first selects one scene,
then selects Skills from that scene, `agent.skills`, prompt trigger matches, and
dependencies declared by `requires`.
Dependency resolution is deterministic and fails on missing providers, ambiguous
providers, or cycles.

```bash
super-agent skills explain --config agent.toml --prompt "research this"
super-agent skills graph --config agent.toml --name prompt:research
super-agent skills lock --config agent.toml --name prompt:research --output skill.lock
```

Core resolves one source hierarchy in `user > project > builtin` order, where `builtin`
is the source label for content under package-level `src/skill/builtin`. User Skills live inside
the current user's private Agent scope and override matching shared Skills. Invalid lower
layers are still reported instead of being silently ignored.

## Task Scenes

A scene is an ordinary configuration-only Skill whose `[configuration].skills` field
contains stable `type:name` references:

```toml
schema_version = 3
name = "review"
type = "scene"
description = "Code review task chain"
version = "0.1.0"
triggers = ["review code", "代码审查"]
default = false

[configuration]
skills = [
  "prompt:review",
  "memory:review",
  "planner:review",
  "workflow:review",
]
```

Every scene currently references a workflow. A scene cannot reference another scene and may
reference at most one planner and one workflow. Every referenced custom type needs a
registered SkillLoader. Missing references, missing loaders, and conflicts are errors;
Runtime does not substitute another scene or workflow.

The shipped roots are:

```text
src/skill/builtin/scene/common/   zero-configuration general task composition
src/skill/builtin/scene/code/     optional repository task composition
```

Scene selection precedence is explicit:

1. `Agent.run(..., scene="name")`, `super-agent run --scene name`, or AG-UI
   `forwardedProps.scene`.
2. The scenes allowed by `Agent.use_only_scenes(...)`, when configured in code.
3. One allowed scene whose manifest trigger matches the prompt.
4. The single allowed scene with `default = true`.

Multiple trigger matches or multiple defaults fail clearly. `Agent.disable_scenes()` and
`Agent.run(..., use_scenes=False)` select no scene and record that choice in the Plan.
Pinned memory, planner, or workflow Skills replace the same type from the selected scene;
other configured or triggered Skills are added normally.

User-private scenes can be created with `UserSkills.create_scene(...)` or by the model's
`create_skill_scene` tool in a tool-using scene. Creation writes a complete scene, prompt,
memory, planner, and workflow set with `agent_created = true` and
`agent_can_update = true`. It never replaces an existing key. The current run keeps its
prepared index, and the created scene is visible on the next run only.

## One Disclosure Core

`Skills` is the only Skill read, loading, and scene-selection path. Constructing
it with Skill roots is read-only: it does not create a storage backend, cache directory,
or history. Freshness data and recording are explicit inputs. It provides five disclosure
stages:

1. `index`: compact identities, summaries, freshness, hashes, and cache paths.
2. `manifest`: normalized metadata for one selected Skill.
3. `instructions`: instruction text for that Skill.
4. `configuration`: its generic configuration table.
5. `files`: the full inventory; UTF-8 files include content and binary files include size
   and SHA-256 only.

When Runtime explicitly attaches a disclosure recorder, disclosure writes
content-addressed cache entries under:

```text
.super-agent/users/<user-hash>/agents/<agent-hash>/cache/
  index.json
  history.json
  skills/<type>/<name>/manifest.json
  skills/<type>/<name>/instructions.md
  skills/<type>/<name>/configuration.json
  skills/<type>/<name>/files.json
```

The `read_*` methods verify and return source content without writing cache, history, usage,
or freshness state. The matching `disclose_*` methods explicitly write cache and history
when a recorder exists. The model always receives the compact index first and uses
`disclose_skill_manifest`, `disclose_skill_instructions`, or
`disclose_skill_configuration` when it needs more content. `read_disclosed_content` reads
an already disclosed path without activating its Skill. `activate_skill` is the separate
operation that loads a loader contribution, attaches its tools, and records actual use.
Offline listing, validation, and ordinary reads have no storage side effects.

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

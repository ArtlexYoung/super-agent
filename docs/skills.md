# Skills and Progressive Disclosure

Everything the Agent can use is represented as a Skill carried by a Capability.

## Directory Format

```text
skills/<capability>/<name>/
  skill.toml
  SKILL.md
  additional resources...
```

Only `skill.toml` is required. Configuration-only Skills, such as model, memory, and workflow definitions, can omit `SKILL.md`.

## Manifest

```toml
schema_version = 2
name = "research"
capability = "prompt"
description = "Research with explicit sources"
version = "0.1.0"
agent_created = false
agent_can_update = false
freshness = 70
function_group = "research"
provides = ["facts"]
requires = ["http"]
triggers = ["research", "source"]

[entry]
instructions = "SKILL.md"

[configuration]
style = "concise"
```

Schema v2 rejects unknown top-level fields and old Capability-specific configuration tables. Every Capability reads the same `[configuration]` table.

## Built-in Capability Names

- `prompt`: instructions added to model context.
- `mcp`: stdio MCP server configuration and model-facing tool guidance.
- `memory`: memory policy and runtime memory construction.
- `workflow`: direct, plan, react, or loop execution behavior.
- `model`: model description, connection ownership, and routing traits.
- `capability`: reserved and rejected; executable mechanisms come only from code.

Custom Capability names are discovered automatically. Register a matching Capability in code:

```python
agent.add_capability(my_capability)
```

The Capability's `capability_name` must match the manifest value.

## Stable Identity

Skills use `capability:name` keys. A bare name is accepted only when it resolves unambiguously.

```text
prompt:default
memory:default
workflow:direct
mcp:filesystem
model:fast
search:adaptive
```

## Selection and Dependencies

The central index selects Skills from:

- Explicit names in `agent.skills`.
- Trigger text found in the prompt.
- Dependencies required by selected Skills.

Dependencies are resolved in topological order. Missing requirements, cycles, and ambiguous providers fail with explicit messages.

```bash
super-agent skills explain --config agent.toml --prompt "research this"
super-agent skills graph --config agent.toml --name research
super-agent skills lock --config agent.toml --name research --output skill.lock
```

## Progressive Disclosure

`ProgressiveDisclosureCore` is the only Skill read path. It exposes five stages:

1. `index`: summaries, identities, freshness, hashes, and cache paths.
2. `manifest`: normalized metadata for one selected Skill.
3. `instructions`: instruction text for one selected Skill.
4. `configuration`: the generic Capability configuration table.
5. `files`: the complete directory inventory and UTF-8 contents; binary files expose only size and SHA-256.

Default cache layout:

```text
.super-agent/users/<user-hash>/agents/<agent-hash>/cache/
  index.json
  history.json
  skills/<capability>/<name>/manifest.json
  skills/<capability>/<name>/instructions.md
  skills/<capability>/<name>/configuration.json
  skills/<capability>/<name>/files.json
```

The model may call `read_disclosed_content` with a cache path already present in the index. Cache paths stay stable, content SHA-256 determines cache hits, and every disclosure appends a canonical storage event. `history.json` is a rebuildable model-readable view of those events.

Core methods:

- `prepare_skill_index()`
- `select_skill_references_for_prompt(...)`
- `explain_skill_selection_for_prompt(...)`
- `open_skill(...)`
- `SkillDisclosure.read_skill_files()`
- `read_disclosed_content(...)`
- `read_disclosure_history()`

## Packages

```bash
super-agent skills pack --config agent.toml --name research --output research.zip
super-agent skills install --config agent.toml --source ./research.zip
super-agent skills update --config agent.toml --name research --source ./new-research
super-agent skills remove --config agent.toml --name research
```

Local directories, ZIP archives, and `git+...#subdirectory` sources are supported. Package validation rejects symlinks, path traversal, identity changes during update, and unexpected content hashes.

# Skills

A Skill is passive content with one manifest. Prompts, workflows, memory behavior, MCP
descriptions, model profiles, task scenes, feedback, and evolution policy all use this
format.

## Layout

```text
skills/
  prompt/
    research/
      skill.toml
      SKILL.md
      resources/
```

```toml
schema_version = 3
name = "research"
type = "prompt"
description = "Research a question and report cited findings"
version = "0.1.0"
agent_created = false
agent_can_update = false
freshness = 70
function_group = "research"
provides = ["research"]
requires = []

[entry]
instructions = "SKILL.md"
```

Unknown fields and unsupported schema versions fail validation. Stable references use
`type:name`; a bare name is accepted only when unique.

## One Disclosure Path

Runtime first exposes only compact index data. The model may then call:

- `list_skills`
- `disclose_skill_manifest`
- `disclose_skill_instructions`
- `disclose_skill_configuration`
- `read_disclosed_content` for a path already cached in this user scope
- `activate_skill` to load registered behavior

Manifest, instruction, configuration, and resource reads all use the same core. Cache paths
are hashes of source identity and content, so a changed Skill cannot reuse stale content.
Reads do not count as activation or use.

There are no trigger words. Descriptions and current task context are given to the model,
which chooses what to inspect and activate during its normal turn.

## Skill Types and Loaders

The manifest accepts a clear lowercase type. Built-in loaders understand:

- `prompt`: model instructions.
- `workflow`: tool-loop policy and maximum turns.
- `memory`: long-term memory context and tools.
- `mcp`: passive selection of an MCP server registered in code.
- `scene`: a named group of other Skills.

`model`, `feedback`, and `evolution` Skills are read by their owning services through the
same index and disclosure core. Applications can add another type with an explicit
`Agent.add_skill_loader()` call.

A SkillLoader is trusted application code. A Skill directory cannot contain or activate a
Python runner. Packages with symlinks, traversal, unexpected hashes, or identity changes
are rejected.

## Scenes

A scene manifest contains references to ordinary Skills. Built-in `common` and `code`
scenes reuse the same `memory:default`; they do not copy shared content.

The model can activate an available scene like any other Skill. Callers can also select a
scene explicitly for one run. Each Agent controls visibility in code:

```python
agent.use_only_scenes("code")
agent.disable_scenes()
agent.select_scenes_automatically()
```

Scene access outside that policy fails. No TOML subagent or scene policy graph is created.

## Ownership and Evolution

`agent_created` records who created the Skill. `agent_can_update` is the explicit update
permission; it defaults to `true` only for Agent-created Skills. Model connection fields
have a separate update permission because they may affect secret and network boundaries.

Freshness is deterministic. It combines call outcomes, token use, time since use, frequency,
and successful same-function follow-ups. The model does not assign the freshness number.
Learning can use it to recommend a candidate, but evaluation and explicit promotion remain
separate.

## Packages

`skills pack`, `install`, `update`, and `remove` operate on passive packages. Install and
update write only the selected user's Skill overlay. `--expected-sha256` pins external
content. Git and ZIP input is staged and fully validated before one final replacement.

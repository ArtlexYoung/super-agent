# Skills

A Skill is passive content with one manifest. Prompts, workflows, memory behavior, MCP
descriptions, model profiles, task instructions, feedback rules, and freshness rules all use the
same source and disclosure path.

## Smallest Skill

```text
skills/prompt/research/
  skill.toml
  SKILL.md
```

```toml
description = "Research a question and report cited findings"
```

The directory supplies `name`, `type` defaults to `prompt`, and `version` defaults to
`0.1.0`. An existing `SKILL.md` supplies instructions. Unknown fields fail validation.
Stable references use `type:name`; a bare name is accepted only when unique.

Add a root in TOML or code:

```toml
[paths]
skills = ["skills", "team-skills"]
```

```python
agent.add_skill_path("team-skills")
```

## One Disclosure Path

Runtime first gives the model compact index entries. The model can then use:

- `list_skills` to inspect the index;
- `disclose_skill_manifest`, `disclose_skill_instructions`, and
  `disclose_skill_configuration` to open one stage;
- `read_disclosed_content` to reuse a storage-backed cached path;
- `activate_skill` to ask Runtime to load registered behavior.

Manifest, instruction, configuration, and resource reads all pass through
`ProgressiveDisclosureCore`. Cache paths include source identity and content hashes, so
changed content cannot reuse stale results. Reading does not activate or count as use.

There are no trigger words. The model receives descriptions and current task context and
makes its choice during the normal model turn.

## Built-In Types

- `prompt` contributes model instructions.
- `workflow` defines the tool-loop policy and maximum turns.
- `memory` contributes optional long-term memory context and tools.
- `mcp` selects an MCP server registered in trusted code.
- `task` combines task instructions, run mode, and stop limit.
- `model`, `feedback`, and `freshness` configure their owning Core services.

Skill directories cannot contain executable Python or shell runners. Runtime setup owns
trusted loaders, while ordinary Agent users add passive content and registered MCP tools.

## Task Skills

A task Skill directly carries its model instructions and run policy. Built-in `common` and
`code` tasks need no memory or storage. The model can activate an available task Skill, or a
caller can make one explicit for a single run:

```python
result = agent.run("Inspect this change", skill="code")
```

An explicit task Skill restricts task Skill activation for that run. Omitting it leaves selection to
model judgment. Neither option changes the Agent or later runs.

## Ownership and Freshness

Ownership and update permission come from trusted source metadata, never Skill-controlled
TOML. Built-ins are read-only, project Skills are user-authorized, and applied user
overlays are marked by Runtime.

Freshness is deterministic. It combines outcomes, token use, time since use, frequency,
latency, and successful same-function follow-ups. The model does not assign the number.
Explicit learning updates evidence; explicit Skill change commands handle content changes.

## Packages

`skills pack`, `install`, `update`, and `remove` operate on passive packages. Install and
update write only the selected user's Skill overlay. `--expected-sha256` pins external
content. Git and ZIP input is staged and validated before one final replacement.

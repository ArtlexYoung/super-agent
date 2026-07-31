# Skill Loaders

This is a framework extension boundary, not ordinary Skill authoring. Most users only
write passive Skill content and never need a loader.

A trusted SkillLoader turns one passive Skill type into model context, tools, a workflow
policy, included Skills, or a completion callback. It runs inside the single Runtime task
path and opens content through the central disclosure snapshot.

Built-in Runtime setup registers loaders for prompt, scene, MCP, memory, and workflow
Skills. Model, feedback, and freshness services read their Skills through the same
disclosure core without adding task tools.

## Contract

A loader declares:

- a clear implementation `name` and `version`;
- the lowercase `skill_type` it owns;
- whether it adds model context;
- any required service names;
- `load_skill(request)`, returning one `LoadedSkill`.

Available optional services are `storage`, `text_model`, and `event_stream`. Missing
requirements fail when a Skill is activated. Runtime validates duplicate types,
dependencies, included Skill cycles, duplicate tool names, and required services.

Every executable `SkillTool` and completion callback must include a `SkillAction` with
explicit effects and a resource. A missing action is an error before the handler runs.

## MCP

MCP has a built-in loader but requires an implementation registered in code:

```python
from core.checks import ActionEffect
from core.skill_use.mcp import StdioMcpServer
from super_agent import Agent

agent = Agent()
agent.add_tool(
    "filesystem",
    StdioMcpServer(
        "npx",
        arguments=("-y", "@modelcontextprotocol/server-filesystem"),
    ),
    effects=(
        ActionEffect.READ,
        ActionEffect.CREATE,
        ActionEffect.UPDATE,
        ActionEffect.DELETE,
        ActionEffect.EXECUTE,
    ),
)
```

Effects are mandatory. Add `NETWORK` when the server can communicate externally. Selecting
an unregistered server or a blocked action fails before its handler runs. Commands,
arguments, and environment values never come from Skill content.

## Trust Boundary

Runtime never imports, compiles, or executes code from a Skill directory. Package changes
can update only passive content and configuration. Adding a new loader type is a reviewed
Core integration change assembled in Runtime setup, not a model-generated Agent action.

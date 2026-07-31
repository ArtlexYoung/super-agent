# Skill Handlers

This is a framework extension boundary, not ordinary Skill authoring. Most users only
write passive Skill content and never need a handler.

A trusted `SkillHandler` turns one passive Skill type into model context, tools, a task
policy, included Skills, or a completion callback. It runs inside the single Runtime task
path and opens content through the central disclosure snapshot.

Runtime registers handlers for task, prompt, MCP, memory, and workflow Skills. Model,
feedback, and freshness services read their Skills through the same disclosure core without
adding task tools.

## Contract

A handler declares the lowercase `skill_type` it owns, whether it adds model context, and
`handle_skill(context)`, which returns one `SkillResult`. `SkillContext` exposes optional
storage, model text, identity, and checked action services explicitly. A missing service
fails when the handler requests it.

Runtime rejects duplicate handler types unless replacement is explicit. It also validates
handler results, included Skill references, duplicate tools, and required tool arguments.
Every executable `SkillTool` and completion callback must include a `SkillAction` with
explicit effects and a resource.

## MCP

MCP has a built-in handler but requires an implementation registered in code:

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
can update only passive content and configuration. Adding a handler type is a reviewed Core
integration change assembled in Runtime setup, not a model-generated Agent action.

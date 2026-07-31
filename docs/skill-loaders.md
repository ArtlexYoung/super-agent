# SkillLoaders

A SkillLoader is trusted application code that turns one passive Skill type into model
context, tools, a workflow policy, included Skills, or a completion callback. It runs
inside the single task Runtime and uses the central disclosure snapshot.

Built-in loaders cover prompt, scene, MCP, memory, and workflow Skills. Model, feedback,
and evolution services read their Skills through the same disclosure core without loading
task tools.

## Add a Loader

```python
from core.checks import ActionEffect
from core.skill_use.loaded import LoadedSkill, SkillAction, SkillTool
from core.skill_use.registry import SkillLoadRequest
from skill.manifest import Skill
from super_agent import Agent


class SearchSkillLoader:
    name = "search-index"
    version = "1"
    skill_type = "search"
    adds_model_context = True
    required_services = ()

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        opened = request.open_skill()
        return LoadedSkill(
            model_context=Skill(
                opened.read_manifest(),
                opened.read_instructions().content,
            ),
            tools=(
                SkillTool(
                    name="search_index",
                    description="Search the registered application index.",
                    properties={"query": {"type": "string"}},
                    handler=self.search_index,
                    action=SkillAction(
                        effects=(ActionEffect.READ, ActionEffect.EXECUTE),
                        resource="skill:registered:search-index",
                    ),
                    required=("query",),
                ),
            ),
        )

    def search_index(self, arguments: dict[str, object]) -> dict[str, object]:
        return {"query": arguments["query"], "matches": []}


agent = Agent()
agent.add_skill_loader(SearchSkillLoader())
```

The loader declares `name`, `version`, `skill_type`, `adds_model_context`, optional
`required_services`, and `load_skill(request)`. Available service names are `storage`,
`text_model`, and `event_stream`. Missing required services fail when the Skill is
activated. Registering the same type explicitly replaces that Agent's current loader.

## MCP

MCP has a built-in loader but requires a code-registered implementation:

```python
from core.checks import ActionEffect
from core.skill_use.mcp import StdioMcpServer
from super_agent import Agent

agent = Agent()
agent.add_mcp_server(
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

Effects are mandatory. Include `NETWORK` when the server can communicate externally.
Selecting an unregistered server or an action blocked by rules fails before its handler
runs. Commands, arguments, and environment values never come from a Skill manifest.

## LoadedSkill

`LoadedSkill` can currently contain model context, prompt context, tools, one task policy,
included Skill references, and one completion callback. Every executable tool and callback
must carry a `SkillAction` with explicit effects and resource.

Disclosure only reads passive content. The model must call `activate_skill` before loader
output and newly registered tools become available. Activation resolves included Skills,
rejects cycles, verifies required services, validates duplicate tool names, and then adds
the complete contribution.

## Trust Boundary

Runtime never imports, compiles, or executes code from a Skill directory. A package can
change only passive content and configuration. Loader changes remain ordinary reviewed
application code. Loader descriptors and MCP registrations include implementation and
settings hashes in recorded evidence; secret values are excluded.

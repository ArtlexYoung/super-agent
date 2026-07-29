# SkillLoaders

A SkillLoader is trusted application code that turns one passive Skill type into Runtime
behavior. The Skill task layer owns scheduling, progressive disclosure, tracing,
evaluation, and evolution; Core only executes prepared Provider calls and checks declared
actions. A SkillLoader has one loading boundary and does not create a second runtime.

Registered default SkillLoaders handle scheduler, scene, prompt, MCP, memory, workflow,
and planner Skills. Model Skills are read by the selected Scheduler when it chooses a
Provider profile.

## Add a Loader

Register custom code explicitly in Agent composition:

```python
from super_agent import (
    ActionEffect,
    Agent,
    LoadedSkill,
    Skill,
    SkillAction,
    SkillLoadRequest,
    SkillTool,
)


class SearchSkillLoader:
    name = "search-index"
    version = "1"
    skill_type = "search"
    adds_model_context = True
    required_services = ()

    def load_skill(self, request: SkillLoadRequest) -> LoadedSkill:
        opened = request.disclosure.open_skill(
            request.reference.name,
            self.skill_type,
        )
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
`required_services`, and `load_skill(request)`. Built-in service names are `storage`,
`text_model`, and `event_stream`. Preflight verifies every selected loader's declaration
before execution. Adding a loader for an existing type explicitly replaces that Agent's
current loader.

MCP servers use a smaller registration surface because their passive SkillLoader is
already built in:

```python
from super_agent import ActionEffect, Agent, StdioMcpServer

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

Effects are mandatory and must include `execute`. Add `network` when the server can access
the network; the standard action rules then require confirmation. A selected MCP Skill
without matching code registration fails preflight.

## LoadedSkill

`load_skill` returns one `LoadedSkill`. It may provide:

- Model instruction content.
- Prompt context built for the current task.
- Model-callable tools.
- Other Skill references through the same `included_skills` field used by scene Skills.
- Scheduling, workflow, or planning rules.
- A task-completed callback with a declared action.

Tools are available only after their Skill is selected and loaded. Disclosing another
Skill during a tool loop only exposes passive content. The model must call
`activate_skill` before that Skill's loader output and tools become available on the next
model step. Workflow, planner, and scene choices remain fixed in the preflighted Plan.

Every tool and completion callback must declare a `SkillAction`. Core checks its effects
and resource before calling trusted code and records the result. Missing action metadata
fails closed.

## Trust Boundary

Core never imports, compiles, or executes Python from a Skill directory. Skills can update
the content and configuration consumed by a loader, but executable loader changes remain
ordinary reviewed application-code changes. The Runtime lock stores each registered
loader's implementation name, version, dependencies, required services, and source hash.
Registered MCP code also contributes implementation and settings hashes plus declared
effects, never environment values.

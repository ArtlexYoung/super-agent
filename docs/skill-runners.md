# SkillRunners

A SkillRunner is trusted application code that turns one passive Skill type into Runtime
behavior. Core owns task scheduling, progressive disclosure, actions, tracing,
evaluation, and evolution. A SkillRunner has one loading boundary and does not create a
second runtime.

Registered default SkillRunners handle scene, prompt, MCP, memory, workflow, and planner
Skills. Model Skills are read by Core when it selects a Provider profile.

## Add a Runner

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


class SearchSkillRunner:
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
agent.add_skill_runner(SearchSkillRunner())
```

The runner declares `name`, `version`, `skill_type`, `adds_model_context`, optional
`required_services`, and `load_skill(request)`. Built-in service names are `storage`,
`text_model`, and `event_stream`. Preflight verifies every selected runner's declaration
before execution. Adding a runner for an existing type explicitly replaces that Agent's
current runner.

MCP servers use a smaller registration surface because their passive SkillRunner is
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
- A scene policy containing task-specific Skill references.
- Workflow or planning rules.
- A task-completed callback with a declared action.

Tools are available only after their Skill is selected and loaded. A model can disclose
another Skill during a tool loop; its runner output becomes available on the next model
step.

Every tool and completion callback must declare a `SkillAction`. Core checks its effects
and resource before calling trusted code and records the result. Missing action metadata
fails closed.

## Trust Boundary

Core never imports, compiles, or executes Python from a Skill directory. Skills can update
the content and configuration consumed by a runner, but executable runner changes remain
ordinary reviewed application-code changes. The Runtime lock stores each registered
runner's implementation name, version, dependencies, required services, and source hash.
Registered MCP code also contributes implementation and settings hashes plus declared
effects, never environment values.

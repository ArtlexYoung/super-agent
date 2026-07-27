# Capabilities

A Capability is code that executes one Skill mechanism. Runtime itself owns task lifecycle, progressive disclosure, tracing, evaluation, and evolution recommendations.

Built-in executors handle prompt, MCP, memory, and workflow Skills. Replace or add one explicitly:

```python
agent.add_skill_executor(executor)
```

An executor declares `name`, `version`, `capability_name`, `adds_model_context`, `load_skill(request)`, and `create_tools(request)`. There are no run-controller, disclosure, evaluator, or updater slots.

`load_skill` returns one `SkillContribution`. It can contribute model context, prompt context, tools, a task policy, and a completion recorder without exposing a private runtime object:

```python
from capability.skill_contributions import CapabilityTool, SkillContribution
from skill.manifest import Skill


class SearchExecutor:
    name = "search"
    version = "1"
    capability_name = "search"
    adds_model_context = True

    def load_skill(self, request):
        opened = request.disclosure.open_skill(request.reference.name, "search")
        return SkillContribution(
            model_context=Skill(
                opened.read_manifest(),
                opened.read_instructions().content,
            ),
            tools=(
                CapabilityTool(
                    "search",
                    "Search indexed content.",
                    {"query": {"type": "string"}},
                    self.run_search,
                    ("query",),
                ),
            ),
        )

    def create_tools(self, request):
        return ()

    def run_search(self, arguments):
        return {"matches": []}
```

`create_tools` contributes capability-wide tools that are available before one specific Skill is loaded. Return an empty tuple when the capability has none. Tool calls are always traced by Runtime.

## Capability Skills

Package an evolvable executor as a standard Skill:

```text
skills/capability/careful/
  skill.toml
  executor.py
```

```toml
schema_version = 2
name = "careful"
capability = "capability"
description = "Prompt executor with additional checks"
version = "0.1.0"
triggers = []
agent_created = true
agent_can_update = true

[configuration]
slot = "skill_executor:prompt"
entry_file = "executor.py"
entry_class = "CarefulPromptExecutor"
```

The zero-argument class must identify the same Skill name and version and execute the capability named by the slot. Two Capability Skills cannot replace the same executor.

Capability Skills use ordinary Skill install, evolve, and rollback commands. Runtime locks the exact implementation hash and attributes execution evidence to both the handler and its source Skill.

# Capabilities

A Capability is code that executes one Skill mechanism. Runtime itself owns task lifecycle, progressive disclosure, tracing, evaluation, and evolution recommendations.

Capability code is trusted application code. Runtime never imports, compiles, or executes
Python from a Skill directory. Register custom code explicitly so the trust decision is
visible in the Agent composition:

Built-in Capabilities handle prompt, MCP, memory, workflow, and planner Skills. Replace or add one explicitly:

```python
agent.add_capability(capability)
```

A Capability declares `name`, `version`, `capability_name`, `adds_model_context`, and one method: `load_skill(request)`. There are no parallel tool, run-controller, disclosure, evaluator, or updater contracts.

`load_skill` returns one `SkillContribution`. It can contribute model context, prompt context, tools, a task policy, and a completion recorder without exposing a private runtime object:

```python
from super_agent import CapabilityAction, CapabilityTool, SkillContribution
from runtime.safety import ActionEffect
from skill.manifest import Skill


class SearchCapability:
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
                    CapabilityAction(
                        (ActionEffect.NETWORK,),
                        "search:index",
                    ),
                ),
            ),
        )

    def run_search(self, arguments):
        return {"matches": []}
```

Tools are loaded progressively with the Skill that declares their content. Tool calls are always traced by Runtime. A model can disclose another Skill during a tool loop; its Capability contribution becomes available on the next model step.

Every contributed tool declares its effects and resource. Runtime checks that declaration
before calling the handler. Registered code cannot delegate authorization to Skill text.

Skills may evolve the content and configuration consumed by a registered Capability.
Executable Capability changes remain ordinary reviewed application-code changes. The
reserved `capability = "capability"` manifest value is rejected so a downloaded or
Agent-generated Skill cannot turn a Python file into trusted code.

# Capabilities

A Capability is code that executes one Skill mechanism. Runtime itself owns task lifecycle, progressive disclosure, tracing, evaluation, and evolution scheduling.

Built-in executors handle prompt, MCP, memory, and workflow Skills. Replace or add one explicitly:

```python
agent.add_skill_executor(executor)
```

An executor declares `name`, `version`, `capability_name`, `adds_model_context`, and `load_skill(request)`. There are no run-controller, disclosure, evaluator, or updater slots.

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

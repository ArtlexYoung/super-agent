# Capabilities

A Capability is executable mechanism code. Runtime mounts Capabilities; Skills carry every installable or evolvable implementation.

Built-in Capability slots include:

- `run_controller`
- `skill_disclosure`
- `skill_executor:<skill-type>`
- `run_result_evaluator`
- `skill_updater`

Code can replace a mechanism directly with explicit Agent methods:

```python
agent.set_run_controller(controller)
agent.set_skill_disclosure(disclosure)
agent.set_run_result_evaluator(evaluator)
agent.set_skill_updater(updater)
agent.add_skill_executor(executor)
```

## Capability Skills

Use a standard Skill directory when a mechanism must be packaged, selected, evaluated, or evolved:

```text
skills/capability/careful/
  skill.toml
  controller.py
```

```toml
schema_version = 2
name = "careful"
capability = "capability"
description = "Run controller for deliberate tasks"
version = "0.1.0"
triggers = []
agent_created = true
agent_can_update = true

[configuration]
slot = "run_controller"
entry_file = "controller.py"
entry_class = "CarefulRunController"
```

The zero-argument entry class must expose the same `name` and `version` and implement the selected slot interface. The Agent scans the central Skill index once at startup, validates each Capability Skill, and mounts it over the built-in implementation. Two Skills cannot claim the same slot.

Capability Skills use the ordinary commands:

```bash
super-agent skills install --config agent.toml --source ./careful
super-agent skills evolve --config agent.toml --name capability:careful \
  --goal "reduce failed runs" --cases evaluation-cases.json
super-agent skills rollback --config agent.toml --name capability:careful
```

The complete directory is the candidate. Validation checks the Python entry, class identity, slot interface, version, and immutable Skill identity. Promotion and rollback immediately remount the implementation in the Agent that created the evolution manager. Runtime evaluation attributes its behavior to `capability:<name>`, so freshness and autonomous recommendations use the same Skill evidence.

There is no Capability package directory, Capability candidate format, or Capability-specific CLI. The only lifecycle is:

```text
Skill package -> Skill candidate -> Skill evaluation -> Skill promotion -> Skill rollback
```

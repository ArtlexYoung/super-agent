# Capabilities

A Capability is executable mechanism code. A Skill carries content and configuration for that mechanism.

## Central Registry

Every Agent owns one `CapabilityRegistry`. Built-in mechanisms and code-selected replacements enter the same registry. Each registration has one immutable descriptor containing:

- Slot and stable name.
- Version and implementation class.
- Exact content SHA-256.
- Capability dependencies.
- Declared permissions.
- Agent creation and update ownership.

Runtime validates the dependency graph before a run and writes every descriptor into the run lock. Evaluation targets reuse the same descriptor and hash.

The common mechanisms keep explicit Agent methods:

```python
agent.set_run_controller(controller)
agent.set_skill_disclosure(disclosure)
agent.add_skill_executor(executor)
agent.set_run_result_evaluator(evaluator)
agent.set_skill_updater(updater)
```

## Local Packages

A local Capability package is a directory with `capability.toml` and a self-contained Python entry file:

```toml
schema_version = 1
slot = "run_controller"
name = "careful"
description = "A custom run controller"
version = "0.1.0"
entry_file = "capability.py"
entry_class = "Capability"
dependencies = []
permissions = ["execute"]
agent_created = false
agent_can_update = false
```

The entry class must have a zero-argument constructor and expose the `name` and `version` declared by the manifest. Its methods must match the selected slot.

Install and activate it in code:

```python
from super_agent import Agent

agent = Agent()
agent.install_capability("./careful-controller")
result = agent.run("handle this task")
```

Installed versions live under `.super-agent/capabilities/`. Installation does not modify Agent TOML and a fresh Agent does not execute installed code automatically. Select it explicitly with `load_installed_capability(slot, name)` so Agent composition remains visible in Python.

```python
agent.load_installed_capability("run_controller", "careful")
agent.update_capability("run_controller", "careful", "./careful-v2")
agent.rollback_capability("run_controller", "careful")
agent.remove_capability("run_controller", "careful")
```

CLI commands manage the same local versions:

```bash
super-agent capabilities list --output json
super-agent capabilities install --source ./careful-controller
super-agent capabilities update --slot run_controller --name careful --source ./careful-v2
super-agent capabilities rollback --slot run_controller --name careful
super-agent capabilities remove --slot run_controller --name careful
```

Package operations reject unknown manifest fields, identity changes, non-increasing versions, path traversal, symlinks, invalid interfaces, and dependency errors. Activation and rollback update one atomic state file.

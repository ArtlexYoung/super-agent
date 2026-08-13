# Getting Started

## Install and Check

Super Agent requires Python 3.11 or newer. The default runtime has no dependencies beyond
the standard library.

```bash
python3 -m pip install -e .
export OPENAI_API_KEY="..."
super-agent check
```

`check` validates the selected configuration, central Skill index, configured Skill
references, and default model readiness. It does not open storage or call a model. A failed
item names the exact layer to fix.

Use `ANTHROPIC_API_KEY`, `OLLAMA_HOST`, an explicit model Skill, or a Provider object in
Python when those fit better. Missing configuration and Provider failures are not replaced
with another implementation.

## Run

```bash
super-agent "Summarize this directory"
super-agent
```

The first command runs one stateless task. Its text output includes the answer and an
actual run summary. The second starts an in-process conversation without saving it. Add
`--save` when conversation history should survive a restart. Use
`SUPER_AGENT_PROVIDER=mock` only for an explicit offline test.

No project files are required or generated. Add strict `common.toml`, `cli.toml`, or
`code.toml` files only for settings owned by those scopes. Add a local `skills/` directory
only when the built-in Skills are not enough.

## Embed

```python
from super_agent import Agent

result = Agent().run("Return three test cases")
print(result.text)
```

This path is stateless and creates no files. A known Provider can be passed directly:

```python
from core.provider import MockProvider
from super_agent import Agent

agent = Agent(provider=MockProvider("offline response"))
```

The common `super_agent` facade exports only `Agent`. Advanced contracts live in `core`,
`skill`, or `adapter` so their ownership remains visible.

## Add Skills and Agents

Add a shared Skill root without editing TOML:

```python
agent.add_skill_path("team-skills")
agent.skills.enable("prompt:research")
```

Both operations update only this in-memory Agent. They do not write configuration or start
Runtime resources. The enabled reference is validated by the central Skill index before use.

Compose Agents in code:

```python
main = Agent()
worker = Agent()
main.add_subagent(worker, name="worker", description="Works on repository changes")
result = worker.run("Inspect this change", skill="code")
```

Omit `skill` to let the model judge available task Skills. An explicit task Skill limits
the run to that task mechanism. This choice does not mutate the Agent or later calls.

## Add State

```python
agent = Agent(use_storage=True)
user = agent.for_user("alice")
conversation = user.conversations.create("Project")
result = user.run("Remember this task", conversation_id=conversation.conversation_id)
user.runs.learn(result.run_id)
```

The default backend is readable JSONL under `.super-agent/`. Conversation messages are
short-term context; a selected memory Skill exposes durable long-term memory. Stateful
features fail if storage is disabled.

## Next

Run the small offline examples from the repository root:

```bash
PYTHONPATH=src python3 examples/minimal.py
PYTHONPATH=src python3 examples/custom_skill.py
PYTHONPATH=src python3 examples/team.py
```

- [Source tour](source-tour.md) follows one run through the implementation.
- [Skills](skills.md) explains the shared manifest and disclosure path.
- [Configuration](configuration.md) lists the small optional TOML surface.
- [Runtime](runtime.md) covers events, models, actions, and subagents.

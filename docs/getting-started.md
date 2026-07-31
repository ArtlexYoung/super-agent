# Getting Started

## Install

Super Agent requires Python 3.11 or newer. Its default install has no runtime dependencies.

```bash
python3 -m pip install -e .
```

Configure one model source. The shortest hosted setup is:

```bash
export OPENAI_API_KEY="..."
```

You can instead use `ANTHROPIC_API_KEY`, `OLLAMA_HOST`, a model Skill, or a Provider passed
in Python. Missing configuration and Provider errors are returned directly.

## Run

```bash
super-agent "Summarize this directory"
super-agent
```

The first command runs one task. The second starts an interactive stored conversation.
Use `SUPER_AGENT_PROVIDER=mock` only for an explicit offline smoke test.

No project files are needed. To create an editable example:

```bash
super-agent init --path my-agent
cd my-agent
super-agent "Use the local Skill"
```

Initialization writes `agent.toml` and `skills/prompt/echo/` only when they do not exist.

## Embed

```python
from super_agent import Agent

agent = Agent()
result = agent.run("Return three test cases")
print(result.text)
```

This path is stateless and creates no files. To use a known Provider object:

```python
from core.provider.chat import MockProvider
from super_agent import Agent

agent = Agent(provider=MockProvider("offline response"))
```

Advanced types intentionally live in `core`, `skill`, or `adapter`; the common
`super_agent` module exports only `Agent`.

## Add State

Storage is an explicit Python choice:

```python
agent = Agent(use_storage=True)
user = agent.for_user("alice")
conversation = user.conversations.create("Project")
result = user.run("Remember this task", conversation_id=conversation.conversation_id)
user.runs.learn(result.run_id)
```

The default backend is readable JSONL under `.super-agent/`. Conversation messages are
short-term context; memory Skills expose durable long-term memory. Stateful features fail
if storage is disabled.

## Add Skills and Agents

Project Skills live under any root listed in `[paths].skills`. Runtime indexes their
manifests first and opens full content only when selected.

```python
from super_agent import Agent

main = Agent()
worker = Agent()
worker.use_only_scenes("code")
main.add_subagent(worker, name="worker", description="Works on repository changes")
```

The model sees descriptions and chooses during its normal tool loop. Super Agent contains
no trigger-word table. `disable_scenes()` opts one Agent out of scenes;
`use_only_scenes("code")` limits its available scenes.

## Next

- [Skills](skills.md) explains manifests and progressive disclosure.
- [Configuration](configuration.md) lists the small optional TOML surface.
- [Runtime](runtime.md) covers state, actions, model calls, and subagents.
- [Source tour](source-tour.md) gives the shortest code-reading path.

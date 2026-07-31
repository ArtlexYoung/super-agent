# Source Tour

Read one ordinary run through these five files, in order:

1. `src/super_agent.py` exposes `Agent.run()` and composes optional dependencies.
2. `src/core/runtime.py` owns the Provider-neutral `Final` or `Actions` turn contract.
3. `src/core/provider/chat.py` converts each model API into `ModelResponse`.
4. `src/skill/disclosure/core.py` owns Skill discovery, disclosure, and cache paths.
5. `src/skill/task/loop.py` executes selected actions until the model returns `Final`.

The target call chain is deliberately short:

```text
Agent.run
  -> Runtime.run
  -> Provider.next_turn
  -> Final or checked Actions
```

Runtime may ask the model to read a Skill, run a Skill tool, switch to a configured model,
or call a registered subagent. It does not route by keywords. A simple request can return
`Final` from the first model call without loading planning, memory, storage, or evolution.

## Release Gates

- One model call for a simple prompt.
- No files created by a default Python run.
- No hidden Provider, Skill, model, or storage fallback.
- At most five common CLI command groups.
- At most five source files to follow an ordinary run.
- At most four owned business calls in the main execution chain.
- No Python source file above 500 non-import lines.
- Between 40 and 50 Python source files and fewer than 15,000 source lines.

These are release conditions, not documentation goals. Tests enforce them as each old
execution path is deleted.

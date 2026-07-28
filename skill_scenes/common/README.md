# Common scene

The common scene is the zero-configuration task chain. It combines one scene Skill with general prompt, memory, planner, and workflow Skills through stable `type:name` references.

Its default workflow is direct so a text-only model can complete general tasks without pretending tool support. An explicitly configured workflow may replace it through normal scene composition.

Its design uses public patterns from:

- [Hermes Agent](https://github.com/NousResearch/hermes-agent): conversation loop, context assembly, memory management, tool execution, turn finalization, and verification evidence.
- [OpenClaw](https://github.com/openclaw/openclaw): session-oriented agent execution, Skills, memory, and iterative tool use.
- General agent-harness practice: explicit task state, bounded loops, stopping conditions, evidence, verification, and recoverable traces. "Harness" is used here as an architecture pattern, not as a claim about one specific repository.

These references influence passive instructions only. Runtime still owns selection, actions, state, traces, evaluation, and stopping enforcement.

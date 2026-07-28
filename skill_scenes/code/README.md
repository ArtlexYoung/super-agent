# Code scene

The code scene is an optional full repository workflow. It groups prompt, memory, planner, and workflow Skills while Runtime continues to own tool authority, task state, model calls, traces, and evaluation.

Its design uses public, inspectable material from:

- [OpenAI Codex](https://github.com/openai/codex), especially the core task loop, instruction discovery, context management, tool handling, and turn diff tracking under `codex-rs/core/src/`.
- [Claude Code](https://github.com/anthropics/claude-code), specifically the public plugin, SDK, and documentation repository. The `feature-dev` and `pr-review-toolkit` plugins provide inspectable exploration, architecture, implementation, test-analysis, and review workflows. This repository does not claim that Claude Code's complete product core is open source.

The resulting chain is intentionally provider-neutral and tool-neutral. It describes expected coding behavior as passive Skill content rather than embedding filesystem or shell authority in the Skill.

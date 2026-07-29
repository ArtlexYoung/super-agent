# Explicit Actions and Skill Isolation

Super Agent checks one action contract before model-triggered tools and management side
effects. Trusted code declares what an operation can do. Skill text may request an
operation, but it cannot choose its actor, effects, resource, or rules.

## Effects

Every executable action declares at least one effect:

- `read`: inspect scoped data.
- `create`: create state or files.
- `update`: change or organize existing state.
- `delete`: remove state or append a deletion tombstone.
- `execute`: run registered code or an external process.
- `network`: communicate outside the Runtime process.
- `delegate`: run a subagent registered in code.

Core records `action.checked` before execution. Reads execute directly and finish with
`action.applied` or `action.failed`. State changes record `action.prepared` without
running the handler, followed by `action.applying` and `action.applied`; blocked actions
record `action.blocked`. Trace records contain argument names, never argument values.

A `SkillTool` has no default action. Missing metadata or a missing Runtime action runner
is an error before its handler runs.

## Code-Only Rules

Action authority is deliberately not a TOML field. Select it where the Agent is created:

```python
from super_agent import ActionMode, ActionRules, Agent

read_only = Agent(action_rules=ActionRules(ActionMode.READ_ONLY))
autonomous = Agent(action_rules=ActionRules(ActionMode.AUTONOMOUS))
```

The presets are:

- `standard`: the default; allows declared reads, registered code, subagent delegation,
  scoped Runtime state, and Agent-owned Skill updates. External execution, network access,
  and deletion require explicit authorization and are blocked before execution.
- `read_only`: allows only actions whose sole effect is `read`.
- `autonomous`: allows every declared action. Scope and input validation still apply.
- `audit`: records declarations without enforcing them. It must be selected explicitly.

CLI management commands identify themselves as user-requested operations. Model-generated
Skill content cannot impersonate that actor.

## No Hidden Execution

Skill manifests, instructions, resources, memory, tool output, and subagent output are
untrusted data. Core wraps them as untrusted model context and never interprets Skill files
as Python or shell code. The reserved Skill type `runner` is rejected.

Custom executable behavior must be registered with `Agent.add_skill_runner(...)`. MCP
implementations use the narrower `Agent.add_mcp_server(...)` API. Commands, arguments,
environment values, transports, and effects live only in trusted application code; an MCP
Skill can select only a registered server name. Preflight rejects a selected MCP Skill when
that registration is absent, and every discovery or tool call is checked before code or a
process can start.

Package validation also rejects symlinks and paths outside a Skill directory. Candidate
Skills remain outside active roots until validation and no-regression evaluation pass.
Promotion reads only the report bound into evolution state and verifies its file hash,
case-set hash, candidate hash, and baseline hash. Changed or unrecorded reports cannot
authorize a write. The copied candidate and current target are checked again before the
atomic switch. Failed Runtime refresh, state publication, or rollback restores the prior
verified directory; a conflicting third state is reported rather than overwritten.
Provider failures, memory-organization failures, invalid candidates, and blocked actions
surface as errors rather than alternate behavior.

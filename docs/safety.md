# Runtime Safety

Super Agent uses one action check before model-triggered or management side effects.
Capabilities declare what an operation can do; Skill content can request those operations,
but cannot grant itself permission.

## Effects

Every action declares at least one effect:

- `read`: inspect scoped data without changing it.
- `create`: create scoped state or files.
- `update`: replace or organize existing state.
- `delete`: remove state or create a deletion tombstone.
- `execute`: run registered code or an external process.
- `network`: communicate outside the Runtime process.
- `delegate`: run a subagent registered in code.

Runtime records `action.checked` before execution. Allowed actions then record
`action.completed` or `action.failed`; denied or approval-gated actions record
`action.blocked`. Audit records contain argument names, never argument values.

## Presets

The optional Agent setting selects one preset:

```toml
[agent]
safety = "standard"
```

- `standard` allows reads, registered code, subagent delegation, memory lifecycle
  changes, and Agent-owned Skill candidate changes. External execution, unknown
  network access, and destructive external changes require approval.
- `read_only` allows only declared reads.
- `autonomous` allows every declared action. Scope and input validation still apply.
- `audit` records decisions without enforcing them and is intended only for migration.

`standard` is the zero-configuration default. Explicit CLI management commands use a
`user:` actor and therefore count as user-authorized actions. A Skill cannot choose its
actor, effect, resource, or safety preset.

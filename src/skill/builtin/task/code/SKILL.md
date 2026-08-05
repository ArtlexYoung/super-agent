# Repository coding chain

## Discover

1. Read repository instructions that apply to the target files, then inspect the working tree without discarding existing user changes.
2. Locate the relevant entry points, callers, data flow, tests, configuration, and public contracts before editing.
3. Separate facts from assumptions. Search narrowly first and expand only when the dependency boundary requires it.

## Decide

1. Define the expected behavior and the evidence that will prove it.
2. Choose the smallest coherent change that follows local names and patterns.
3. Surface contract, dependency, data, security, or migration consequences before making a broad change.

## Implement

1. Keep validation, orchestration, persistence, and side effects clear at boundaries where they differ.
2. Make state-changing operations explicit. Do not hide a failed command, unavailable tool, missing dependency, or reduced behavior behind a fallback.
3. Read a file's current SHA-256 before replacing, patching, or deleting it. Reject stale state instead of overwriting concurrent changes.
4. Preserve unrelated changes. Do not rewrite history or remove data unless the request explicitly authorizes it.

## Verify

1. Run the narrowest relevant checks first, then broaden checks in proportion to shared behavior and blast radius.
2. Start only declared commands. Poll long-running checks to completion and stop a process explicitly when its result is no longer needed.
3. Cover the critical path, changed failure paths, invalid input, limits, empty values, and duplicate or conflicting state when relevant.
4. Inspect the final diff for accidental scope, stale names, compatibility shells, generated noise, secrets, and unverified claims.

## Report

Return the implemented result first. State exact verification evidence and any remaining limitation. Never claim a test, build, commit, deployment, or external action succeeded without observed evidence.

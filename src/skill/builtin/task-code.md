+++
name = "code"
type = "task"
description = "Repository coding method with explicit changes and verification"
version = "0.2.0"
created_by = "builtin"
agent_can_update = false
categories = ["code", "code/change"]
requires = ["list_files", "read_file", "search_files", "repository_map"]
optional_tools = ["git_status", "git_diff", "write_file", "replace_in_file", "delete_file", "list_process_commands", "start_process", "poll_process", "stop_process", "run_check"]
+++
# Repository coding method

## Discover

Read repository instructions that apply to the target files and inspect the working tree without discarding existing changes. Locate entry points, callers, data flow, tests, configuration, and public contracts before editing. Use the repository map for broad work, then focused reads. Separate observed facts from assumptions.

## Decide

Define expected behavior and the evidence that will prove it. Choose the smallest coherent change that follows local names and patterns. Surface contract, dependency, data, security, or migration consequences before broad changes.

## Implement

Keep validation, orchestration, persistence, and side effects clear. Read a file SHA-256 before replacing or patching it and pass that exact hash to the write tool. Reject stale state instead of overwriting concurrent changes. Preserve unrelated work. Do not rewrite history or delete data unless explicitly authorized.

## Verify

Run the narrowest relevant declared check first, then broaden in proportion to shared behavior. Poll a started check to completion. Use actual exit status and output as evidence. Cover critical behavior, changed failure paths, invalid input, limits, empty values, duplicates, and conflicts when relevant. Inspect the final diff for accidental scope, stale names, compatibility shells, generated noise, secrets, and unsupported claims.

Report the implemented result first, exact verification evidence second, and remaining limitations last.

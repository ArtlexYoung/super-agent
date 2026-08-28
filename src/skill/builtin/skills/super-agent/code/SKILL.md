---
name: "code"
description: "Repository coding method with explicit changes and verification"
metadata:
  super-agent-categories: "[\"code\",\"code/change\"]"
  super-agent-optional-tools: "[\"git_status\",\"git_diff\",\"write_file\",\"replace_in_file\",\"delete_file\",\"list_process_commands\",\"start_process\",\"poll_process\",\"stop_process\",\"run_check\"]"
  super-agent-requires: "[\"list_files\",\"read_file\",\"search_files\",\"repository_map\"]"
  super-agent-type: "task"
  super-agent-version: "0.2.22"
---
# Repository coding method

## Discover

Read repository instructions that apply to the target files and inspect the working tree without discarding existing changes. Locate entry points, callers, data flow, tests, configuration, and public contracts before editing. Use the repository map for broad work, then focused reads. Separate observed facts from assumptions.

The Code Skill loads `AGENTS.md` files from the workspace parent chain before the current directory. Treat each file as scoped project guidance and keep the nearest instructions in force for the files they cover.

## Decide

Define expected behavior and the evidence that will prove it. Choose the smallest coherent change that follows local names and patterns. Surface contract, dependency, data, security, or migration consequences before broad changes.

## Implement

Keep validation, orchestration, persistence, and side effects clear. Read a file SHA-256 before replacing or patching it and pass that exact hash to the write tool. Reject stale state instead of overwriting concurrent changes. Preserve unrelated work. Do not rewrite history or delete data unless explicitly authorized.

## Verify

Run the narrowest relevant declared check first, then broaden in proportion to shared behavior. Poll a started check to completion. Use actual exit status and output as evidence. Cover critical behavior, changed failure paths, invalid input, limits, empty values, duplicates, and conflicts when relevant. Inspect the final diff for accidental scope, stale names, compatibility shells, generated noise, secrets, and unsupported claims.

Report the implemented result first, exact verification evidence second, and remaining limitations last.

The workspace tools use SHA-256 preconditions for edits, bounded process execution for verification, and read-only Git inspection by default. Independent review remains a separate Skill and must be requested when the task benefits from another perspective.

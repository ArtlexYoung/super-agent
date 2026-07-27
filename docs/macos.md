# macOS App

The SwiftUI app in `src/frontend/mac` is a desktop client for the same Python runtime.

## Features

- Runtime-backed conversation list and multi-turn history.
- Visual Chinese TOML configuration.
- Editable model Skill list with connection, purpose, capability, quality, latency, cost, and evolution settings.
- Skill, MCP, memory, and workflow selection.
- Skill freshness display.
- Main Agent and subagent execution tree.
- Per-run scheduler reasons, model calls, routing evidence, Skill freshness, and automatic evolution decisions.
- Conversation output from the runtime JSONL protocol.

The app reads the central Skill index, model Skills, run insight, and conversations through the CLI. It does not maintain a second Skill parser, evidence calculator, or conversation store.

## Run During Development

Install the Python command first:

```bash
python3 -m pip install -e .
swift run --package-path src/frontend/mac SuperAgentMac
```

Or build it:

```bash
swift build --package-path src/frontend/mac
```

## Runtime Integration

The app sends requests using:

```bash
super-agent run --config agent.toml --request-stdin --output jsonl
```

It loads Skill state using:

```bash
super-agent skills index --config agent.toml --output json
```

It manages model Skills and run insight using:

```bash
super-agent models list --config agent.toml --output json
super-agent models save --config agent.toml --request-stdin --output json
super-agent models remove --config agent.toml --name <name> --output json
super-agent runs explain --config agent.toml --run-id <run-id> --output json
```

The model editor writes standard `model` Skills under the first configured Skill root. It stores actual API keys in macOS Keychain and passes them to Runtime only through the configured environment-variable names. Secrets never enter `agent.toml`, `skill.toml`, Application Support `config.json`, run events, or runtime locks.

The app manages conversations using `super-agent conversations` and sends `user_id` plus `conversation_id` in every run request. The selected Runtime storage backend is the only conversation source of truth. Application Support stores only UI selection, visual Agent TOML settings, and the bound configuration path. When no file is bound, relative Skill and storage paths resolve under the app's Application Support project directory. Assistant records contain `run_result`, allowing the UI to rebuild the main Agent and subagent tree after restart.

## Package a Local Release

```bash
src/frontend/mac/package_release.sh
```

The script reads the version from `pyproject.toml`, builds a release executable, creates an `.app` bundle, and writes a versioned ZIP under `release/mac/`.

The local app is unsigned. macOS may require opening it through Finder's context menu on first launch.

See [the app-specific README](../src/frontend/mac/README.md) for storage details and development notes.

# macOS App

The SwiftUI app in `src/frontend/mac` is a desktop client for the same Python runtime.

The Python runtime now uses model Skills. The current desktop model editor still writes the removed `[model]` table and is intentionally pending migration in `v0.0.39`; use environment discovery or edit model Skills directly until that release.

## Features

- Runtime-backed conversation list and multi-turn history.
- Visual Chinese TOML configuration.
- Editable model list with provider, model, URL, and key-variable settings.
- Skill, MCP, memory, and workflow selection.
- Skill freshness display.
- Main Agent and subagent execution tree.
- Conversation output from the runtime JSONL protocol.

The app reads the central Skill index and conversations through the CLI. It does not maintain a second Skill manifest parser or conversation store.

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

It manages conversations using `super-agent conversations` and sends `user_id` plus `conversation_id` in every run request. The selected Runtime storage backend is the only conversation source of truth. The app stores only UI selection, TOML settings, and model profiles in Application Support. Assistant message records include the complete `run_result`, allowing the UI to rebuild the main Agent and subagent execution tree after restart.

## Package a Local Release

```bash
src/frontend/mac/package_release.sh
```

The script reads the version from `pyproject.toml`, builds a release executable, creates an `.app` bundle, and writes a versioned ZIP under `release/mac/`.

The local app is unsigned. macOS may require opening it through Finder's context menu on first launch.

See [the app-specific README](../src/frontend/mac/README.md) for storage details and development notes.

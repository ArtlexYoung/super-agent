# macOS App

The SwiftUI app in `src/frontend/mac` is a desktop client for the same Python runtime.

## Features

- Conversation list and persisted conversation JSON.
- Visual Chinese TOML configuration.
- Editable model list with provider, model, URL, and key-variable settings.
- Skill, MCP, memory, and workflow selection.
- Skill freshness display.
- Main Agent and subagent execution tree.
- Conversation output from the runtime JSONL protocol.

The app reads the central Skill index through the CLI. It does not maintain a second Skill manifest parser.

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

Conversation records are stored separately from Python runtime state. Python run IDs are preserved so the UI can link a conversation node to its trace and subagent children.

## Package a Local Release

```bash
src/frontend/mac/package_release.sh
```

The script reads the version from `pyproject.toml`, builds a release executable, creates an `.app` bundle, and writes a versioned ZIP under `release/mac/`.

The local app is unsigned. macOS may require opening it through Finder's context menu on first launch.

See [the app-specific README](../src/frontend/mac/README.md) for storage details and development notes.

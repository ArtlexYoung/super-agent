# Web Client

The Vite, React, TypeScript, and shadcn/ui client in `web/` is a thin Chinese interface over the Python Runtime. It does not own a Skill parser, conversation store, evidence calculator, or execution engine.

## Start

Install Super Agent and start one server:

```bash
python3 -m pip install -e .
super-agent serve
```

Open `http://127.0.0.1:8765/`. The standard-library Python server hosts the production assets, management API, and AG-UI SSE endpoint on one origin.

## Features

- Runtime-backed conversation creation, rename, clear, delete, and multi-turn chat.
- Live task progress through AG-UI events.
- Main Agent and subagent run tree with scheduling, model, freshness, and evolution evidence.
- Chinese visual Agent configuration with hover help.
- Skill, MCP, memory, workflow, and model lists backed by the central Skill index.
- Skill enablement, selection, default behavior, ownership, update permission, and freshness state.
- Model Skill creation and editing for provider, model name, address, credential environment variable, routing traits, cost, and evolution permissions.
- Runtime memory inspection and explicit forgetting.

Configuration changes are validated by the Python Runtime and written atomically to `agent.toml` or standard model Skill manifests. The selected storage backend remains the source of truth for conversations, runs, memory, and evolution.

## Credential Boundary

The browser never accepts or persists a raw provider secret. The model editor stores an environment-variable name such as `OPENAI_API_KEY`; the provider reads the value from the server process environment. Secret values do not enter `agent.toml`, `skill.toml`, Web storage, Runtime events, or runtime locks.

## Develop

Run the Python server in one terminal:

```bash
PYTHONPATH=src python3 -m cli serve
```

Run Vite in another terminal:

```bash
cd web
pnpm install
pnpm dev
```

Vite proxies `/api`, `/ag-ui`, and `/health` to `127.0.0.1:8765`. Build production assets with:

```bash
pnpm lint
pnpm build
```

The build writes to `src/ag_ui_bridge/static`, so packaged Python distributions can serve the client without Node.js.

## Security Boundary

The local server applies request-size, origin, path traversal, content-type, CSP, clickjacking, MIME-sniffing, and referrer protections. It intentionally has no authentication or TLS. Keep it bound to loopback, or place authentication and TLS in a reverse proxy before exposing it to a network.

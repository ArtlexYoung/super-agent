# Web Client

The `web/` project is a small Chinese React, TypeScript, and shadcn/ui client over the
Python Core. It does not parse Skills, own conversation state, calculate evidence, or run
a second task engine.

## Start

```bash
python3 -m pip install -e .
super-agent serve
```

Open `http://127.0.0.1:8765/`. The standard-library Python server hosts the built client,
management API, and AG-UI endpoint on one origin.

The left navigation contains:

- `对话`: conversation management, streamed tasks, and main/subagent run trees.
- `CopilotKit`: a lazy-loaded headless integration example using the official CopilotKit
  React context and `@ag-ui/client` against the same `POST /ag-ui` endpoint.
- `配置`: Chinese visual editing for Agent fields, model Skills, task Skills, all other
  Skill types, ownership/update permission, freshness, memory, and storage-backed state.

The CopilotKit page reuses the selected persisted conversation ID. If no conversation
exists it displays an explicit create button; opening the page never creates hidden state.
The normal conversation page does not load the CopilotKit bundle. The example uses the
public headless API and a small local message view instead of importing optional rich-text
chat renderers.

## Data Ownership

Configuration writes are validated by Python and saved atomically to `common.toml` or a
user model Skill. The selected storage backend remains authoritative for conversations,
runs, memory, and explicit Skill changes. Skill lists come from the central progressive index and group
by their actual `type`, including custom types.

Task Skills appear in their own group. Choosing one fixes that task Skill and clears any other
fixed task Skill; leaving every task Skill unchecked keeps model selection. The default badge
describes fallback selection and does not silently write configuration.

Memory management shows only durable long-term items. Current conversation messages are
the short-term context and are not copied into memory storage. Forgetting an item is an
explicit management action and does not erase the append-only history. The main model may
explicitly merge, replace, or forget recalled items while handling the conversation.

The browser stores no raw Provider secret. Model configuration records an environment
variable name such as `OPENAI_API_KEY`; only the server process resolves its value.
Secrets do not enter TOML, Web storage, or events.

## Develop and Build

Run the Python server:

```bash
PYTHONPATH=src SUPER_AGENT_PROVIDER=mock python3 -m cli serve
```

Run Vite separately:

```bash
cd web
pnpm install
pnpm dev
```

Vite proxies `/api`, `/ag-ui`, and `/health` to `127.0.0.1:8765`.

```bash
pnpm lint
pnpm typecheck
pnpm build
```

The production build writes to `src/adapter/static`, so packaged Python
distributions can serve it without Node.js.

## Network Boundary

The local server enforces request size, origin, path traversal, content type, CSP,
clickjacking, MIME sniffing, and referrer protections. It intentionally has no login or
TLS. Keep it bound to loopback or put authentication and TLS in a reverse proxy before
network exposure.

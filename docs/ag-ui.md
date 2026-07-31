# AG-UI Adapter

Super Agent exposes the same Core task path through AG-UI. The adapter validates protocol
input, calls `Agent.run(...)`, and maps canonical Runtime events to server-sent events.
It does not own model selection, conversations, tools, or another execution engine.

```text
AG-UI RunAgentInput
  -> Agent.run(...)
  -> canonical Runtime events and storage
  -> AG-UI SSE events
```

## Start

```bash
super-agent serve
```

The default routes are:

- `GET http://127.0.0.1:8765/`: built React client.
- `POST http://127.0.0.1:8765/ag-ui`: AG-UI run endpoint.
- `/api/*`: same-origin management operations.
- `GET http://127.0.0.1:8765/health`: health check.

`POST /ag-ui` requires `Content-Type: application/json`. `threadId` is the persisted
conversation ID and `runId` is the canonical Runtime run ID. The latest non-empty user
message becomes the task prompt.

```json
{
  "threadId": "project-a",
  "runId": "run-001",
  "state": {},
  "messages": [
    {"id": "message-001", "role": "user", "content": "Explain this project"}
  ],
  "tools": [],
  "context": [],
  "forwardedProps": {"scene": "code"}
}
```

`forwardedProps.scene` is optional and selects one scene for this run. It accepts a scene
name such as `code` or a stable key such as `scene:code`. When omitted, available scene
descriptions remain in the central Skill index and the model may activate one during its
normal turn. It cannot select a user, override an explicit scene, or change action
authority.

The response uses `text/event-stream` and official SSE framing:

```text
data: {"type":"RUN_STARTED","threadId":"project-a","runId":"run-001"}

```

## Event Mapping

| Runtime event | AG-UI event |
| --- | --- |
| `run.started` | `RUN_STARTED` |
| `task.started` | `STEP_STARTED` |
| `tool.requested` | `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END` |
| `tool.completed`, `tool.failed` | `TOOL_CALL_RESULT` |
| `task.completed` | `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END` |
| `run.completed` | `RUN_FINISHED` |
| `run.failed` | `RUN_ERROR` |
| every canonical event | `CUSTOM` with sequence and payload |

Provider calls are currently non-streaming. Tool progress arrives live, while final
assistant text is emitted after the model loop completes. Events are forwarded
from the active run and are not reconstructed from logs.

## Python and CopilotKit

Embed the server with the public library API:

```python
from adapter.ag_ui_adapter import create_ag_ui_server
from super_agent import Agent

server = create_ag_ui_server(Agent(use_storage=True))
server.serve_forever()
```

The Web client's CopilotKit page uses `HttpAgent` from `@ag-ui/client`,
`CopilotKitCoreReact` from the public `/v2/context` entry, and `useAgent` from the public
`/v2/headless` entry. It renders a small project-native chat against `/ag-ui` and reuses
an explicitly created conversation ID. The example is lazy-loaded, so the native chat
does not load CopilotKit and the packaged client does not include its optional rich-chat
renderers.

## Network and Identity Boundary

The default server listens on `127.0.0.1`, limits request bodies to 1 MiB, validates
content types, and allows only configured browser origins. `--user-id` fixes one Runtime
user when the server starts; client-controlled state and forwarded properties cannot
select another user.

The server has no authentication or TLS. Keep it on loopback, or put an authenticated TLS
reverse proxy in front of it and configure each allowed origin explicitly. Core still
checks every declared action before its handler runs; AG-UI only transports the resulting
events.

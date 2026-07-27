# AG-UI Bridge

Super Agent exposes its existing Runtime through the AG-UI protocol without adding a Python dependency or a second execution path.

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

- `GET http://127.0.0.1:8765/` for the built React client
- `POST http://127.0.0.1:8765/ag-ui`
- `/api/*` for same-origin Runtime management operations
- `GET http://127.0.0.1:8765/health`

`POST /ag-ui` requires `Content-Type: application/json` and the official AG-UI input fields. Super Agent currently consumes text user messages; `threadId` becomes the persisted conversation ID and `runId` becomes the canonical Runtime run ID.

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
  "forwardedProps": {}
}
```

The response uses `text/event-stream`. Each frame follows the official SSE encoding:

```text
data: {"type":"RUN_STARTED","threadId":"project-a","runId":"run-001"}

```

## Event Mapping

| Runtime event | AG-UI event |
| --- | --- |
| `run.started` | `RUN_STARTED` |
| `task.started` | `STEP_STARTED` |
| `task.step.scheduled` | `STEP_STARTED` |
| `task.step.completed` | `STEP_FINISHED` |
| `tool.requested` | `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END` |
| `tool.completed`, `tool.failed` | `TOOL_CALL_RESULT` |
| `task.completed` | `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END` |
| `run.completed` | `RUN_FINISHED` with a success outcome |
| `run.failed` | `RUN_ERROR` |
| every Runtime event | `CUSTOM` with its canonical sequence and payload |

Provider calls are currently non-streaming, so Runtime progress arrives live while the final assistant text is emitted as one content delta after the model step completes. No events are reconstructed from logs after the run.

## Embed in Python

```python
from super_agent import Agent, create_ag_ui_server

server = create_ag_ui_server(Agent())
server.serve_forever()
```

## Security Boundary

The default server listens only on `127.0.0.1`, accepts request bodies up to 1 MiB, and allows browser calls only from the configured origin list. `--user-id` fixes one Runtime user scope when the server starts; client-controlled `forwardedProps` never selects another user.

The server has no authentication or TLS. Keep the default local binding for local use. Protect any non-local binding with an authenticated reverse proxy and an explicit `--allow-origin`. Runtime Safety still authorizes every model-triggered action before its handler runs; AG-UI only observes the resulting canonical events.

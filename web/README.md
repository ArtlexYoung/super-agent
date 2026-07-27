# Super Agent Web

The React client is a thin view over the Python Runtime. It uses the management
JSON API for stored state and AG-UI SSE for live runs.

```bash
# Terminal 1, from the repository root
PYTHONPATH=src python3 -m cli serve

# Terminal 2
cd web
pnpm install
pnpm dev
```

Vite proxies `/api`, `/ag-ui`, and `/health` to `127.0.0.1:8765`. A production
build writes directly to `src/ag_ui_bridge/static`, which lets the dependency-free
Python server host the client at `http://127.0.0.1:8765/`.

```bash
pnpm lint
pnpm build
```

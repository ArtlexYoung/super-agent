# Super Agent Web

This React client is a thin view over Super Agent Core. The management JSON API reads and
updates stored state, while AG-UI streams live runs. The CopilotKit page is a lazy-loaded
headless example over the same AG-UI endpoint and uses the project's small chat view.

```bash
# Terminal 1, from the repository root
PYTHONPATH=src SUPER_AGENT_PROVIDER=mock python3 -m cli serve

# Terminal 2
cd web
pnpm install
pnpm dev
```

Vite proxies `/api`, `/ag-ui`, and `/health` to `127.0.0.1:8765`. A production
build writes to `src/adapter/static` for the dependency-free Python server
to host at `http://127.0.0.1:8765/`.

```bash
pnpm lint
pnpm typecheck
pnpm build
```

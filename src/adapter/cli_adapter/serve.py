"""CLI command for the dependency-free AG-UI HTTP server."""

from __future__ import annotations

import argparse

from adapter.cli_adapter import load_agent
from adapter.ag_ui_adapter.server import DEFAULT_ALLOWED_ORIGINS, create_ag_ui_server
from core.identity import LOCAL_USER_ID


def configure_serve_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--user-id", default=LOCAL_USER_ID)
    parser.add_argument(
        "--allow-origin",
        action="append",
        dest="allowed_origins",
        help="browser origin allowed to call the server; may be repeated",
    )


def run_serve_command(args: argparse.Namespace) -> int:
    agent = load_agent(args.config)
    origins = tuple(args.allowed_origins or DEFAULT_ALLOWED_ORIGINS)
    server = create_ag_ui_server(
        agent,
        args.host,
        args.port,
        user_id=args.user_id,
        allowed_origins=origins,
    )
    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}"
    print(f"Super Agent Web UI: {base_url}/")
    print(f"Super Agent AG-UI endpoint: {base_url}/ag-ui")
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        print("Warning: this server has no authentication; protect non-local bindings.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0

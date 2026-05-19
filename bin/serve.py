#!/usr/bin/env python3
"""Launch the oyster-agent-runner HTTP server.

Usage
-----
    python bin/serve.py --port 8089 [--host 0.0.0.0] [--reload]

Requires
--------
  * ``OYSTER_API_TOKEN`` env var must be set (the server refuses to start
    otherwise — see ``server.create_app``).
  * ``fastapi`` and ``uvicorn[standard]`` installed in the active env.

This is intentionally tiny: it parses CLI flags, prints a friendly message
about the auth requirement, and hands off to uvicorn. All real logic lives
in ``oyster_agent_runner.server``.
"""

from __future__ import annotations

import argparse
import os
import sys


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="serve.py",
        description="Launch the oyster-agent-runner HTTP API.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default: 127.0.0.1; use 0.0.0.0 for public).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8089,
        help="Bind port (default: 8089).",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload (dev only).",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="uvicorn log level.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not os.environ.get("OYSTER_API_TOKEN"):
        print(
            "ERROR: OYSTER_API_TOKEN is not set. The server refuses to launch "
            "without an auth token. Export OYSTER_API_TOKEN=<secret> and retry.",
            file=sys.stderr,
        )
        return 2

    try:
        import uvicorn  # type: ignore[import-not-found]
    except ImportError:
        print(
            "ERROR: uvicorn is not installed. Install with: "
            "pip install 'fastapi>=0.110' 'uvicorn[standard]>=0.27'",
            file=sys.stderr,
        )
        return 2

    # Import the factory after the token check so a token-less invocation
    # never imports fastapi unnecessarily.
    from oyster_agent_runner.server import create_app

    app = create_app()

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

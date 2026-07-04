#!/usr/bin/env python3
"""
Health Check Endpoint for G125 Production Operations.

Thin HTTP server reporting last_clip_at, disk_free, and queue_depth metrics.
Designed for operational monitoring and health checks.
"""

import argparse
import json
import logging
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from shutil import disk_usage
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level constants
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
STATE_FILE = Path("/var/lib/g125/state.json")
QUEUE_DIR = Path("/var/lib/g125/queue")


def get_last_clip_at(state_file: Path) -> Optional[float]:
    """Read last clip timestamp from state file."""
    if not state_file.exists():
        return None
    try:
        data = json.loads(state_file.read_text())
        return data.get("last_clip_at")
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "health_check: failed to read state file %s: %s: %s",
            state_file,
            type(exc).__name__,
            exc,
        )
        return None


def get_disk_free(path: str = "/") -> int:
    """Get free disk space in bytes."""
    return disk_usage(path).free


def get_queue_depth(queue_dir: Path) -> int:
    """Get current queue depth by counting pending items."""
    if not queue_dir.exists():
        return 0
    try:
        return sum(1 for f in queue_dir.iterdir() if f.is_file() and not f.name.startswith("."))
    except OSError as exc:
        logger.warning(
            "health_check: failed to read queue dir %s: %s: %s",
            queue_dir,
            type(exc).__name__,
            exc,
        )
        return 0


def collect_metrics(
    state_file: Path = STATE_FILE,
    queue_dir: Path = QUEUE_DIR,
) -> dict:
    """Collect all health metrics."""
    return {
        "last_clip_at": get_last_clip_at(state_file),
        "disk_free": get_disk_free(),
        "queue_depth": get_queue_depth(queue_dir),
        "timestamp": time.time(),
    }


class HealthCheckHandler(BaseHTTPRequestHandler):
    """HTTP request handler for health check endpoint."""

    def log_message(self, format: str, *args) -> None:
        """Suppress default logging for cleaner output."""
        pass

    def do_GET(self) -> None:
        """Handle GET requests."""
        if self.path in ("/health", "/healthz", "/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            metrics = collect_metrics()
            self.wfile.write(json.dumps(metrics, indent=2).encode())
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "not found"}')

    def do_HEAD(self) -> None:
        """Handle HEAD requests for health checks."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()


def run_server(host: str, port: int) -> None:
    """Run the HTTP health check server."""
    server_address = (host, port)
    httpd = HTTPServer(server_address, HealthCheckHandler)
    print(f"Health check endpoint running on http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        httpd.shutdown()


def main(argv: Optional[list] = None) -> int:
    """Main entry point with argparse CLI."""
    parser = argparse.ArgumentParser(
        description="G125 Health Check Endpoint - HTTP server for ops metrics"
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Host to bind to (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to listen on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=STATE_FILE,
        help="Path to state file for last_clip_at",
    )
    parser.add_argument(
        "--queue-dir",
        type=Path,
        default=QUEUE_DIR,
        help="Path to queue directory",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Print metrics once and exit (for testing)",
    )

    args = parser.parse_args(argv)

    if args.once:
        metrics = collect_metrics(state_file=args.state_file, queue_dir=args.queue_dir)
        print(json.dumps(metrics, indent=2))
        return 0

    run_server(args.host, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())

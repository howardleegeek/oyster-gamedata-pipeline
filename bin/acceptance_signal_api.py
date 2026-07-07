#!/usr/bin/env python3
"""
G013 Acceptance Signal API

Webhook API to notify vendor of accept/reject status.
Sends HTTP POST requests with JSON payloads containing signal type,
transaction ID, timestamp, and optional metadata.
"""

import argparse
import json
import logging
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def send_signal(
    url: str,
    signal: str,
    transaction_id: str,
    metadata: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Tuple[int, str]:
    """
    Send accept/reject signal to vendor webhook.

    Args:
        url: Webhook endpoint URL.
        signal: Either 'accept' or 'reject'.
        transaction_id: Unique transaction identifier.
        metadata: Optional additional metadata as dictionary.
        timeout: Request timeout in seconds.

    Returns:
        Tuple of (HTTP status code, response body).

    Raises:
        ValueError: If signal is invalid or URL is malformed.
        RuntimeError: If connection fails.
    """
    if signal not in ("accept", "reject"):
        raise ValueError(f"Signal must be 'accept' or 'reject', got '{signal}'")

    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid URL format: {url}")

    payload = {
        "signal": signal,
        "transaction_id": transaction_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if metadata:
        payload["metadata"] = metadata

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "G013-Acceptance-Signal-API/1.0",
    }

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        return e.code, error_body
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection failed: {e.reason}") from e


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Send acceptance/rejection signal to vendor webhook",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --url https://vendor.example/webhook accept TXN-12345
  %(prog)s --url https://vendor.example/webhook reject TXN-67890 -m '{"reason":"quality"}'
        """,
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Webhook endpoint URL",
    )
    parser.add_argument(
        "signal",
        choices=["accept", "reject"],
        help="Signal type to send",
    )
    parser.add_argument(
        "transaction_id",
        help="Unique transaction identifier",
    )
    parser.add_argument(
        "-m",
        "--metadata",
        type=json.loads,
        help="Optional JSON metadata to include",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress output on success",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """
    Main entry point for acceptance signal API CLI.

    Args:
        argv: Command line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code: 0 on success, 1 on failure.
    """
    if argv is None:
        argv = sys.argv[1:]

    args = parse_args(argv)

    try:
        status, body = send_signal(
            url=args.url,
            signal=args.signal,
            transaction_id=args.transaction_id,
            metadata=args.metadata,
            timeout=args.timeout,
        )
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"Status: {status}")
        try:
            resp_json = json.loads(body)
            print(f"Response: {json.dumps(resp_json, indent=2)}")
        except json.JSONDecodeError as e:
            logger.debug("Failed to parse JSON response: %s", e)
            print(f"Response: {body}")

    if 200 <= status < 300:
        return 0
    print(f"Request failed with status {status}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
acceptance_signal_api.py

Webhook API to notify vendor of accept/reject status.

This module provides a CLI tool and programmatic interface to send acceptance
signals (accept/reject) to a vendor's webhook endpoint via HTTP POST requests
with JSON payloads containing the signal type and associated metadata.
"""

import argparse
import json
import sys
import urllib.request
import urllib.error
from typing import Optional


def send_webhook(
    url: str,
    signal_type: str,
    transaction_id: str,
    payload: Optional[dict] = None,
    timeout: int = 30,
) -> tuple[int, str]:
    """
    Send acceptance signal to vendor webhook endpoint.

    Args:
        url: Webhook endpoint URL.
        signal_type: Either "accept" or "reject".
        transaction_id: Unique identifier for the transaction.
        payload: Optional additional metadata to include.
        timeout: Request timeout in seconds.

    Returns:
        Tuple of (HTTP status code, response body).

    Raises:
        ValueError: If signal_type is invalid.
        RuntimeError: If connection fails.
    """
    if signal_type not in ("accept", "reject"):
        raise ValueError(
            f"Invalid signal_type: {signal_type}. Must be 'accept' or 'reject'."
        )

    data = {
        "signal": signal_type,
        "transaction_id": transaction_id,
        "timestamp": _get_iso_timestamp(),
    }
    if payload:
        data["metadata"] = payload

    json_data = json.dumps(data).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    request = urllib.request.Request(
        url,
        data=json_data,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
            return response.status, response_body
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        return e.code, error_body
    except urllib.error.URLError as e:
        raise RuntimeError(f"Failed to connect to webhook: {e.reason}") from e


def _get_iso_timestamp() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Send acceptance/rejection signal to vendor webhook.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --url https://vendor.example.com/webhook accept TXN-12345
  %(prog)s --url https://vendor.example.com/webhook reject TXN-67890 --metadata '{"reason": "quality"}'
  %(prog)s --url https://vendor.example.com/webhook accept TXN-11111 --timeout 60
        """,
    )
    parser.add_argument(
        "signal",
        choices=["accept", "reject"],
        help="Accept or reject signal to send",
    )
    parser.add_argument(
        "transaction_id",
        help="Unique transaction identifier",
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Vendor webhook endpoint URL",
    )
    parser.add_argument(
        "--metadata",
        type=json.loads,
        help="Optional JSON metadata to include in webhook payload",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output on success",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """
    Main entry point for CLI execution.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code: 0 on success, non-zero on failure.
    """
    if argv is None:
        argv = sys.argv[1:]

    args = parse_args(argv)

    try:
        status_code, response_body = send_webhook(
            url=args.url,
            signal_type=args.signal,
            transaction_id=args.transaction_id,
            payload=args.metadata,
            timeout=args.timeout,
        )

        if not args.quiet:
            print(f"Signal: {args.signal}")
            print(f"Transaction: {args.transaction_id}")
            print(f"Status: {status_code}")
            if response_body:
                print(f"Response: {response_body}")

        # Return 0 for 2xx status codes, non-zero otherwise
        return 0 if 200 <= status_code < 300 else 1

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 3
    except json.JSONDecodeError as e:
        print(f"Invalid JSON metadata: {e}", file=sys.stderr)
        return 4
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
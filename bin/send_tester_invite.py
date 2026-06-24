#!/usr/bin/env python3
"""
bin/send_tester_invite.py – CLI to approve a tester and print an email-ready invite.

Usage:
    TESTER_ADMIN_TOKEN=secret python bin/send_tester_invite.py <tester_id> [--base-url http://localhost:8500]

This script:
  1. POSTs /api/v1/testers/{id}/approve to the running backend stub.
  2. Prints a ready-to-copy email body with the download link and tester_id.

No real SMTP is used – Howard copies the output and sends manually.
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Approve a tester and print an email-ready invite."
    )
    parser.add_argument("tester_id", help="The tester_id to approve")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BACKEND_STUB_URL", "http://localhost:8500"),
        help="Base URL of the backend stub (default: http://localhost:8500)",
    )
    args = parser.parse_args()

    admin_token = os.environ.get("TESTER_ADMIN_TOKEN")
    if not admin_token:
        print(
            "ERROR: TESTER_ADMIN_TOKEN environment variable is not set.",
            file=sys.stderr,
        )
        sys.exit(1)

    url = f"{args.base_url}/api/v1/testers/{args.tester_id}/approve"
    headers = {"Authorization": f"Bearer {admin_token}"}

    try:
        resp = httpx.post(url, headers=headers, timeout=10)
    except httpx.ConnectError:
        print(
            f"ERROR: Could not connect to {args.base_url}. Is the backend stub running?",
            file=sys.stderr,
        )
        sys.exit(1)

    if resp.status_code == 401:
        print("ERROR: Unauthorized – check your TESTER_ADMIN_TOKEN.", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 404:
        print(f"ERROR: Tester '{args.tester_id}' not found.", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 409:
        print(f"ERROR: {resp.json().get('detail', 'Already processed')}", file=sys.stderr)
        sys.exit(1)
    if resp.status_code != 200:
        print(f"ERROR: Unexpected status {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    download_url = data.get("download_url", "")
    tester_id = data.get("tester_id", args.tester_id)

    # Print email-ready text
    lines = [
        "=" * 60,
        "EMAIL-READY TEXT (copy everything between the lines)",
        "=" * 60,
        "Subject: You're in! Beta access for gamedata-pipeline",
        "",
        "Hi there,",
        "",
        "Great news - your beta application has been approved!",
        "",
        f"Download link: {download_url}",
        f"Your tester ID: {tester_id}",
        "",
        "Please keep this link private.  If you run into any issues,",
        "reach out on Discord.",
        "",
        "Cheers,",
        "The gamedata-pipeline team",
        "=" * 60,
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()

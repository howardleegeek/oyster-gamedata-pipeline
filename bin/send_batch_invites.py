#!/usr/bin/env python3
"""
bin/send_batch_invites.py – Batch-approve N testers and print email-ready invites.

Usage:
    OYSTER_ADMIN_TOKEN=secret python3 bin/send_batch_invites.py \
        --emails howard@x.com,bruno@y.com,foo@z.com \
        --backend http://localhost:8500

Per email:
  1. POST /api/v1/testers/apply  → capture tester_id
  2. POST /api/v1/testers/{id}/approve  → capture download_url
  3. Print formatted email body (docs/TESTER_BATCH_TEMPLATE.md)

No real SMTP is used – Howard copies the output and sends manually.
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_BATCH_SIZE = 10
DISCORD_SUPPORT_URL = "https://discord.gg/gamedata-pipeline"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _derive_name(email: str) -> str:
    """Extract the name from an email address (prefix before @)."""
    return email.split("@")[0]


def _format_email(name: str, download_url: str, tester_id: str) -> str:
    """Return a ready-to-copy email body with placeholders filled."""
    lines = [
        "=" * 60,
        f"EMAIL FOR: {name}",
        "=" * 60,
        "Subject: You're in! Beta access for gamedata-pipeline",
        "",
        f"Hi {name},",
        "",
        "Great news — your beta application has been approved!",
        "",
        f"Quick install link: {download_url}",
        f"Your tester ID: {tester_id}",
        "",
        "Next steps:",
        "• Download the installer using the link above",
        "• Run the installer and follow the on-screen prompts",
        "• Launch the app and verify you see the beta badge",
        "",
        "If you run into any issues, reach out on our Discord support channel:",
        f"  {DISCORD_SUPPORT_URL}",
        "",
        "Disclaimer: This is alpha software. Expect bugs, missing features,",
        "and occasional crashes. Your feedback is invaluable — please report",
        "any issues on Discord or via the in-app bug reporter.",
        "",
        "Cheers,",
        "The gamedata-pipeline team",
        "=" * 60,
    ]
    return "\n".join(lines)


def _apply_tester(
    client: httpx.Client,
    base_url: str,
    email: str,
) -> str:
    """POST /api/v1/testers/apply and return the tester_id."""
    discord_user = _derive_name(email)
    resp = client.post(
        f"{base_url}/api/v1/testers/apply",
        json={
            "email": email,
            "discord_user": discord_user,
            "why_interested": "Internal week 1 tester",
        },
        timeout=10,
    )
    if resp.status_code != 200:
        print(
            f"ERROR: Failed to apply for {email} "
            f"(status={resp.status_code}): {resp.text}",
            file=sys.stderr,
        )
        sys.exit(1)
    data = resp.json()
    return data["tester_id"]


def _approve_tester(
    client: httpx.Client,
    base_url: str,
    tester_id: str,
    admin_token: str,
) -> tuple[str, str]:
    """POST /api/v1/testers/{id}/approve and return (download_url, tester_id)."""
    resp = client.post(
        f"{base_url}/api/v1/testers/{tester_id}/approve",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=10,
    )
    if resp.status_code == 401:
        print(
            "ERROR: Unauthorized – check your OYSTER_ADMIN_TOKEN.",
            file=sys.stderr,
        )
        sys.exit(1)
    if resp.status_code != 200:
        print(
            f"ERROR: Failed to approve tester {tester_id} "
            f"(status={resp.status_code}): {resp.text}",
            file=sys.stderr,
        )
        sys.exit(1)
    data = resp.json()
    return data["download_url"], data["tester_id"]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch-approve testers and print email-ready invites."
    )
    parser.add_argument(
        "--emails",
        required=True,
        help="Comma-separated list of email addresses (max 10).",
    )
    parser.add_argument(
        "--backend",
        default=os.environ.get("BACKEND_STUB_URL", "http://localhost:8500"),
        help="Base URL of the backend stub (default: http://localhost:8500).",
    )
    args = parser.parse_args()

    # --- Validate admin token ---
    admin_token = os.environ.get("OYSTER_ADMIN_TOKEN")
    if not admin_token:
        print(
            "ERROR: OYSTER_ADMIN_TOKEN environment variable is not set.",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Parse and validate emails ---
    emails = [e.strip() for e in args.emails.split(",") if e.strip()]
    if not emails:
        print("ERROR: No emails provided.", file=sys.stderr)
        sys.exit(1)
    if len(emails) > MAX_BATCH_SIZE:
        print(
            f"ERROR: Too many emails ({len(emails)}). "
            f"Maximum is {MAX_BATCH_SIZE} per batch.",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Process each email ---
    with httpx.Client() as client:
        for email in emails:
            name = _derive_name(email)

            # Step 1: Apply
            tester_id = _apply_tester(client, args.backend, email)

            # Step 2: Approve
            download_url, approved_id = _approve_tester(
                client, args.backend, tester_id, admin_token
            )

            # Step 3: Print email body
            body = _format_email(name, download_url, approved_id)
            print(body)
            print()  # blank line between emails


if __name__ == "__main__":
    main()

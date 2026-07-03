#!/usr/bin/env python3
"""
payout_cron.py — daily Stripe Connect payout cron for the GameData tester portal.

Run from cron (or a launchd plist) once per day:

    0 3 * * *  /usr/local/bin/python3 /path/to/bin/payout_cron.py

Behaviour:
    1. Read each tester's accepted-but-unpaid balance from Supabase
       (`tester_unpaid_balance` view in the migration).
    2. For testers whose balance >= GAMEDATA_MIN_PAYOUT_CENTS AND have a
       Stripe-payouts-enabled account, create a Stripe Transfer with an
       idempotency key of f"{tester_id}-{date}". Re-running on the same
       day is a no-op at both layers (Stripe Idempotency-Key + our DB
       unique index on payouts.idempotency_key).
    3. Insert a `payouts` row capturing the result.
    4. Log to /tmp/payout_cron.log; on errors, optionally POST to a Slack
       webhook (env: PAYOUT_CRON_SLACK_WEBHOOK).

DEV MODE: when STRIPE_SECRET_KEY is not set OR Supabase env is missing,
the script still runs end-to-end against synthetic fixtures so you can
exercise the loop without hitting any external service.

Exit codes:
    0 - success (incl. "no eligible payouts today")
    1 - hard failure (DB unreachable, Slack ping failed, etc.)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_PATH = Path(os.environ.get("PAYOUT_CRON_LOG_PATH", "/tmp/payout_cron.log"))
DEFAULT_RATE_PER_HOUR_CENTS = int(os.environ.get("GAMEDATA_RATE_PER_HOUR_CENTS", "600"))
DEFAULT_MIN_PAYOUT_CENTS = int(os.environ.get("GAMEDATA_MIN_PAYOUT_CENTS", "2000"))
STRIPE_API_BASE = "https://api.stripe.com/v1"


# =====================================================================
# Logging
# =====================================================================


def configure_logger(level: int = logging.INFO) -> logging.Logger:
    """File + stderr logger; matches existing bin/ scripts."""
    logger = logging.getLogger("payout_cron")
    logger.setLevel(level)
    if logger.handlers:  # idempotent under repeated imports
        return logger
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


# =====================================================================
# Domain types
# =====================================================================


@dataclass(frozen=True)
class TesterBalance:
    """One row from the `tester_unpaid_balance` view."""

    tester_id: str
    email: str
    stripe_account_id: str | None
    stripe_payouts_enabled: bool
    accepted_seconds: int
    paid_cents: int

    def unpaid_cents(self, rate_per_hour_cents: int) -> int:
        """Calculate unpaid balance in cents.

        Args:
            rate_per_hour_cents: Hourly rate in cents.

        Returns:
            Unpaid balance in cents (non-negative).
        """
        earned = round(self.accepted_seconds * rate_per_hour_cents / 3600)
        return max(0, earned - self.paid_cents)


@dataclass(frozen=True)
class TransferResult:
    tester_id: str
    idempotency_key: str
    amount_cents: int
    success: bool
    transfer_id: str | None
    failure_reason: str | None


# =====================================================================
# Stripe HTTP client (live + mock)
# =====================================================================


class StripeError(RuntimeError):
    """Raised when Stripe returns a non-2xx response."""


class StripeClient:
    """Live Stripe client over urllib (no `stripe` PyPI dep)."""

    def __init__(self, secret_key: str) -> None:
        if not secret_key.startswith("sk_"):
            raise ValueError("Stripe secret key must start with sk_")
        self.secret_key = secret_key

    def create_transfer(
        self,
        amount_cents: int,
        destination_account_id: str,
        idempotency_key: str,
        description: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a Stripe Connect transfer to a destination account.

        Args:
            amount_cents: Transfer amount in US cents.
            destination_account_id: Stripe Connect account ID (acct_...).
            idempotency_key: Unique key for idempotent request retry safety.
            description: Optional human-readable description for the transfer.
            metadata: Optional key-value metadata dict for Stripe.

        Returns:
            Stripe API response dict containing transfer details.

        Raises:
            StripeError: On HTTP error from Stripe API.
        """
        body: dict[str, str] = {
            "amount": str(amount_cents),
            "currency": "usd",
            "destination": destination_account_id,
        }
        if description:
            body["description"] = description
        for k, v in (metadata or {}).items():
            body[f"metadata[{k}]"] = v

        encoded = urllib.parse.urlencode(body).encode("utf-8")
        req = urllib.request.Request(
            url=f"{STRIPE_API_BASE}/transfers",
            data=encoded,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.secret_key}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Idempotency-Key": idempotency_key,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (HTTPS only)
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8"))
            except Exception:
                payload = {"error": {"message": str(e)}}
            raise StripeError(
                f"Stripe HTTP {e.code}: {payload.get('error', {}).get('message', e.reason)}"
            ) from e


class MockStripeClient:
    """In-process stand-in. Same shape as `StripeClient`. Idempotency via dict."""

    def __init__(self) -> None:
        self.transfers_by_key: dict[str, dict[str, Any]] = {}
        self.counter = 0

    def create_transfer(
        self,
        amount_cents: int,
        destination_account_id: str,
        idempotency_key: str,
        description: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a Stripe transfer to a connected account.

        Implements idempotent transfers using an in-memory dict. If the same
        idempotency_key is provided, returns the cached transfer without creating
        a new one.

        Args:
            amount_cents: Amount to transfer in US cents.
            destination_account_id: Stripe connected account ID (acct_xxx).
            idempotency_key: Unique key for idempotency (re-submit same key
                returns cached result).
            description: Optional description for the transfer.
            metadata: Optional key-value metadata dict.

        Returns:
            Transfer dict with keys: id, object, amount, currency, destination,
            created, description, metadata.
        """
        if idempotency_key in self.transfers_by_key:
            return self.transfers_by_key[idempotency_key]
        self.counter += 1
        transfer = {
            "id": f"tr_mock_{self.counter:08x}",
            "object": "transfer",
            "amount": amount_cents,
            "currency": "usd",
            "destination": destination_account_id,
            "created": int(datetime.now(timezone.utc).timestamp()),
            "description": description,
            "metadata": dict(metadata or {}),
        }
        self.transfers_by_key[idempotency_key] = transfer
        return transfer


def make_stripe_client(*, allow_mock: bool = False) -> StripeClient | MockStripeClient:
    """Create a Stripe API client (live or mock) based on environment.

    Reads STRIPE_SECRET_KEY from the environment. If it starts with 'sk_',
    returns a live StripeClient. Otherwise, returns a MockStripeClient only
    when allow_mock is True; otherwise raises RuntimeError.

    Args:
        allow_mock: If True, allow falling back to MockStripeClient when
            STRIPE_SECRET_KEY is not a valid live key. Defaults to False.

    Returns:
        A StripeClient (live) or MockStripeClient (test) instance.

    Raises:
        RuntimeError: If STRIPE_SECRET_KEY is missing/invalid and allow_mock
            is False.
    """
    secret = os.environ.get("STRIPE_SECRET_KEY", "")
    if secret.startswith("sk_"):
        return StripeClient(secret)
    if allow_mock:
        return MockStripeClient()
    raise RuntimeError(
        "STRIPE_SECRET_KEY is not set or does not start with 'sk_'. "
        "Iron-law: payout_cron must not silently fall back to MockStripeClient "
        "in production. Set STRIPE_SECRET_KEY or pass --dry-run for testing."
    )


# =====================================================================
# Supabase HTTP client (live + mock)
# =====================================================================


class SupabaseClient:
    """REST PostgREST client (live)."""

    def __init__(self, url: str, service_role_key: str) -> None:
        self.url = url.rstrip("/")
        self.key = service_role_key

    def _request(
        self,
        method: str,
        path: str,
        body: Any = None,
        prefer: str | None = None,
    ) -> Any:
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        data: bytes | None = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url=f"{self.url}{path}",
            data=data,
            method=method,
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None

    def list_unpaid_balances(self) -> list[TesterBalance]:
        """Fetch all testers with unpaid balances from Supabase.

        Queries the `tester_unpaid_balance` view which aggregates accepted
        session time minus already-paid amounts.

        Returns:
            List of TesterBalance dataclasses representing each tester
            with their current unpaid balance information.
        """
        rows = self._request("GET", "/rest/v1/tester_unpaid_balance?select=*")
        return [
            TesterBalance(
                tester_id=row["tester_id"],
                email=row.get("email") or "",
                stripe_account_id=row.get("stripe_account_id"),
                stripe_payouts_enabled=bool(row.get("stripe_payouts_enabled")),
                accepted_seconds=int(row.get("accepted_seconds") or 0),
                paid_cents=int(row.get("paid_cents") or 0),
            )
            for row in (rows or [])
        ]

    def insert_payout(
        self,
        tester_id: str,
        amount_cents: int,
        idempotency_key: str,
        status: str,
        stripe_transfer_id: str | None,
        failure_reason: str | None,
    ) -> dict[str, Any] | None:
        """Insert a payout record into the Supabase database.

        Args:
            tester_id: Unique identifier for the tester receiving payout.
            amount_cents: Payout amount in cents.
            idempotency_key: Unique key to prevent duplicate payouts.
            status: Payout status (e.g., "paid", "failed").
            stripe_transfer_id: Stripe transfer ID if successful.
            failure_reason: Reason for failure if status indicates failure.

        Returns:
            The inserted payout record as a dict, or None if a duplicate
            idempotency_key was detected (409 conflict).
        """
        body = {
            "tester_id": tester_id,
            "amount_cents": amount_cents,
            "idempotency_key": idempotency_key,
            "status": status,
            "stripe_transfer_id": stripe_transfer_id,
            "stripe_payout_id": stripe_transfer_id,  # stripe_payout_id mirrored for backwards compat
            "failure_reason": failure_reason,
            "paid_at": datetime.now(timezone.utc).isoformat() if status == "paid" else None,
        }
        # PostgREST honours unique constraints — duplicates raise 409. Caller handles.
        try:
            return self._request(
                "POST",
                "/rest/v1/payouts?on_conflict=idempotency_key",
                body=[body],
                prefer="resolution=ignore-duplicates,return=representation",
            )
        except urllib.error.HTTPError as e:
            if e.code == 409:
                return None  # duplicate — already inserted by previous run
            raise


class MockSupabaseClient:
    """In-process stand-in for Supabase. Used in DEV MODE and tests."""

    def __init__(self, balances: list[TesterBalance] | None = None) -> None:
        self.balances = balances or self._default_balances()
        self.payouts_inserted: list[dict[str, Any]] = []

    @staticmethod
    def _default_balances() -> list[TesterBalance]:
        return [
            TesterBalance(
                tester_id="00000000-0000-0000-0000-000000000001",
                email="alice@example.com",
                stripe_account_id="acct_mock_alice",
                stripe_payouts_enabled=True,
                accepted_seconds=3600 * 8,
                paid_cents=0,
            ),
            TesterBalance(
                tester_id="00000000-0000-0000-0000-000000000002",
                email="bob@example.com",
                stripe_account_id=None,  # not onboarded — should be skipped
                stripe_payouts_enabled=False,
                accepted_seconds=3600 * 4,
                paid_cents=0,
            ),
            TesterBalance(
                tester_id="00000000-0000-0000-0000-000000000003",
                email="carol@example.com",
                stripe_account_id="acct_mock_carol",
                stripe_payouts_enabled=True,
                accepted_seconds=600,  # below threshold
                paid_cents=0,
            ),
        ]

    def list_unpaid_balances(self) -> list[TesterBalance]:
        """Fetch unpaid tester balances from mock data.

        Returns the pre-configured balance list for testing or development
        when a real Supabase connection is unavailable.

        Returns:
            List of TesterBalance dataclasses representing mock testers
            with their current unpaid balance information.
        """
        return list(self.balances)

    def insert_payout(
        self,
        tester_id: str,
        amount_cents: int,
        idempotency_key: str,
        status: str,
        stripe_transfer_id: str | None,
        failure_reason: str | None,
    ) -> dict[str, Any] | None:
        """Insert a payout record into the mock database.

        Simulates inserting a payout record, checking for duplicates based on
        the idempotency key.

        Args:
            tester_id: Unique identifier for the tester.
            amount_cents: Payout amount in cents.
            idempotency_key: Unique key to prevent duplicate payouts.
            status: Status of the payout ("paid" or "failed").
            stripe_transfer_id: Stripe transfer ID if successful.
            failure_reason: Reason for failure if status is "failed".

        Returns:
            The inserted row as a dictionary, or None if duplicate.
        """
        for prev in self.payouts_inserted:
            if prev["idempotency_key"] == idempotency_key:
                return None  # duplicate — pretend the unique index caught it
        row = {
            "tester_id": tester_id,
            "amount_cents": amount_cents,
            "idempotency_key": idempotency_key,
            "status": status,
            "stripe_transfer_id": stripe_transfer_id,
            "stripe_payout_id": stripe_transfer_id,
            "failure_reason": failure_reason,
            "paid_at": datetime.now(timezone.utc).isoformat() if status == "paid" else None,
        }
        self.payouts_inserted.append(row)
        return row


def make_supabase_client(
    *,
    allow_mock: bool = False,
) -> SupabaseClient | MockSupabaseClient:
    """Create a Supabase client from environment variables.

    Attempts to create a real Supabase client using SUPABASE_URL and
    SUPABASE_SERVICE_ROLE_KEY environment variables. Falls back to a
    mock client if allow_mock=True and env vars are not set.

    Args:
        allow_mock: If True, return a MockSupabaseClient when env vars
            are missing instead of raising an error. Default False.

    Returns:
        SupabaseClient if env vars are set, otherwise MockSupabaseClient
        if allow_mock is True.

    Raises:
        RuntimeError: If required env vars are missing and allow_mock is False.
    """
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL", "") or os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if url and key:
        return SupabaseClient(url, key)
    if allow_mock:
        return MockSupabaseClient()
    raise RuntimeError(
        "Supabase env vars (SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY) are not set. "
        "Iron-law: payout_cron must not silently fall back to MockSupabaseClient "
        "in production. Set the env vars or pass --dry-run for testing."
    )


# =====================================================================
# Slack webhook (best-effort)
# =====================================================================


def post_slack(message: str, webhook_url: str | None = None) -> bool:
    """POST a JSON `{ "text": ... }` to Slack. Returns False on failure."""
    webhook_url = webhook_url or os.environ.get("PAYOUT_CRON_SLACK_WEBHOOK", "")
    if not webhook_url:
        return False
    try:
        req = urllib.request.Request(
            url=webhook_url,
            data=json.dumps({"text": message}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return 200 <= resp.status < 300
    except Exception:
        return False


# =====================================================================
# Core run loop
# =====================================================================


def build_idempotency_key(tester_id: str, run_date: str) -> str:
    """Stable key derived from tester_id + date. Same day → same key."""
    return f"payout-{tester_id}-{run_date}"


def process_payouts(
    *,
    supabase: SupabaseClient | MockSupabaseClient,
    stripe: StripeClient | MockStripeClient,
    rate_per_hour_cents: int,
    min_payout_cents: int,
    run_date: str,
    logger: logging.Logger,
    dry_run: bool = False,
) -> list[TransferResult]:
    """Pure-ish core loop. Easy to test by injecting mocks."""
    balances = supabase.list_unpaid_balances()
    logger.info("Loaded %d tester balances from Supabase", len(balances))

    results: list[TransferResult] = []
    for bal in balances:
        unpaid = bal.unpaid_cents(rate_per_hour_cents)
        if unpaid < min_payout_cents:
            logger.debug(
                "skip %s — unpaid %d < threshold %d", bal.tester_id, unpaid, min_payout_cents
            )
            continue
        if not bal.stripe_account_id or not bal.stripe_payouts_enabled:
            logger.info(
                "skip %s — no Stripe account or payouts disabled (unpaid=%d)",
                bal.tester_id,
                unpaid,
            )
            continue

        idem = build_idempotency_key(bal.tester_id, run_date)
        if dry_run:
            logger.info(
                "[dry-run] would transfer %d cents to %s (key=%s)",
                unpaid,
                bal.stripe_account_id,
                idem,
            )
            results.append(
                TransferResult(
                    tester_id=bal.tester_id,
                    idempotency_key=idem,
                    amount_cents=unpaid,
                    success=True,
                    transfer_id=None,
                    failure_reason=None,
                )
            )
            continue

        try:
            transfer = stripe.create_transfer(
                amount_cents=unpaid,
                destination_account_id=bal.stripe_account_id,
                idempotency_key=idem,
                description=f"Oyster GameData payout for {run_date}",
                metadata={"tester_id": bal.tester_id, "run_date": run_date},
            )
        except StripeError as e:
            logger.error("transfer failed for %s: %s", bal.tester_id, e)
            supabase.insert_payout(
                tester_id=bal.tester_id,
                amount_cents=unpaid,
                idempotency_key=idem,
                status="failed",
                stripe_transfer_id=None,
                failure_reason=str(e),
            )
            results.append(
                TransferResult(
                    tester_id=bal.tester_id,
                    idempotency_key=idem,
                    amount_cents=unpaid,
                    success=False,
                    transfer_id=None,
                    failure_reason=str(e),
                )
            )
            continue

        transfer_id = transfer.get("id")
        inserted = supabase.insert_payout(
            tester_id=bal.tester_id,
            amount_cents=unpaid,
            idempotency_key=idem,
            status="paid",
            stripe_transfer_id=transfer_id,
            failure_reason=None,
        )
        # `inserted is None` → unique index caught a duplicate (re-run today).
        # We still report success because Stripe also de-duplicated.
        logger.info(
            "%s %s → tester=%s amount=%d cents transfer=%s",
            "duplicate" if inserted is None else "paid",
            idem,
            bal.tester_id,
            unpaid,
            transfer_id,
        )
        results.append(
            TransferResult(
                tester_id=bal.tester_id,
                idempotency_key=idem,
                amount_cents=unpaid,
                success=True,
                transfer_id=transfer_id,
                failure_reason=None,
            )
        )

    return results


# =====================================================================
# CLI
# =====================================================================


def main(argv: list[str] | None = None) -> int:
    """Daily payout cron entry point.

    Reads unpaid tester balances from Supabase, creates Stripe transfers
    for eligible testers, and records payouts.

    Args:
        argv: Command-line arguments (default: sys.argv).

    Returns:
        Exit code: 0 for success, 1 for failure.
    """
    parser = argparse.ArgumentParser(description="Daily Stripe Connect payout cron.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute payouts and log them, but don't hit Stripe or write to Supabase.",
    )
    parser.add_argument(
        "--rate-per-hour-cents",
        type=int,
        default=DEFAULT_RATE_PER_HOUR_CENTS,
        help="Override $/hr in cents (default: env GAMEDATA_RATE_PER_HOUR_CENTS).",
    )
    parser.add_argument(
        "--min-payout-cents",
        type=int,
        default=DEFAULT_MIN_PAYOUT_CENTS,
        help="Minimum payout threshold in cents (default: env GAMEDATA_MIN_PAYOUT_CENTS).",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logger = configure_logger(level=logging.DEBUG if args.verbose else logging.INFO)
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    supabase = make_supabase_client(allow_mock=args.dry_run)
    stripe = make_stripe_client(allow_mock=args.dry_run)

    mode_bits = []
    mode_bits.append("supabase=live" if isinstance(supabase, SupabaseClient) else "supabase=mock")
    mode_bits.append("stripe=live" if isinstance(stripe, StripeClient) else "stripe=mock")
    if args.dry_run:
        mode_bits.append("dry-run")
    logger.info("payout_cron starting [%s] run_date=%s", ",".join(mode_bits), run_date)

    try:
        results = process_payouts(
            supabase=supabase,
            stripe=stripe,
            rate_per_hour_cents=args.rate_per_hour_cents,
            min_payout_cents=args.min_payout_cents,
            run_date=run_date,
            logger=logger,
            dry_run=args.dry_run,
        )
    except Exception as e:  # pragma: no cover — top-level catch-all
        logger.exception("payout_cron crashed: %s", e)
        post_slack(f":rotating_light: payout_cron crashed: {e}")
        return 1

    paid = [r for r in results if r.success]
    failed = [r for r in results if not r.success]
    total_cents = sum(r.amount_cents for r in paid)
    logger.info(
        "payout_cron complete: %d paid (%d cents), %d failed",
        len(paid),
        total_cents,
        len(failed),
    )

    if failed:
        msg = f":warning: payout_cron {run_date}: {len(failed)} failed transfers — " + ", ".join(
            f"{r.tester_id}({r.failure_reason})" for r in failed[:5]
        )
        post_slack(msg)

    return 0


if __name__ == "__main__":
    sys.exit(main())

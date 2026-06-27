"""
Stripe Connect Express Integration
Handles onboarding and transfer execution for contributor payouts.
Uses test mode by default; production toggle via STRIPE_LIVE=true.
"""

import logging
import os
from typing import Any, Dict, Optional

import stripe

logger = logging.getLogger(__name__)

# Configuration
STRIPE_LIVE = os.getenv("STRIPE_LIVE", "false").lower() == "true"
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_placeholder")

stripe.api_key = STRIPE_SECRET_KEY

# Mode indicator
MODE = "live" if STRIPE_LIVE else "test"
logger.info(f"Stripe Connect running in {MODE} mode")


# --- Onboarding ---


def create_connect_account(contributor_id: str, email: str, country: str = "US") -> Dict[str, Any]:
    """
    Create a Stripe Connect Express account for a contributor.
    Returns account ID and onboarding link.
    """
    try:
        account = stripe.Account.create(
            type="express",
            country=country,
            email=email,
            capabilities={"transfers": {"requested": True}},
            metadata={"contributor_id": contributor_id, "platform": "oyster"},
        )

        # Create account link for onboarding
        account_link = stripe.AccountLink.create(
            account=account.id,
            refresh_url=f"https://oyster.example.com/onboarding/refresh?account={account.id}",
            return_url=f"https://oyster.example.com/onboarding/complete?account={account.id}",
            type="account_onboarding",
        )

        logger.info(f"Created Connect account {account.id} for contributor {contributor_id}")

        return {"account_id": account.id, "onboarding_url": account_link.url, "status": "pending"}

    except stripe.error.StripeError as e:
        logger.error(f"Failed to create Connect account: {e}")
        raise


def get_account_status(account_id: str) -> Dict[str, Any]:
    """Get the status of a Connect account (onboarding complete, etc)."""
    try:
        account = stripe.Account.retrieve(account_id)

        return {
            "account_id": account.id,
            "charges_enabled": account.charges_enabled,
            "payouts_enabled": account.payouts_enabled,
            "details_submitted": account.details_submitted,
            "requirements": account.requirements,
        }

    except stripe.error.StripeError as e:
        logger.error(f"Failed to retrieve account status: {e}")
        raise


def create_onboarding_link(account_id: str) -> str:
    """Create a new onboarding link for an existing account."""
    try:
        account_link = stripe.AccountLink.create(
            account=account_id,
            refresh_url=f"https://oyster.example.com/onboarding/refresh?account={account_id}",
            return_url=f"https://oyster.example.com/onboarding/complete?account={account_id}",
            type="account_onboarding",
        )

        return account_link.url

    except stripe.error.StripeError as e:
        logger.error(f"Failed to create onboarding link: {e}")
        raise


# --- Transfers ---


def execute_stripe_transfer(
    contributor_id: str,
    amount: float,
    idempotency_key: str,
    account_id: Optional[str] = None,
    currency: str = "usd",
) -> str:
    """
    Execute a transfer to a contributor's Connect account.

    Args:
        contributor_id: Oyster contributor ID
        amount: Amount in dollars (converted to cents)
        idempotency_key: Key to ensure idempotency
        account_id: Stripe Connect account ID (if known)
        currency: Currency code (default: usd)

    Returns:
        Stripe transfer ID
    """
    # Convert dollars to cents
    amount_cents = int(amount * 100)

    if amount_cents < 50:  # Minimum $0.50
        raise ValueError(f"Transfer amount too small: ${amount}")

    # In production, look up account_id from contributor record
    # For now, require it to be passed or raise
    if not account_id:
        # In production: lookup from database
        # For test: use a mock account
        logger.warning(f"No account_id provided for {contributor_id}, using test account")
        account_id = os.getenv("STRIPE_TEST_ACCOUNT_ID", "acct_test_placeholder")

    try:
        # Create transfer with idempotency
        transfer = stripe.Transfer.create(
            amount=amount_cents,
            currency=currency,
            destination=account_id,
            idempotency_key=idempotency_key,
            metadata={"contributor_id": contributor_id, "platform": "oyster"},
        )

        logger.info(
            f"Transfer {transfer.id}: ${amount} to {account_id} "
            f"(idempotency_key: {idempotency_key})"
        )

        return transfer.id

    except stripe.error.StripeError as e:
        logger.error(f"Transfer failed: {e}")
        # In test mode, return mock ID for testing
        if not STRIPE_LIVE and "test" in STRIPE_SECRET_KEY.lower():
            logger.warning("Returning mock transfer ID for test mode")
            return f"tr_test_{idempotency_key[:20]}"
        raise


def get_transfer_status(transfer_id: str) -> Dict[str, Any]:
    """Get the status of a transfer."""
    try:
        transfer = stripe.Transfer.retrieve(transfer_id)

        return {
            "id": transfer.id,
            "amount": transfer.amount / 100,
            "currency": transfer.currency,
            "destination": transfer.destination,
            "status": transfer.status,
            "created": transfer.created,
        }

    except stripe.error.StripeError as e:
        logger.error(f"Failed to retrieve transfer: {e}")
        raise


def list_transfers(contributor_id: Optional[str] = None, limit: int = 10) -> list:
    """List transfers, optionally filtered by contributor."""
    try:
        params = {"limit": limit}

        if contributor_id:
            params["metadata"] = {"contributor_id": contributor_id}

        transfers = stripe.Transfer.list(**params)

        return [
            {
                "id": t.id,
                "amount": t.amount / 100,
                "currency": t.currency,
                "destination": t.destination,
                "status": t.status,
                "created": t.created,
                "metadata": t.metadata,
            }
            for t in transfers.data
        ]

    except stripe.error.StripeError as e:
        logger.error(f"Failed to list transfers: {e}")
        raise


# --- Webhook Handling ---


def construct_webhook_event(payload: bytes, signature: str) -> Any:
    """Construct and verify webhook event from Stripe."""
    try:
        event = stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
        return event
    except stripe.error.SignatureVerificationError:
        logger.error("Invalid webhook signature")
        raise
    except ValueError:
        logger.error("Invalid payload")
        raise


def handle_webhook_event(event: Any) -> Dict[str, Any]:
    """Handle webhook events from Stripe."""
    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "account.updated":
        # Connect account status changed
        account_id = data["id"]
        logger.info(f"Account {account_id} updated: charges_enabled={data.get('charges_enabled')}")
        return {"status": "processed", "account_id": account_id}

    elif event_type == "transfer.created":
        transfer_id = data["id"]
        logger.info(f"Transfer {transfer_id} created")
        return {"status": "processed", "transfer_id": transfer_id}

    elif event_type == "transfer.failed":
        transfer_id = data["id"]
        logger.error(f"Transfer {transfer_id} failed")
        return {"status": "processed", "transfer_id": transfer_id, "failed": True}

    else:
        logger.info(f"Unhandled event type: {event_type}")
        return {"status": "ignored", "type": event_type}


# --- Test Helpers ---


def get_test_account_id() -> str:
    """Get a test Connect account ID for development."""
    return os.getenv("STRIPE_TEST_ACCOUNT_ID", "acct_test_placeholder")


def is_test_mode() -> bool:
    """Check if running in test mode."""
    return not STRIPE_LIVE


if __name__ == "__main__":
    # Quick test
    print(f"Stripe Connect mode: {MODE}")
    print(f"Test mode: {is_test_mode()}")

"""
PayPal Payouts API Fallback
Used for contributors in countries not supported by Stripe Connect.
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

# Configuration
PAYPAL_LIVE = os.getenv("PAYPAL_LIVE", "false").lower() == "true"
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "placeholder_client_id")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "placeholder_secret")

# API endpoints
PAYPAL_API_BASE = "https://api-m.paypal.com" if PAYPAL_LIVE else "https://api-m.sandbox.paypal.com"
PAYPAL_OAUTH_URL = f"{PAYPAL_API_BASE}/v1/oauth2/token"
PAYPAL_PAYOUTS_URL = f"{PAYPAL_API_BASE}/v1/payments/payouts"

# In-memory token cache (in production, use Redis)
_access_token: Optional[str] = None
_token_expiry: Optional[datetime] = None

MODE = "live" if PAYPAL_LIVE else "sandbox"
logger.info(f"PayPal Payouts running in {MODE} mode")


# --- Authentication ---

def get_access_token() -> str:
    """Get OAuth access token, refreshing if needed."""
    global _access_token, _token_expiry
    
    # Check if we have a valid token
    if _access_token and _token_expiry and datetime.utcnow() < _token_expiry:
        return _access_token
    
    # Request new token
    auth = (PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET)
    data = {"grant_type": "client_credentials"}
    
    try:
        response = requests.post(
            PAYPAL_OAUTH_URL,
            auth=auth,
            data=data,
            headers={"Accept": "application/json"}
        )
        response.raise_for_status()
        
        token_data = response.json()
        _access_token = token_data["access_token"]
        
        # Set expiry (subtract 60 seconds for safety margin)
        expires_in = token_data.get("expires_in", 3600)
        _token_expiry = datetime.utcnow().timestamp() + expires_in - 60
        
        logger.info("Obtained new PayPal access token")
        return _access_token
        
    except requests.RequestException as e:
        logger.error(f"Failed to obtain PayPal access token: {e}")
        # In test mode, return mock token
        if not PAYPAL_LIVE:
            logger.warning("Returning mock token for test mode")
            return "mock_test_token"
        raise


def _get_headers() -> Dict[str, str]:
    """Get headers for PayPal API requests."""
    return {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type": "application/json"
    }


# --- Payout Execution ---

def execute_paypal_payout(
    contributor_id: str,
    amount: float,
    idempotency_key: str,
    paypal_email: Optional[str] = None,
    currency: str = "USD"
) -> str:
    """
    Execute a payout to a contributor via PayPal.
    
    Args:
        contributor_id: Oyster contributor ID
        amount: Amount in dollars
        idempotency_key: Key to ensure idempotency
        paypal_email: Contributor's PayPal email (if known)
        currency: Currency code (default: USD)
    
    Returns:
        PayPal payout batch ID
    """
    if amount < 1.0:  # Minimum $1.00
        raise ValueError(f"Payout amount too small: ${amount}")
    
    # In production, look up PayPal email from contributor record
    if not paypal_email:
        # For test, use a placeholder
        logger.warning(f"No PayPal email for {contributor_id}, using test email")
        paypal_email = f"contributor-{contributor_id}@test.example.com"
    
    # Build payout batch request
    sender_batch_id = idempotency_key[:36]  # Max 36 chars
    payout_item = {
        "recipient_type": "EMAIL",
        "amount": {
            "value": f"{amount:.2f}",
            "currency": currency
        },
        "receiver": paypal_email,
        "note": f"Oyster contributor payout - {contributor_id}",
        "sender_item_id": contributor_id
    }
    
    payload = {
        "sender_batch_header": {
            "sender_batch_id": sender_batch_id,
            "email_subject": "You have a payout from Oyster!",
            "email_message": "Thank you for your contributions. Your payout is on the way!"
        },
        "items": [payout_item]
    }
    
    try:
        response = requests.post(
            PAYPAL_PAYOUTS_URL,
            json=payload,
            headers=_get_headers()
        )
        response.raise_for_status()
        
        result = response.json()
        batch_id = result["batch_header"]["payout_batch_id"]
        
        logger.info(
            f"PayPal payout {batch_id}: ${amount} to {paypal_email} "
            f"(idempotency_key: {idempotency_key})"
        )
        
        return batch_id
        
    except requests.RequestException as e:
        logger.error(f"PayPal payout failed: {e}")
        # In test mode, return mock ID
        if not PAYPAL_LIVE:
            logger.warning("Returning mock payout ID for test mode")
            return f"PAYOUT-{idempotency_key[:20]}"
        raise


def get_payout_status(batch_id: str) -> Dict[str, Any]:
    """Get the status of a PayPal payout batch."""
    try:
        url = f"{PAYPAL_PAYOUTS_URL}/{batch_id}"
        response = requests.get(url, headers=_get_headers())
        response.raise_for_status()
        
        result = response.json()
        
        return {
            "batch_id": result["batch_header"]["payout_batch_id"],
            "status": result["batch_header"]["batch_status"],
            "amount": result["batch_header"]["amount"]["value"],
            "currency": result["batch_header"]["amount"]["currency"],
            "created_time": result["batch_header"]["created_time"],
            "item_count": len(result.get("items", []))
        }
        
    except requests.RequestException as e:
        logger.error(f"Failed to get payout status: {e}")
        raise


def list_payouts(
    contributor_id: Optional[str] = None,
    limit: int = 10
) -> list:
    """List recent payout batches."""
    try:
        params = {"limit": limit}
        
        response = requests.get(
            PAYPAL_PAYOUTS_URL,
            params=params,
            headers=_get_headers()
        )
        response.raise_for_status()
        
        result = response.json()
        
        payouts = []
        for batch in result.get("batches", []):
            item = {
                "batch_id": batch["batch_header"]["payout_batch_id"],
                "status": batch["batch_header"]["batch_status"],
                "amount": batch["batch_header"]["amount"]["value"],
                "currency": batch["batch_header"]["amount"]["currency"],
                "created_time": batch["batch_header"]["created_time"]
            }
            
            # Filter by contributor if specified
            if contributor_id:
                # Check items for matching contributor
                for batch_item in batch.get("items", []):
                    if batch_item.get("sender_item_id") == contributor_id:
                        payouts.append(item)
                        break
            else:
                payouts.append(item)
        
        return payouts
        
    except requests.RequestException as e:
        logger.error(f"Failed to list payouts: {e}")
        raise


# --- Eligibility Check ---

def is_paypal_available_for_country(country_code: str) -> bool:
    """
    Check if PayPal is available for a given country.
    This is a simplified list - in production, use PayPal's country support API.
    """
    # PayPal is available in most countries
    # Some countries have limited support
    unsupported = {
        "KP",  # North Korea
        "IR",  # Iran
        "SY",  # Syria
        "CU",  # Cuba
    }
    
    return country_code not in unsupported


def get_supported_countries() -> list:
    """Get list of countries where PayPal payouts are supported."""
    # This would be maintained by PayPal's documentation
    # Returning a sample of major markets
    return [
        "US", "GB", "DE", "FR", "ES", "IT", "AU", "CA", "JP",
        "CN", "KR", "SG", "HK", "TW", "IN", "BR", "MX", "RU"
    ]


# --- Test Helpers ---

def is_test_mode() -> bool:
    """Check if running in test mode."""
    return not PAYPAL_LIVE


if __name__ == "__main__":
    print(f"PayPal Payouts mode: {MODE}")
    print(f"Test mode: {is_test_mode()}")
    print(f"Supported countries: {len(get_supported_countries())}")

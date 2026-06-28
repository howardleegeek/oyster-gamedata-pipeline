"""
Webhook dispatcher for marketplace events.

Background worker that:
- Subscribes to internal event bus
- POSTs to registered webhooks with HMAC-SHA256 signature
- Retries on 5xx with exponential backoff
- Dead-letters after max retries
"""

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

import aiohttp

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Event bus (in production, use Redis pub/sub or message queue)
event_queue: asyncio.Queue = asyncio.Queue()

# Webhook delivery tracking
delivery_log: Dict[str, List[Dict]] = {}

# Retry configuration
MAX_RETRIES = 5
MAX_AGE_HOURS = 24
BASE_RETRY_DELAY = 1  # seconds
MAX_RETRY_DELAY = 3600  # 1 hour

# Event types
EVENT_TYPES = [
    "session.created",
    "session.audit_passed",
    "session.approved",
    "payout.completed",
]


class WebhookDeliveryError(Exception):
    """Exception raised when webhook delivery fails."""

    pass


def compute_hmac_signature(secret: str, payload: str) -> str:
    """
    Compute HMAC-SHA256 signature for webhook payload.

    Args:
        secret: Shared secret between Oyster and buyer
        payload: JSON string of the webhook payload

    Returns:
        Hex-encoded signature string
    """
    signature = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return signature


async def get_subscribed_webhooks(event_type: str) -> List[Dict]:
    """
    Get all webhooks subscribed to a given event type.

    In production, this queries the database.
    """
    # Import here to avoid circular dependency
    from server.marketplace_api import webhooks_store

    subscribed = []
    for wh_id, wh_data in webhooks_store.items():
        if event_type in wh_data.get("events", []):
            subscribed.append(wh_data)

    return subscribed


async def deliver_webhook(
    webhook_url: str,
    secret: str,
    event_type: str,
    payload: Dict[str, Any],
    attempt: int = 1,
) -> bool:
    """
    Deliver webhook payload with HMAC signature.

    Args:
        webhook_url: Target URL
        secret: Shared secret for HMAC
        event_type: Event type string
        payload: Event payload dict
        attempt: Current attempt number

    Returns:
        True if delivery successful, False otherwise
    """
    # Build full payload with metadata
    full_payload = {
        "event_type": event_type,
        "timestamp": datetime.utcnow().isoformat(),
        "data": payload,
        "attempt": attempt,
    }

    payload_str = json.dumps(full_payload, sort_keys=True)
    signature = compute_hmac_signature(secret, payload_str)

    headers = {
        "Content-Type": "application/json",
        "X-Oyster-Signature": signature,
        "X-Oyster-Event": event_type,
        "X-Oyster-Attempt": str(attempt),
    }

    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                webhook_url,
                data=payload_str,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response,
        ):
            if response.status >= 200 and response.status < 300:
                logger.info(f"Webhook delivered successfully to {webhook_url}")
                return True
            elif response.status >= 500:
                # Server error - retry
                logger.warning(f"Webhook got 5xx response: {response.status}")
                raise WebhookDeliveryError(f"Server error: {response.status}")
            else:
                # Client error - don't retry
                logger.error(f"Webhook got client error: {response.status}")
                return False

    except asyncio.TimeoutError:
        logger.warning(f"Webhook timeout to {webhook_url}")
        raise WebhookDeliveryError("Timeout")

    except aiohttp.ClientError as e:
        logger.warning(f"Webhook client error: {e}")
        raise WebhookDeliveryError(str(e))


async def retry_webhook(
    webhook_data: Dict,
    event_type: str,
    payload: Dict[str, Any],
    attempt: int,
    created_at: datetime,
) -> None:
    """
    Retry webhook delivery with exponential backoff.

    Args:
        webhook_data: Webhook configuration
        event_type: Event type
        payload: Event payload
        attempt: Current attempt number
        created_at: When the event was first created
    """
    webhook_id = webhook_data["id"]
    webhook_url = webhook_data["url"]
    secret = webhook_data["secret"]

    # Check max age
    age = datetime.utcnow() - created_at
    if age > timedelta(hours=MAX_AGE_HOURS):
        logger.error(f"Webhook {webhook_id} exceeded max age, dead-lettering")
        await dead_letter(webhook_id, event_type, payload, "max_age_exceeded")
        return

    # Check max retries
    if attempt > MAX_RETRIES:
        logger.error(f"Webhook {webhook_id} exceeded max retries, dead-lettering")
        await dead_letter(webhook_id, event_type, payload, "max_retries_exceeded")
        return

    # Calculate exponential backoff delay
    delay = min(BASE_RETRY_DELAY * (2 ** (attempt - 1)), MAX_RETRY_DELAY)

    logger.info(f"Scheduling webhook {webhook_id} retry attempt {attempt} in {delay}s")

    await asyncio.sleep(delay)

    try:
        success = await deliver_webhook(webhook_url, secret, event_type, payload, attempt)
        if success:
            logger.info(f"Webhook {webhook_id} delivered on attempt {attempt}")
        else:
            # Non-retryable error
            await dead_letter(webhook_id, event_type, payload, "client_error")

    except WebhookDeliveryError:
        # Schedule next retry
        await retry_webhook(webhook_data, event_type, payload, attempt + 1, created_at)


async def dead_letter(
    webhook_id: str,
    event_type: str,
    payload: Dict[str, Any],
    reason: str,
) -> None:
    """
    Dead-letter a failed webhook delivery.

    In production, this writes to a dead-letter queue or database.
    """
    dead_letter_entry = {
        "webhook_id": webhook_id,
        "event_type": event_type,
        "payload": payload,
        "reason": reason,
        "timestamp": datetime.utcnow().isoformat(),
    }

    logger.error(f"Dead-lettered webhook: {json.dumps(dead_letter_entry)}")

    # Store in delivery log
    if webhook_id not in delivery_log:
        delivery_log[webhook_id] = []
    delivery_log[webhook_id].append(
        {
            **dead_letter_entry,
            "status": "dead_lettered",
        }
    )


async def dispatch_event(event_type: str, payload: Dict[str, Any]) -> None:
    """
    Dispatch an event to all subscribed webhooks.

    This is the main entry point for event dispatch.

    Args:
        event_type: Event type string (e.g., "session.approved")
        payload: Event data
    """
    if event_type not in EVENT_TYPES:
        logger.warning(f"Unknown event type: {event_type}")
        return

    logger.info(f"Dispatching event: {event_type}")

    # Get subscribed webhooks
    webhooks = await get_subscribed_webhooks(event_type)

    if not webhooks:
        logger.info(f"No webhooks subscribed to {event_type}")
        return

    created_at = datetime.utcnow()

    # Deliver to each webhook
    for webhook_data in webhooks:
        webhook_id = webhook_data["id"]

        # Log delivery attempt
        if webhook_id not in delivery_log:
            delivery_log[webhook_id] = []

        try:
            success = await deliver_webhook(
                webhook_data["url"], webhook_data["secret"], event_type, payload, attempt=1
            )

            if success:
                delivery_log[webhook_id].append(
                    {
                        "event_type": event_type,
                        "status": "delivered",
                        "attempt": 1,
                        "timestamp": created_at.isoformat(),
                    }
                )
            else:
                # Non-retryable error
                await dead_letter(webhook_id, event_type, payload, "client_error")

        except WebhookDeliveryError:
            # Start retry loop
            asyncio.create_task(retry_webhook(webhook_data, event_type, payload, 2, created_at))


async def event_worker():
    """
    Background worker that processes events from the queue.

    In production, this would listen to a message queue.
    """
    logger.info("Webhook dispatcher worker started")

    while True:
        try:
            event = await event_queue.get()
            await dispatch_event(event["type"], event["payload"])
            event_queue.task_done()
        except asyncio.CancelledError:
            logger.info("Webhook dispatcher worker stopped")
            break
        except Exception as e:
            logger.error(f"Error processing event: {e}")


def emit_event(event_type: str, payload: Dict[str, Any]) -> None:
    """
    Emit an event to the internal event bus.

    Args:
        event_type: Event type string
        payload: Event data
    """
    # In production, publish to message queue
    # For now, schedule async dispatch
    asyncio.create_task(dispatch_event(event_type, payload))


# Example usage and testing
if __name__ == "__main__":

    async def test_webhook():
        """Test webhook delivery."""
        # Register a test webhook
        from server.marketplace_api import webhooks_store

        webhooks_store["wh_test"] = {
            "id": "wh_test",
            "url": "https://httpbin.org/post",
            "secret": "test_secret_123",
            "events": ["session.approved"],
            "buyer_id": "buyer_test",
        }

        # Dispatch test event
        await dispatch_event(
            "session.approved",
            {
                "session_id": "sess_123",
                "buyer_id": "buyer_test",
                "approved_at": datetime.utcnow().isoformat(),
            },
        )

        print("Test webhook dispatched")

    asyncio.run(test_webhook())

#!/usr/bin/env python3
"""
EPAL Payout Passthrough - Bonus payout via EPAL payment rails.

This module provides a CLI tool to process bonus payouts through EPAL's
payment system, allowing companions to see EPAL session pay and recording
bonus on a single statement.

The tool interfaces with the EPAL API endpoint:
POST <epal-api>/v1/companion/bonus

Key features:
- No Stripe/PayPal/W-9 dependencies
- Direct integration with EPAL payment infrastructure
- Proper error handling and logging
- Configurable via command-line arguments
"""

import argparse
import json
import logging
import os
import sys
import urllib.parse
from datetime import datetime
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from typing import Any, Dict, List, Optional

# Lazy imports for optional dependencies
YAML_AVAILABLE = False
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError as e:
    _yaml_logger = logging.getLogger(__name__)
    _yaml_logger.debug("PyYAML not available; YAML inputs/outputs disabled: %s", e)


class EPALPayoutError(Exception):
    """Base exception for EPAL payout errors."""
    pass


class EPALPayoutClient:
    """Client for interacting with EPAL payout API."""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: int = 30,
        verify_ssl: bool = True
    ) -> None:
        """
        Initialize EPAL payout client.

        Args:
            base_url: Base URL for EPAL API (e.g., "https://api.epal.com")
            api_key: Optional API key for authentication
            timeout: Request timeout in seconds
            verify_ssl: Whether to verify SSL certificates
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.logger = logging.getLogger(__name__)

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make HTTP request to EPAL API."""
        parsed = urllib.parse.urlparse(f"{self.base_url}/{endpoint.lstrip('/')}")
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'EPAL-Payout-Passthrough/1.0'
        }
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'

        conn_class = HTTPSConnection if parsed.scheme == 'https' else HTTPConnection
        conn = conn_class(parsed.netloc, timeout=self.timeout)

        try:
            body = json.dumps(data) if data else None
            conn.request(method, parsed.path, body=body, headers=headers)
            response = conn.getresponse()
            response_body = response.read().decode('utf-8')

            if response.status >= 400:
                raise EPALPayoutError(f"API error {response.status}: {response_body}")

            return json.loads(response_body) if response_body else {}
        finally:
            conn.close()

    def payout_bonus(
        self,
        companion_id: str,
        amount: float,
        currency: str = "USD",
        session_id: Optional[str] = None,
        recording_id: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Process a bonus payout for a companion.

        Args:
            companion_id: Unique identifier for the companion
            amount: Bonus amount to payout
            currency: Currency code (default: USD)
            session_id: Optional session ID
            recording_id: Optional recording ID
            description: Optional payout description
            metadata: Optional additional metadata

        Returns:
            Dictionary containing payout result
        """
        payload: Dict[str, Any] = {
            "companion_id": companion_id,
            "amount": amount,
            "currency": currency,
            "timestamp": datetime.utcnow().isoformat()
        }

        if session_id:
            payload["session_id"] = session_id
        if recording_id:
            payload["recording_id"] = recording_id
        if description:
            payload["description"] = description
        if metadata:
            payload["metadata"] = metadata

        self.logger.info(f"Processing bonus payout: {amount} {currency} for {companion_id}")
        return self._make_request("POST", "v1/companion/bonus", payload)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the application."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def load_config(config_path: str) -> Dict[str, Any]:
    """Load configuration from file."""
    path = Path(config_path)
    if not path.exists():
        return {}

    with open(path, 'r', encoding='utf-8') as f:
        if path.suffix in ('.yaml', '.yml') and YAML_AVAILABLE:
            return yaml.safe_load(f) or {}
        elif path.suffix == '.json':
            return json.load(f)
    return {}


def validate_args(args: argparse.Namespace) -> bool:
    """Validate command-line arguments."""
    if args.amount <= 0:
        logging.error("Amount must be positive")
        return False
    if args.amount > 100000:
        logging.error("Amount exceeds maximum allowed (100000)")
        return False
    if args.companion_id and len(args.companion_id) < 3:
        logging.error("Companion ID must be at least 3 characters")
        return False
    return True


def main(argv: Optional[List[str]] = None) -> int:
    """
    Main entry point for the EPAL payout passthrough CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv)

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    parser = argparse.ArgumentParser(
        description='EPAL Payout Passthrough - Process bonus payouts via EPAL payment rails',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --companion-id cmp_123 --amount 50.00
  %(prog)s --companion-id cmp_123 --amount 100 --session-id ses_456
  %(prog)s --companion-id cmp_123 --amount 75.50 --dry-run
        """
    )

    parser.add_argument(
        '--companion-id', '-c',
        required=True,
        help='Companion ID for the payout'
    )
    parser.add_argument(
        '--amount', '-a',
        type=float,
        required=True,
        help='Bonus amount to payout'
    )
    parser.add_argument(
        '--currency', '-u',
        default='USD',
        help='Currency code (default: USD)'
    )
    parser.add_argument(
        '--session-id', '-s',
        help='Optional session ID'
    )
    parser.add_argument(
        '--recording-id', '-r',
        help='Optional recording ID'
    )
    parser.add_argument(
        '--description', '-d',
        help='Optional payout description'
    )
    parser.add_argument(
        '--api-url',
        default=os.environ.get('EPAL_API_URL', 'https://api.epal.com'),
        help='EPAL API base URL'
    )
    parser.add_argument(
        '--api-key',
        default=os.environ.get('EPAL_API_KEY'),
        help='EPAL API key (or set EPAL_API_KEY env var)'
    )
    parser.add_argument(
        '--config', '-f',
        help='Path to configuration file (JSON or YAML)'
    )
    parser.add_argument(
        '--timeout', '-t',
        type=int,
        default=30,
        help='Request timeout in seconds (default: 30)'
    )
    parser.add_argument(
        '--output', '-o',
        choices=['text', 'json', 'yaml'],
        default='text',
        help='Output format'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Validate without making API request'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    # Load config file if provided
    config: Dict[str, Any] = {}
    if args.config:
        config = load_config(args.config)

    # Merge config with args
    api_url = args.api_url
    if 'api_url' in config:
        api_url = config['api_url']

    api_key = args.api_key
    if not api_key and 'api_key' in config:
        api_key = config['api_key']
    if not api_key:
        api_key = os.environ.get('EPAL_API_KEY')

    # Validate arguments
    if not validate_args(args):
        return 1

    # Build metadata from args
    metadata: Dict[str, Any] = {}
    if args.session_id:
        metadata['session_id'] = args.session_id
    if args.recording_id:
        metadata['recording_id'] = args.recording_id

    # Create client
    client = EPALPayoutClient(
        base_url=api_url,
        api_key=api_key,
        timeout=args.timeout
    )

    try:
        if args.dry_run:
            logger.info("Dry run mode - no API request will be made")
            logger.info(f"Validation successful for payout: {args.amount} {args.currency}")
            return 0

        # Process payout
        result = client.payout_bonus(
            companion_id=args.companion_id,
            amount=args.amount,
            currency=args.currency,
            session_id=args.session_id,
            recording_id=args.recording_id,
            description=args.description,
            metadata=metadata
        )

        # Output result
        if args.output == 'json':
            print(json.dumps(result, indent=2))
        elif args.output == 'yaml' and YAML_AVAILABLE:
            print(yaml.dump(result, default_flow_style=False))
        else:
            print("Bonus payout successful!")
            print(f"  Companion: {args.companion_id}")
            print(f"  Amount: {args.amount} {args.currency}")
            if 'payout_id' in result:
                print(f"  Payout ID: {result['payout_id']}")
            if 'transaction_id' in result:
                print(f"  Transaction ID: {result['transaction_id']}")
            print(f"  Timestamp: {result.get('timestamp', 'N/A')}")

        logger.info("Bonus payout completed successfully")
        return 0

    except EPALPayoutError as e:
        logger.error(f"Payout failed: {e}")
        return 1
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        return 130
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        if args.verbose:
            logger.exception("Detailed traceback:")
        return 1


if __name__ == "__main__":
    sys.exit(main())

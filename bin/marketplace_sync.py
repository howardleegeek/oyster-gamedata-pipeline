#!/usr/bin/env python3
"""
Oyster Marketplace Sync Tool

Buyer-side helper to sync sessions from the marketplace into training pipelines.

Usage:
    oyster-marketplace sync --filter "audit_score>=101 and has_depth and quality_score>=80" \
        --since 2026-05-17 --output ./oyster-data/

Environment variables:
    OYSTER_API_URL: Base URL for Oyster Marketplace API (default: https://api.oyster.ai)
    OYSTER_API_KEY: API key for authentication
"""

import argparse
import json
import logging
import os
import sys
import time
from typing import Any, Dict, Optional

# Configure logging
logger = logging.getLogger(__name__)

# Try to import requests, fall back to urllib
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    import urllib.error
    import urllib.request
    HAS_REQUESTS = False


class FilterParser:
    """Parse filter expressions into API query parameters."""

    OPERATORS = {
        ">=": "gte",
        "<=": "lte",
        ">": "gt",
        "<": "lt",
        "=": "eq",
        "!=": "ne",
    }

    def __init__(self, filter_str: str):
        self.filter_str = filter_str
        self.params = {}

    def parse(self) -> Dict[str, Any]:
        """Parse filter string into query parameters."""
        if not self.filter_str:
            return {}

        # Split by 'and' (case-insensitive)
        conditions = [c.strip() for c in self.filter_str.split(" and ")]

        for condition in conditions:
            self._parse_condition(condition)

        return self.params

    def _parse_condition(self, condition: str) -> None:
        """Parse a single condition."""
        # Try each operator
        for op, op_name in self.OPERATORS.items():
            if op in condition:
                parts = condition.split(op, 1)
                if len(parts) == 2:
                    field = parts[0].strip()
                    value = parts[1].strip()

                    # Convert value to appropriate type
                    if value.lower() == "true":
                        value = True
                    elif value.lower() == "false":
                        value = False
                    else:
                        try:
                            value = int(value)
                        except ValueError:
                            try:
                                value = float(value)
                            except ValueError as exc:
                                # Value remains as string; log for debugging
                                logger.debug(
                                    "filter value %r could not be parsed as int or float: %s",
                                    value,
                                    exc,
                                )

                    # Map to API parameter names
                    if op == ">=":
                        self.params[f"{field}_min"] = value
                    elif op == "<=":
                        self.params[f"{field}_max"] = value
                    elif field == "has_depth":
                        self.params["has_depth"] = value
                    elif field == "has_audio":
                        self.params["has_audio"] = value
                    elif field == "has_voice":
                        self.params["has_voice"] = value
                    elif field == "has_zbuffer":
                        self.params["has_zbuffer"] = value
                    elif field == "audit_score":
                        if op == ">=":
                            self.params["audit_score_min"] = value
                        elif op == ">":
                            self.params["audit_score_min"] = value + 1
                    elif field == "quality_score":
                        if op == ">=":
                            self.params["quality_score_min"] = value
                        elif op == ">":
                            self.params["quality_score_min"] = value + 1
                    else:
                        self.params[field] = value

                    return

        print(f"Warning: Could not parse condition: {condition}", file=sys.stderr)


class OysterClient:
    """Client for Oyster Marketplace API."""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        self.base_url = (base_url or os.environ.get("OYSTER_API_URL", "https://api.oyster.ai")).rstrip("/")
        self.api_key = api_key or os.environ.get("OYSTER_API_KEY", "")
        self.session_id = None

        if not self.api_key:
            print("Warning: No API key provided. Set OYSTER_API_KEY environment variable.", file=sys.stderr)

    def _request(self, method: str, path: str, params: Optional[Dict] = None, data: Optional[Dict] = None) -> Dict:
        """Make HTTP request to API."""
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        if HAS_REQUESTS:
            response = requests.request(
                method,
                url,
                headers=headers,
                params=params,
                json=data,
                timeout=30,
            )

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "60")
                raise Exception(f"Rate limited. Retry after {retry_after} seconds.")

            response.raise_for_status()
            return response.json()
        else:
            # Use urllib
            import urllib.parse

            if params:
                url += "?" + urllib.parse.urlencode(params)

            req_data = None
            if data:
                req_data = json.dumps(data).encode('utf-8')

            req = urllib.request.Request(
                url,
                data=req_data,
                headers=headers,
                method=method,
            )

            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    return json.loads(response.read().decode('utf-8'))
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    retry_after = e.headers.get("Retry-After", "60")
                    raise Exception(f"Rate limited. Retry after {retry_after} seconds.")
                raise

    def list_sessions(self, params: Optional[Dict] = None) -> Dict:
        """List sessions with filters."""
        return self._request("GET", "/api/v1/sessions", params=params)

    def get_session(self, session_id: str) -> Dict:
        """Get single session details."""
        return self._request("GET", f"/api/v1/sessions/{session_id}")

    def get_audit(self, session_id: str) -> Dict:
        """Get session audit results."""
        return self._request("GET", f"/api/v1/sessions/{session_id}/audit")

    def verify_session(self, session_id: str) -> Dict:
        """Verify session provenance."""
        return self._request("GET", f"/api/v1/sessions/{session_id}/verify")

    def create_bulk_download(self, filters: Dict, since: Optional[str] = None) -> Dict:
        """Create bulk download job."""
        data = {"filters": filters}
        if since:
            data["since"] = since
        return self._request("POST", "/api/v1/sessions/bulk-download", data=data)

    def get_bulk_download(self, job_id: str) -> Dict:
        """Get bulk download job status."""
        return self._request("GET", f"/api/v1/bulk-download/{job_id}")

    def approve_session(self, session_id: str, notes: Optional[str] = None) -> Dict:
        """Approve a session."""
        data = {}
        if notes:
            data["notes"] = notes
        return self._request("POST", f"/api/v1/sessions/{session_id}/approve", data=data)

    def reject_session(self, session_id: str, reason: str, notes: Optional[str] = None) -> Dict:
        """Reject a session."""
        data = {"reason": reason}
        if notes:
            data["notes"] = notes
        return self._request("POST", f"/api/v1/sessions/{session_id}/reject", data=data)


def download_file(url: str, output_path: str) -> None:
    """Download a file from URL."""
    print(f"Downloading: {url}")

    if HAS_REQUESTS:
        response = requests.get(url, stream=True, timeout=300)
        response.raise_for_status()

        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
    else:
        urllib.request.urlretrieve(url, output_path)

    print(f"Saved to: {output_path}")


def cmd_sync(args):
    """Sync sessions from marketplace."""
    client = OysterClient(args.api_url, args.api_key)

    # Parse filter
    parser = FilterParser(args.filter)
    params = parser.parse()

    # Add since parameter
    if args.since:
        params["since"] = args.since

    print(f"Fetching sessions with filter: {args.filter}")
    print(f"Parameters: {params}")

    # Fetch sessions
    result = client.list_sessions(params)

    sessions = result.get("sessions", [])
    total = result.get("total", 0)

    print(f"Found {total} sessions")

    # Create output directory
    os.makedirs(args.output, exist_ok=True)

    # Save session metadata
    metadata_path = os.path.join(args.output, "sessions.json")
    with open(metadata_path, 'w') as f:
        json.dump(result, f, indent=2)
    print(f"Saved metadata to: {metadata_path}")

    # Download session files if requested
    if args.download:
        for session in sessions:
            session_id = session["id"]
            session_dir = os.path.join(args.output, session_id)
            os.makedirs(session_dir, exist_ok=True)

            # Get full session details with download URLs
            full_session = client.get_session(session_id)
            download_urls = full_session.get("download_urls", {})

            for file_type, url in download_urls.items():
                output_path = os.path.join(session_dir, f"{file_type}.tar.gz")
                try:
                    download_file(url, output_path)
                except Exception as e:
                    print(f"Error downloading {file_type} for {session_id}: {e}", file=sys.stderr)

    print(f"\nSync complete. {len(sessions)} sessions synced to {args.output}")


def cmd_bulk_download(args):
    """Create and poll bulk download job."""
    client = OysterClient(args.api_url, args.api_key)

    # Parse filter
    parser = FilterParser(args.filter)
    params = parser.parse()

    print(f"Creating bulk download job with filter: {args.filter}")

    # Create job
    job = client.create_bulk_download(params, args.since)
    job_id = job["job_id"]

    print(f"Job created: {job_id}")
    print(f"Status: {job['status']}")
    print(f"Total sessions: {job.get('total_sessions', 'unknown')}")

    # Poll for completion
    if args.wait:
        while job["status"] not in ("completed", "failed"):
            print(f"Waiting {args.poll_interval}s...")
            time.sleep(args.poll_interval)
            job = client.get_bulk_download(job_id)
            print(f"Status: {job['status']}")

        if job["status"] == "completed":
            print(f"\nDownload ready: {job.get('download_url')}")

            if args.output:
                os.makedirs(args.output, exist_ok=True)
                output_path = os.path.join(args.output, f"bulk_{job_id}.tar.gz")
                download_file(job["download_url"], output_path)
        else:
            print(f"Job failed: {job}", file=sys.stderr)
            sys.exit(1)

    return job


def cmd_list(args):
    """List sessions without downloading."""
    client = OysterClient(args.api_url, args.api_key)

    # Parse filter
    parser = FilterParser(args.filter)
    params = parser.parse()

    # Pagination
    params["page"] = args.page
    params["page_size"] = args.page_size

    result = client.list_sessions(params)

    sessions = result.get("sessions", [])

    print(f"Sessions (page {result['page']}, showing {len(sessions)} of {result['total']}):")
    print("-" * 80)

    for session in sessions:
        print(f"ID: {session['id']}")
        print(f"  Game: {session['game']}, Scene: {session['scene']}")
        print(f"  Audit Score: {session['audit_score']}, Quality: {session['quality_score']}")
        print(f"  Depth: {session['has_depth']}, Audio: {session['has_audio']}, Voice: {session['has_voice']}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Oyster Marketplace Sync Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List sessions with filter
  oyster-marketplace list --filter "audit_score>=100 and has_depth"

  # Sync sessions since a date
  oyster-marketplace sync --filter "quality_score>=80" --since 2026-05-17 --output ./data/

  # Create bulk download job
  oyster-marketplace bulk --filter "has_depth and has_audio" --wait --output ./downloads/
"""
    )

    parser.add_argument("--api-url", help="Oyster API base URL")
    parser.add_argument("--api-key", help="Oyster API key")

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Sync command
    sync_parser = subparsers.add_parser("sync", help="Sync sessions to local directory")
    sync_parser.add_argument("--filter", "-f", default="", help="Filter expression")
    sync_parser.add_argument("--since", help="Only sessions created since this date")
    sync_parser.add_argument("--output", "-o", default="./oyster-data/", help="Output directory")
    sync_parser.add_argument("--download", "-d", action="store_true", help="Download session files")

    # Bulk download command
    bulk_parser = subparsers.add_parser("bulk", help="Create bulk download job")
    bulk_parser.add_argument("--filter", "-f", default="", help="Filter expression")
    bulk_parser.add_argument("--since", help="Only sessions created since this date")
    bulk_parser.add_argument("--wait", "-w", action="store_true", help="Wait for job completion")
    bulk_parser.add_argument("--poll-interval", type=int, default=5, help="Poll interval in seconds")
    bulk_parser.add_argument("--output", "-o", help="Output directory for download")

    # List command
    list_parser = subparsers.add_parser("list", help="List sessions")
    list_parser.add_argument("--filter", "-f", default="", help="Filter expression")
    list_parser.add_argument("--page", type=int, default=1, help="Page number")
    list_parser.add_argument("--page-size", type=int, default=50, help="Page size")

    args = parser.parse_args()

    if args.command == "sync":
        cmd_sync(args)
    elif args.command == "bulk":
        cmd_bulk_download(args)
    elif args.command == "list":
        cmd_list(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

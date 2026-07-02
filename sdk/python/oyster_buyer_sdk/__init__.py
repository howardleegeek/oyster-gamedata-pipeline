"""
Oyster Buyer SDK - Python SDK for Oyster data buyers.

Provides utilities for listing, downloading, and streaming dataset clips
with progress bars and automatic presigned URL refresh capabilities.
"""

__version__ = "1.0.0"

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple, Union


class OysterError(Exception):
    """Base exception for Oyster SDK errors."""
    pass


class AuthenticationError(OysterError):
    """Raised when authentication fails."""
    pass


class ClipNotFoundError(OysterError):
    """Raised when a requested clip is not found."""
    pass


class APIError(OysterError):
    """Raised for API-level errors."""
    pass


class ChecksumError(OysterError):
    """Raised when checksum verification fails."""
    pass


@dataclass
class Clip:
    """Represents a data clip in the Oyster marketplace."""
    clip_id: str
    name: str
    size_bytes: int
    created_at: datetime
    expires_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    checksum: Optional[str] = None
    format: str = "parquet"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clip_id": self.clip_id, "name": self.name, "size_bytes": self.size_bytes,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "metadata": self.metadata, "checksum": self.checksum, "format": self.format,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Clip":
        return cls(
            clip_id=data["clip_id"], name=data["name"], size_bytes=data["size_bytes"],
            created_at=datetime.fromisoformat(data["created_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
            metadata=data.get("metadata", {}), checksum=data.get("checksum"),
            format=data.get("format", "parquet"),
        )


class ProgressBar:
    """Simple progress bar for terminal output."""

    def __init__(self, total: int, width: int = 40, desc: str = "Progress"):
        self.total, self.width, self.desc = total, width, desc
        self.current, self.start_time = 0, time.time()

    def set_current(self, current: int) -> None:
        self.current = min(current, self.total)
        if self.total <= 0:
            return
        pct = self.current / self.total
        filled = int(self.width * pct)
        bar = "█" * filled + "░" * (self.width - filled)
        elapsed = time.time() - self.start_time
        speed = self.current / elapsed if elapsed > 0 else 0
        sys.stdout.write(f"\r{self.desc}: |{bar}| {pct*100:.1f}% ({speed/1024/1024:.1f} MB/s)")
        sys.stdout.flush()

    def close(self) -> None:
        sys.stdout.write("\n")
        sys.stdout.flush()


class PresignedURLManager:
    """Manages presigned URLs with automatic refresh capability."""

    def __init__(self, refresh_threshold_seconds: int = 300):
        self.refresh_threshold = timedelta(seconds=refresh_threshold_seconds)
        self._url_cache: Dict[str, Tuple[str, datetime]] = {}

    def get_url(self, clip_id: str, fetch_func: Callable[[str], Tuple[str, datetime]]) -> str:
        if clip_id in self._url_cache:
            url, expires_at = self._url_cache[clip_id]
            if datetime.utcnow() + self.refresh_threshold < expires_at:
                return url
        url, expires_at = fetch_func(clip_id)
        self._url_cache[clip_id] = (url, expires_at)
        return url

    def invalidate(self, clip_id: str) -> None:
        self._url_cache.pop(clip_id, None)


class OysterClient:
    """Main client for interacting with the Oyster data marketplace."""

    def __init__(
        self, api_key: Optional[str] = None, base_url: str = "https://api.oyster.ai",
        timeout: int = 30, max_retries: int = 3, show_progress: bool = True,
    ):
        self.api_key = api_key or os.environ.get("OYSTER_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.timeout, self.max_retries, self.show_progress = timeout, max_retries, show_progress
        self._url_manager = PresignedURLManager()

    def _get_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Dict:
        url = f"{self.base_url}{endpoint}"
        headers, body = self._get_headers(), json.dumps(data).encode() if data else None
        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(url, data=body, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise AuthenticationError("Invalid API key") from e
                if e.code == 404:
                    raise ClipNotFoundError(f"Resource not found: {endpoint}") from e
                if attempt == self.max_retries - 1:
                    raise APIError(f"HTTP {e.code}: {e.reason}") from e
                time.sleep(2 ** attempt)
            except urllib.error.URLError as e:
                if attempt == self.max_retries - 1:
                    raise APIError(f"Connection error: {e.reason}") from e
                time.sleep(2 ** attempt)
        raise APIError("Max retries exceeded")

    def list_clips(self, limit: int = 100, offset: int = 0, filters: Optional[Dict] = None) -> List[Clip]:
        """List available clips in the marketplace."""
        params = {"limit": limit, "offset": offset, **(filters or {})}
        resp = self._make_request("GET", "/v1/clips", params if filters else None)
        return [Clip.from_dict(item) for item in resp.get("clips", [])]

    def get_clip(self, clip_id: str) -> Clip:
        """Get details for a specific clip."""
        return Clip.from_dict(self._make_request("GET", f"/v1/clips/{clip_id}"))

    def _fetch_presigned_url(self, clip_id: str) -> Tuple[str, datetime]:
        resp = self._make_request("GET", f"/v1/clips/{clip_id}/download-url")
        return resp["url"], datetime.fromisoformat(resp["expires_at"])

    def download_clip(
        self, clip_id: str, output_dir: Union[str, Path], filename: Optional[str] = None,
        chunk_size: int = 8192, verify_checksum: bool = True,
    ) -> Path:
        """Download a clip to the specified directory."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        clip = self.get_clip(clip_id)
        filename = filename or f"{clip.name}.{clip.format}"
        output_path = output_dir / filename
        url = self._url_manager.get_url(clip_id, self._fetch_presigned_url)
        progress_bar = ProgressBar(clip.size_bytes, desc=clip_id) if self.show_progress else None
        temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        downloaded = 0
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                with open(temp_path, "wb") as f:
                    while chunk := resp.read(chunk_size):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_bar:
                            progress_bar.set_current(downloaded)
            if verify_checksum and clip.checksum:
                self._verify_checksum(temp_path, clip.checksum)
            temp_path.rename(output_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
            if progress_bar:
                progress_bar.close()
        return output_path

    def _verify_checksum(self, filepath: Path, expected: str) -> None:
        sha256 = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        if sha256.hexdigest() != expected:
            raise ChecksumError(f"Checksum mismatch: expected {expected}")

    def stream_dataset(self, clip_id: str, chunk_size: int = 8192) -> Generator[bytes, None, None]:
        """Stream a dataset clip as a generator of bytes."""
        url = self._url_manager.get_url(clip_id, self._fetch_presigned_url)
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            while chunk := resp.read(chunk_size):
                yield chunk


# Module-level convenience functions
_default_client: Optional[OysterClient] = None


def get_client() -> OysterClient:
    """Get or create the default client instance."""
    global _default_client
    if _default_client is None:
        _default_client = OysterClient()
    return _default_client


def list_clips(limit: int = 100, offset: int = 0) -> List[Clip]:
    """List available clips using the default client."""
    return get_client().list_clips(limit=limit, offset=offset)


def download_clip(clip_id: str, output_dir: Union[str, Path], filename: Optional[str] = None) -> Path:
    """Download a clip using the default client."""
    return get_client().download_clip(clip_id, output_dir, filename=filename)


def stream_dataset(clip_id: str) -> Generator[bytes, None, None]:
    """Stream a dataset clip using the default client."""
    return get_client().stream_dataset(clip_id)


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for CLI usage."""
    parser = argparse.ArgumentParser(prog="oyster-buyer-sdk", description="Oyster Buyer SDK CLI")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--api-key", help="API key for authentication")
    parser.add_argument("--base-url", default="https://api.oyster.ai", help="API base URL")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_p = subparsers.add_parser("list", help="List available clips")
    list_p.add_argument("--limit", type=int, default=100)
    list_p.add_argument("--offset", type=int, default=0)
    list_p.add_argument("--json", action="store_true", help="Output as JSON")

    dl_p = subparsers.add_parser("download", help="Download a clip")
    dl_p.add_argument("clip_id", help="Clip ID to download")
    dl_p.add_argument("-o", "--output-dir", default=".", help="Output directory")
    dl_p.add_argument("--filename", help="Custom output filename")

    stream_p = subparsers.add_parser("stream", help="Stream a clip to stdout")
    stream_p.add_argument("clip_id", help="Clip ID to stream")

    args = parser.parse_args(argv)
    try:
        client = OysterClient(api_key=args.api_key, base_url=args.base_url)
        if args.command == "list":
            clips = client.list_clips(limit=args.limit, offset=args.offset)
            if args.json:
                print(json.dumps([c.to_dict() for c in clips], indent=2))
            else:
                for c in clips:
                    print(f"{c.clip_id}\t{c.name}\t{c.size_bytes} bytes")
        elif args.command == "download":
            path = client.download_clip(args.clip_id, args.output_dir, args.filename)
            print(f"Downloaded to: {path}")
        elif args.command == "stream":
            for chunk in client.stream_dataset(args.clip_id):
                sys.stdout.buffer.write(chunk)
        return 0
    except AuthenticationError as e:
        print(f"Auth error: {e}", file=sys.stderr)
        return 1
    except ClipNotFoundError as e:
        print(f"Not found: {e}", file=sys.stderr)
        return 2
    except APIError as e:
        print(f"API error: {e}", file=sys.stderr)
        return 3
    except OysterError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
G250 · Update Server Endpoint

FastAPI endpoint serving latest version + download URL + SHA256 + release notes;
backs G225 auto-updater.

Usage:
    python -m bin.update_server_endpoint --port 8080 --data-path ./updates/
    python -m bin.update_server_endpoint --help
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

# Lazy imports for heavy dependencies (pydantic/torch must be lazy per spec)
_fastapi: Optional[Any] = None
_pydantic: Optional[Any] = None


def _get_fastapi():
    """Lazy import FastAPI to avoid loading pydantic at module level."""
    global _fastapi
    if _fastapi is None:
        import fastapi
        _fastapi = fastapi
    return _fastapi


def _get_pydantic():
    """Lazy import pydantic to avoid eager loading."""
    global _pydantic
    if _pydantic is None:
        import pydantic
        _pydantic = pydantic
    return _pydantic


class UpdateInfo:
    """Update information model for the auto-updater."""
    
    def __init__(
        self,
        version: str,
        download_url: str,
        sha256: str,
        release_notes: str,
        min_system_version: Optional[str] = None,
    ) -> None:
        self.version = version
        self.download_url = download_url
        self.sha256 = sha256
        self.release_notes = release_notes
        self.min_system_version = min_system_version
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "version": self.version,
            "download_url": self.download_url,
            "sha256": self.sha256,
            "release_notes": self.release_notes,
        }
        if self.min_system_version:
            result["min_system_version"] = self.min_system_version
        return result


class UpdateCheckResponse:
    """Response model for /api/updates/check endpoint."""
    
    def __init__(
        self,
        latest_version: str,
        download_url: str,
        sha256: str,
        release_notes: str,
        update_available: bool,
        min_system_version: Optional[str] = None,
    ) -> None:
        self.latest_version = latest_version
        self.download_url = download_url
        self.sha256 = sha256
        self.release_notes = release_notes
        self.update_available = update_available
        self.min_system_version = min_system_version
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "latest_version": self.latest_version,
            "download_url": self.download_url,
            "sha256": self.sha256,
            "release_notes": self.release_notes,
            "update_available": self.update_available,
        }
        if self.min_system_version:
            result["min_system_version"] = self.min_system_version
        return result


def parse_version(version: str) -> tuple[int, ...]:
    """Parse version string into tuple for comparison.
    
    Args:
        version: Version string like "1.2.3"
        
    Returns:
        Tuple of integers for comparison, e.g., (1, 2, 3)
    """
    try:
        return tuple(int(part) for part in version.split("."))
    except (ValueError, AttributeError):
        return (0,)


def is_newer_version(current: str, latest: str) -> bool:
    """Check if latest version is newer than current version.
    
    Args:
        current: Current version string
        latest: Latest version string
        
    Returns:
        True if latest is newer than current
    """
    current_tuple = parse_version(current)
    latest_tuple = parse_version(latest)
    return latest_tuple > current_tuple


def load_update_data(data_path: Path) -> dict[str, UpdateInfo]:
    """Load update data from JSON file.
    
    Args:
        data_path: Path to the updates data directory
        
    Returns:
        Dictionary mapping platform to UpdateInfo
    """
    updates_file = data_path / "updates.json"
    
    if not updates_file.exists():
        # Return empty dict if no updates file
        return {}
    
    with open(updates_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    updates = {}
    for platform, info in data.items():
        updates[platform] = UpdateInfo(
            version=info.get("version", "0.0.0"),
            download_url=info.get("download_url", ""),
            sha256=info.get("sha256", ""),
            release_notes=info.get("release_notes", ""),
            min_system_version=info.get("min_system_version"),
        )
    
    return updates


def create_app(data_path: Path):
    """Create FastAPI application with update endpoint.
    
    Args:
        data_path: Path to the updates data directory
        
    Returns:
        FastAPI application instance
    """
    fastapi = _get_fastapi()
    
    app = fastapi.FastAPI(
        title="G225 Update Server",
        description="Auto-updater endpoint for G225 client",
        version="1.0.0",
    )
    
    # Load update data at startup
    updates = load_update_data(data_path)
    
    @app.get("/api/updates/check")
    async def check_update(
        platform: str = fastapi.Query(..., description="Target platform (e.g., windows, macos, linux)"),
        current: str = fastapi.Query(..., description="Current version string"),
    ) -> dict[str, Any]:
        """Check for available updates.
        
        Query parameters:
            platform: Target platform (windows, macos, linux)
            current: Current version string (e.g., "1.0.0")
            
        Returns:
            JSON with latest version, download URL, SHA256, release notes,
            and whether an update is available
        """
        # Normalize platform name
        platform_key = platform.lower()
        
        if platform_key not in updates:
            return {
                "error": f"Platform '{platform}' not supported",
                "update_available": False,
            }
        
        update_info = updates[platform_key]
        update_available = is_newer_version(current, update_info.version)
        
        response = UpdateCheckResponse(
            latest_version=update_info.version,
            download_url=update_info.download_url,
            sha256=update_info.sha256,
            release_notes=update_info.release_notes,
            update_available=update_available,
            min_system_version=update_info.min_system_version,
        )
        
        return response.to_dict()
    
    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "healthy"}
    
    return app


def main(argv: list[str]) -> int:
    """Main entry point for the update server.
    
    Args:
        argv: Command line arguments (excluding script name)
        
    Returns:
        Exit code (0 for success, non-zero for error)
    """
    parser = argparse.ArgumentParser(
        description="G225 Update Server - FastAPI endpoint for auto-updater",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s --port 8080 --data-path ./updates/
    %(prog)s --port 443 --data-path /etc/app/updates --host 0.0.0.0
        """,
    )
    
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to listen on (default: 8080)",
    )
    
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    
    parser.add_argument(
        "--data-path",
        type=Path,
        default=Path("./updates"),
        help="Path to updates data directory (default: ./updates)",
    )
    
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    
    args = parser.parse_args(argv)
    
    # Validate data path
    if not args.data_path.exists():
        print(f"Warning: Data path '{args.data_path}' does not exist", file=sys.stderr)
        # Create temp directory for demo purposes
        temp_dir = Path(tempfile.mkdtemp())
        print(f"Using temporary directory: {temp_dir}", file=sys.stderr)
        args.data_path = temp_dir
    
    # Create FastAPI app
    app = create_app(args.data_path)
    
    # Import uvicorn for running the server
    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn is required to run the server", file=sys.stderr)
        print("Install with: pip install uvicorn", file=sys.stderr)
        return 1
    
    # Run the server
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

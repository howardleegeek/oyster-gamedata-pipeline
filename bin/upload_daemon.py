#!/usr/bin/env python3
"""
S3 Multipart Upload Daemon with Resume-on-failure and Bandwidth Throttling

Watches ~/Documents/OysterClips/ for new finalized sessions and uploads them
to S3 in the background with automatic resume on failure.
"""

import argparse
import hashlib
import json
import logging
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# Constants
CHUNK_SIZE = 8 * 1024 * 1024  # 8MB chunks
DEFAULT_MAX_KBPS = 5000  # 5 MB/s
DEFAULT_WATCH_DIR = Path.home() / "Documents" / "OysterClips"
STATE_FILE = Path.home() / ".oyster" / "upload_state.json"
LOG_FILE = Path.home() / ".oyster" / "upload.log"
PROVENANCE_FILE = Path.home() / ".oyster" / "oyster_provenance"

# Configure logging. LOG_FILE parent (~/.oyster/) may not exist on a fresh
# machine or CI runner — mkdir before opening the handler, otherwise import-
# time crash with FileNotFoundError prevents the module from being loaded at
# all (which breaks pytest collection on CI).
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


class UploadState(Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ChunkInfo:
    index: int
    offset: int
    size: int
    etag: Optional[str] = None
    uploaded: bool = False


@dataclass
class UploadSession:
    session_id: str
    local_path: str
    file_size: int
    sha256: str
    state: str
    upload_id: Optional[str] = None
    chunks: List[ChunkInfo] = None
    progress: float = 0.0
    error: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    bandwidth_kbps: float = 0.0

    def __post_init__(self):
        if self.chunks is None:
            self.chunks = []
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()


class UploadDaemon:
    """Background daemon for uploading sessions to S3."""

    def __init__(
        self,
        watch_dir: Path = DEFAULT_WATCH_DIR,
        max_kbps: int = DEFAULT_MAX_KBPS,
        s3_endpoint: str = "http://localhost:8080",
        dry_run: bool = False,
    ):
        self.watch_dir = watch_dir
        self.max_kbps = max_kbps
        self.s3_endpoint = s3_endpoint
        self.dry_run = dry_run
        self.running = False
        self.current_upload: Optional[UploadSession] = None
        self.state: Dict[str, Any] = {"sessions": {}, "last_scan": None}
        self._lock = threading.Lock()

        # Ensure directories exist
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Load persisted state
        self._load_state()

    def _load_state(self):
        """Load persisted upload state from disk."""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r") as f:
                    state_data = json.load(f)

                # Convert sessions back to UploadSession objects
                sessions = {}
                for session_id, session_data in state_data.get("sessions", {}).items():
                    # Convert chunks to ChunkInfo objects
                    chunks = []
                    if "chunks" in session_data and session_data["chunks"]:
                        for chunk_data in session_data["chunks"]:
                            if isinstance(chunk_data, dict):
                                chunks.append(ChunkInfo(**chunk_data))
                            else:
                                chunks.append(chunk_data)

                    session_data["chunks"] = chunks
                    sessions[session_id] = UploadSession(**session_data)

                self.state = {"sessions": sessions, "last_scan": state_data.get("last_scan")}
                logger.info(f"Loaded {len(sessions)} sessions from state")
            except Exception as e:
                logger.error(f"Failed to load state: {e}")
                self.state = {"sessions": {}, "last_scan": None}

    def _save_state(self):
        """Persist upload state to disk."""
        # Convert sessions to dicts for JSON serialization
        sessions_dict = {}
        for session_id, session in self.state.get("sessions", {}).items():
            if isinstance(session, UploadSession):
                session_dict = asdict(session)
                # Convert chunks to dicts
                if session_dict.get("chunks"):
                    session_dict["chunks"] = [
                        asdict(c) if isinstance(c, ChunkInfo) else c for c in session_dict["chunks"]
                    ]
                sessions_dict[session_id] = session_dict
            else:
                sessions_dict[session_id] = session

        state_to_save = {"sessions": sessions_dict, "last_scan": datetime.now().isoformat()}

        with open(STATE_FILE, "w") as f:
            json.dump(state_to_save, f, indent=2)

    def _compute_sha256(self, file_path: Path, progress_callback=None) -> str:
        """Compute SHA256 hash of file."""
        sha256 = hashlib.sha256()
        file_size = file_path.stat().st_size
        bytes_read = 0

        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                sha256.update(chunk)
                bytes_read += len(chunk)
                if progress_callback:
                    progress_callback(bytes_read, file_size)

        return sha256.hexdigest()

    def _init_multipart_upload(self, session_id: str, file_size: int, sha256: str) -> Dict:
        """Initialize multipart upload via API."""
        url = f"{self.s3_endpoint}/api/upload/init"
        data = json.dumps(
            {"session_id": session_id, "file_size": file_size, "sha256": sha256}
        ).encode("utf-8")

        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as e:
            logger.error(f"Failed to init multipart upload: {e}")
            raise

    def _get_presigned_url(self, session_id: str, upload_id: str, part_number: int) -> str:
        """Get presigned URL for uploading a chunk."""
        url = f"{self.s3_endpoint}/api/upload/url"
        data = json.dumps(
            {"session_id": session_id, "upload_id": upload_id, "part_number": part_number}
        ).encode("utf-8")

        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
                return result["url"]
        except Exception as e:
            logger.error(f"Failed to get presigned URL: {e}")
            raise

    def _complete_multipart_upload(
        self, session_id: str, upload_id: str, parts: List[Dict]
    ) -> Dict:
        """Complete multipart upload."""
        url = f"{self.s3_endpoint}/api/upload/complete"
        data = json.dumps(
            {"session_id": session_id, "upload_id": upload_id, "parts": parts}
        ).encode("utf-8")

        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as e:
            logger.error(f"Failed to complete multipart upload: {e}")
            raise

    def _upload_chunk(self, url: str, data: bytes, retry_count: int = 3) -> str:
        """Upload a single chunk with retry and exponential backoff."""
        for attempt in range(retry_count):
            try:
                req = urllib.request.Request(url, data=data, method="PUT")
                req.add_header("Content-Length", str(len(data)))

                with urllib.request.urlopen(req, timeout=120) as response:
                    etag = response.headers.get("ETag", "")
                    logger.debug(f"Chunk uploaded successfully, ETag: {etag}")
                    return etag
            except urllib.error.HTTPError as e:
                if e.code >= 500 and attempt < retry_count - 1:
                    wait_time = 2**attempt
                    logger.warning(f"Server error {e.code}, retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    raise
            except Exception as e:
                if attempt < retry_count - 1:
                    wait_time = 2**attempt
                    logger.warning(f"Upload failed: {e}, retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    raise

        raise Exception("Max retries exceeded")

    def _throttle(self, bytes_sent: int, start_time: float):
        """Apply bandwidth throttling."""
        elapsed = time.time() - start_time
        expected_time = bytes_sent / (self.max_kbps * 1024 / 8)

        if elapsed < expected_time:
            time.sleep(expected_time - elapsed)

    def _load_provenance_manifest(self, session_id: str) -> Optional[Dict]:
        """Load provenance manifest for a session."""
        provenance_path = PROVENANCE_FILE
        if not provenance_path.exists():
            return None

        try:
            with open(provenance_path, "r") as f:
                data = json.load(f)
                return data.get(session_id)
        except Exception as e:
            logger.warning(f"Failed to load provenance: {e}")
            return None

    def _is_wifi_only(self) -> bool:
        """Check if we're on WiFi (not cellular)."""
        # On macOS, check network interface
        try:
            result = subprocess.run(
                ["networksetup", "-listallhardwareports"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            # Check if WiFi is active and no cellular
            output = result.stdout
            if "Wi-Fi" in output and "Cellular" not in output:
                return True
            # Default to WiFi-only if we can't determine
            return True
        except Exception:
            # Default to WiFi-only
            return True

    def _scan_for_sessions(self) -> List[Path]:
        """Scan for new finalized sessions."""
        if not self.watch_dir.exists():
            logger.warning(f"Watch directory does not exist: {self.watch_dir}")
            return []

        sessions = []
        pattern = "clip-*.tar.gz"

        for file_path in self.watch_dir.glob(pattern):
            # Skip already uploaded files
            if ".uploaded." in file_path.name:
                continue

            # Check if already in our state
            session_id = file_path.stem  # clip-YYYYMMDD-HHMMSS

            if session_id not in self.state.get("sessions", {}):
                sessions.append(file_path)

        return sessions

    def _process_session(self, file_path: Path) -> bool:
        """Process a single session for upload."""
        session_id = file_path.stem
        file_size = file_path.stat().st_size

        logger.info(f"Processing session: {session_id} ({file_size} bytes)")

        # Check if already completed (idempotent)
        if session_id in self.state.get("sessions", {}):
            session = self.state["sessions"][session_id]
            if isinstance(session, UploadSession) and session.state == UploadState.COMPLETED.value:
                logger.info(f"Session {session_id} already uploaded, skipping")
                return True

        # Compute SHA256
        logger.info(f"Computing SHA256 for {session_id}...")
        sha256 = self._compute_sha256(file_path)
        logger.info(f"SHA256: {sha256}")

        # Create session record
        session = UploadSession(
            session_id=session_id,
            local_path=str(file_path),
            file_size=file_size,
            sha256=sha256,
            state=UploadState.PENDING.value,
            created_at=datetime.now().isoformat(),
        )

        # Calculate chunks
        num_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        for i in range(num_chunks):
            offset = i * CHUNK_SIZE
            size = min(CHUNK_SIZE, file_size - offset)
            session.chunks.append(ChunkInfo(index=i + 1, offset=offset, size=size, uploaded=False))

        self.state["sessions"][session_id] = session
        self._save_state()

        # Check WiFi-only constraint
        if not self._is_wifi_only():
            logger.warning("Not on WiFi, skipping upload to preserve cellular data")
            return False

        # Initialize multipart upload
        try:
            logger.info(f"Initializing multipart upload for {session_id}...")
            init_result = self._init_multipart_upload(session_id, file_size, sha256)
            session.upload_id = init_result["upload_id"]
            session.state = UploadState.UPLOADING.value
            self._save_state()
        except Exception as e:
            logger.error(f"Failed to init multipart upload: {e}")
            session.state = UploadState.FAILED.value
            session.error = str(e)
            self._save_state()
            return False

        # Upload chunks
        parts = []
        total_bytes = 0

        for chunk in session.chunks:
            if chunk.uploaded:
                # Resume from where we left off
                logger.info(f"Resuming chunk {chunk.index}")
                parts.append({"PartNumber": chunk.index, "ETag": chunk.etag})
                total_bytes += chunk.size
                continue

            # Read chunk data
            with open(file_path, "rb") as f:
                f.seek(chunk.offset)
                chunk_data = f.read(chunk.size)

            # Get presigned URL
            try:
                url = self._get_presigned_url(session_id, session.upload_id, chunk.index)
            except Exception as e:
                logger.error(f"Failed to get presigned URL for chunk {chunk.index}: {e}")
                session.state = UploadState.FAILED.value
                session.error = str(e)
                self._save_state()
                return False

            # Upload chunk
            try:
                chunk_start = time.time()
                etag = self._upload_chunk(url, chunk_data)
                chunk.etag = etag
                chunk.uploaded = True

                # Update progress
                total_bytes += chunk.size
                session.progress = (total_bytes / file_size) * 100

                # Calculate bandwidth
                elapsed = time.time() - chunk_start
                if elapsed > 0:
                    session.bandwidth_kbps = (chunk.size / 1024) / elapsed

                parts.append({"PartNumber": chunk.index, "ETag": etag})

                logger.info(
                    f"Uploaded chunk {chunk.index}/{len(session.chunks)} "
                    f"({session.progress:.1f}%, {session.bandwidth_kbps:.1f} KB/s)"
                )

                # Throttle
                self._throttle(chunk.size, chunk_start)

                # Save progress
                self._save_state()

            except Exception as e:
                logger.error(f"Failed to upload chunk {chunk.index}: {e}")
                session.state = UploadState.FAILED.value
                session.error = str(e)
                self._save_state()
                return False

        # Complete multipart upload
        try:
            logger.info(f"Completing multipart upload for {session_id}...")
            result = self._complete_multipart_upload(session_id, session.upload_id, parts)

            # Verify SHA256
            if result.get("sha256") != sha256:
                logger.error(f"SHA256 mismatch! Expected {sha256}, got {result.get('sha256')}")
                session.state = UploadState.FAILED.value
                session.error = "SHA256 mismatch after upload"
                self._save_state()
                return False

            # Mark as completed
            session.state = UploadState.COMPLETED.value
            session.completed_at = datetime.now().isoformat()
            session.progress = 100.0

            # Rename file to indicate upload
            uploaded_path = file_path.parent / f"{file_path.stem}.uploaded.tar.gz"
            if not self.dry_run:
                file_path.rename(uploaded_path)

            logger.info(f"Session {session_id} uploaded successfully!")
            self._save_state()
            return True

        except Exception as e:
            logger.error(f"Failed to complete upload: {e}")
            session.state = UploadState.FAILED.value
            session.error = str(e)
            self._save_state()
            return False

    def run_once(self):
        """Run one iteration of the upload loop."""
        logger.info("Scanning for new sessions...")

        new_sessions = self._scan_for_sessions()
        logger.info(f"Found {len(new_sessions)} new sessions")

        for session_path in new_sessions:
            self._process_session(session_path)

        self.state["last_scan"] = datetime.now().isoformat()
        self._save_state()

    def run(self, interval: int = 60):
        """Run the daemon continuously."""
        self.running = True

        logger.info(f"Starting upload daemon (max {self.max_kbps} KB/s)")
        logger.info(f"Watching: {self.watch_dir}")

        # Handle shutdown signals
        def signal_handler(signum, frame):
            logger.info("Received shutdown signal, stopping...")
            self.running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        while self.running:
            try:
                self.run_once()
            except Exception as e:
                logger.error(f"Error in upload loop: {e}")

            # Sleep until next scan
            for _ in range(interval):
                if not self.running:
                    break
                time.sleep(1)

        logger.info("Upload daemon stopped")

    def get_status(self) -> Dict[str, Any]:
        """Get current status of all uploads."""
        sessions = self.state.get("sessions", {})

        pending = []
        uploading = []
        completed = []
        failed = []

        total_pending_size = 0
        total_completed_size = 0
        total_completed_count = 0

        for session_id, session in sessions.items():
            if isinstance(session, UploadSession):
                state = session.state
                file_size = session.file_size
            else:
                state = session.get("state", "pending")
                file_size = session.get("file_size", 0)

            if state == UploadState.PENDING.value:
                pending.append(session)
                total_pending_size += file_size
            elif state == UploadState.UPLOADING.value:
                uploading.append(session)
            elif state == UploadState.COMPLETED.value:
                completed.append(session)
                total_completed_size += file_size
                total_completed_count += 1
            elif state == UploadState.FAILED.value:
                failed.append(session)

        # Calculate last 24h stats
        last_24h_size = 0
        last_24h_count = 0
        last_24h_failures = 0

        cutoff = datetime.now() - timedelta(hours=24)
        for session in completed:
            if isinstance(session, UploadSession):
                completed_at = session.completed_at
                file_size = session.file_size
            else:
                completed_at = session.get("completed_at")
                file_size = session.get("file_size", 0)

            if completed_at:
                try:
                    dt = datetime.fromisoformat(completed_at)
                    if dt >= cutoff:
                        last_24h_count += 1
                        last_24h_size += file_size
                except Exception:
                    pass

        for session in failed:
            if isinstance(session, UploadSession):
                created_at = session.created_at
            else:
                created_at = session.get("created_at")

            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at)
                    if dt >= cutoff:
                        last_24h_failures += 1
                except Exception:
                    pass

        return {
            "pending": pending,
            "uploading": uploading,
            "completed": completed,
            "failed": failed,
            "total_pending_size": total_pending_size,
            "total_completed_size": total_completed_size,
            "total_completed_count": total_completed_count,
            "last_24h": {
                "count": last_24h_count,
                "size": last_24h_size,
                "failures": last_24h_failures,
            },
        }


def main():
    parser = argparse.ArgumentParser(description="S3 Upload Daemon")
    parser.add_argument(
        "--watch-dir",
        type=Path,
        default=DEFAULT_WATCH_DIR,
        help="Directory to watch for new sessions",
    )
    parser.add_argument(
        "--max-kbps",
        type=int,
        default=DEFAULT_MAX_KBPS,
        help="Maximum bandwidth in KB/s (default: 5000)",
    )
    parser.add_argument(
        "--s3-endpoint", type=str, default="http://localhost:8080", help="S3 API endpoint URL"
    )
    parser.add_argument(
        "--interval", type=int, default=60, help="Scan interval in seconds (default: 60)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Don't actually upload or rename files"
    )
    parser.add_argument("--once", action="store_true", help="Run once and exit")

    args = parser.parse_args()

    daemon = UploadDaemon(
        watch_dir=args.watch_dir,
        max_kbps=args.max_kbps,
        s3_endpoint=args.s3_endpoint,
        dry_run=args.dry_run,
    )

    if args.once:
        daemon.run_once()
    else:
        daemon.run(interval=args.interval)


if __name__ == "__main__":
    main()

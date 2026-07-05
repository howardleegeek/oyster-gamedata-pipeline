#!/usr/bin/env python3
"""
G225 · bin/auto_updater_winsparkle.py

Auto-update mechanism for desktop applications using WinSparkle / Squirrel.Mac
style update workflows. Polls an update server daily, downloads new installers,
and prompts users to restart or auto-updates during idle periods.

Usage:
    python auto_updater_winsparkle.py check --url <update_url> --version <current>
    python auto_updater_winsparkle.py download --url <update_url> --version <current>
    python auto_updater_winsparkle.py daemon --url <update_url> --version <current>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from xml.etree import ElementTree

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class UpdateStatus(Enum):
    """Enumeration of possible update states."""
    UP_TO_DATE = auto()
    UPDATE_AVAILABLE = auto()
    DOWNLOADING = auto()
    DOWNLOADED = auto()
    ERROR = auto()

@dataclass
class UpdateInfo:
    """Information about an available update."""
    version: str
    download_url: str
    file_size: int = 0
    checksum_sha256: str = ""
    release_notes: str = ""
    critical: bool = False
    release_date: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize update info to a dictionary."""
        return {k: v for k, v in self.__dict__.items()}

@dataclass
class AppConfig:
    """Application configuration for the updater."""
    app_name: str
    current_version: str
    update_url: str
    poll_interval_hours: int = 24
    auto_install_critical: bool = False
    download_dir: Optional[Path] = None

    def __post_init__(self) -> None:
        """Create a temporary download directory if none provided."""
        if self.download_dir is None:
            self.download_dir = Path(tempfile.mkdtemp(prefix="updater_"))

@dataclass
class UpdateState:
    """Current state of the update process."""
    status: UpdateStatus = UpdateStatus.UP_TO_DATE
    last_check: Optional[datetime] = None
    available_update: Optional[UpdateInfo] = None
    downloaded_file: Optional[Path] = None
    error_message: str = ""
    download_progress: float = 0.0

class AutoUpdater:
    """Main auto-updater implementing WinSparkle/Squirrel.Mac style updates."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.state = UpdateState()
        self._stop_event = threading.Event()
        self._daemon_thread: Optional[threading.Thread] = None
        self._callbacks: Dict[str, List[Callable[..., None]]] = {
            "update_available": [], "download_complete": [],
            "install_ready": [], "error": [],
        }

    def on(self, event: str, callback: Callable[..., None]) -> None:
        """Register a callback for a named update event."""
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def _emit(self, event: str, *args: Any, **kwargs: Any) -> None:
        """Fire all callbacks registered for *event*."""
        for cb in self._callbacks.get(event, []):
            try:
                cb(*args, **kwargs)
            except Exception as e:
                logger.debug("Callback error for event=%s: %s", event, repr(e))
                logger.exception("Callback error for event=%s", event)

    @staticmethod
    def _parse_version(ver: str) -> List[int]:
        """Parse a dotted version string into a list of ints."""
        parts: List[int] = []
        for seg in ver.strip().split("."):
            try:
                parts.append(int(seg))
            except ValueError:
                parts.append(0)
        return parts

    def _is_newer(self, candidate: str) -> bool:
        """Return True if *candidate* is newer than the current version."""
        return self._parse_version(candidate) > self._parse_version(self.config.current_version)

    def _parse_sparkle_xml(self, body: bytes) -> Optional[UpdateInfo]:
        """Parse a Sparkle-style XML appcast feed."""
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            logger.error("Failed to parse XML feed: %s", exc)
            return None
        ns = "http://www.andymatuschak.org/xml-namespaces/sparkle"
        for item in root.findall(".//item"):
            enc = item.find("enclosure")
            if enc is None:
                continue
            ver_elem = item.find(f"{{{ns}}}version")
            sha = enc.get(f"{{{ns}}}sha256Signature", "")
            notes_elem = item.find("description")
            date_elem = item.find("pubDate")
            return UpdateInfo(
                version=ver_elem.text if ver_elem is not None else "",
                download_url=enc.get("url", ""),
                file_size=int(enc.get("length", 0)),
                checksum_sha256=sha,
                release_notes=notes_elem.text if notes_elem is not None else "",
                release_date=date_elem.text if date_elem is not None else "",
            )
        return None

    def _parse_json_feed(self, body: bytes) -> Optional[UpdateInfo]:
        """Parse a JSON-format update feed."""
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse JSON feed: %s", exc)
            return None
        if isinstance(data, list) and data:
            data = data[0]
        version = data.get("version", "")
        if not self._is_newer(version):
            return None
        return UpdateInfo(
            version=version,
            download_url=data.get("url", data.get("download_url", "")),
            file_size=int(data.get("size", data.get("file_size", 0))),
            checksum_sha256=data.get("sha256", data.get("checksum", "")),
            release_notes=data.get("release_notes", data.get("notes", "")),
            critical=bool(data.get("critical", False)),
            release_date=data.get("release_date", data.get("date", "")),
        )

    def _fetch(self, url: str, timeout: int = 30) -> Optional[bytes]:
        """Download *url* and return raw bytes, or None on failure."""
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", f"{self.config.app_name}-Updater/1.0")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, OSError) as exc:
            logger.error("Fetch failed for %s: %s", url, exc)
            self.state.error_message = str(exc)
            self._emit("error", exc)
            return None

    def _download_file(self, url: str, dest: Path, progress_cb: Optional[Callable[[float], None]] = None) -> bool:
        """Download a file with optional progress reporting."""
        self.state.status = UpdateStatus.DOWNLOADING
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", f"{self.config.app_name}-Updater/1.0")
            with urllib.request.urlopen(req, timeout=120) as resp:
                total = int(resp.getheader("Content-Length", 0))
                downloaded = 0
                with open(dest, "wb") as fh:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if total > 0 and progress_cb:
                            progress_cb(downloaded / total)
                        self.state.download_progress = downloaded / total if total > 0 else 0.0
            return True
        except (urllib.error.URLError, OSError) as exc:
            logger.error("Download failed: %s", exc)
            self.state.error_message = str(exc)
            self.state.status = UpdateStatus.ERROR
            self._emit("error", exc)
            return False

    def _verify_checksum(self, path: Path, expected: str) -> bool:
        """Verify SHA-256 checksum of a downloaded file."""
        if not expected:
            return True
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        actual = h.hexdigest()
        if actual != expected:
            logger.error("Checksum mismatch: expected=%s actual=%s", expected, actual)
            return False
        logger.info("Checksum verified OK")
        return True

    def check_for_update(self) -> Optional[UpdateInfo]:
        """Poll the update server and return UpdateInfo if a newer version exists."""
        body = self._fetch(self.config.update_url)
        if body is None:
            self.state.status = UpdateStatus.ERROR
            return None
        self.state.last_check = datetime.utcnow()
        update_info: Optional[UpdateInfo] = None
        if body.lstrip().startswith(b"<"):
            update_info = self._parse_sparkle_xml(body)
        else:
            update_info = self._parse_json_feed(body)
        if update_info is None or not self._is_newer(update_info.version):
            self.state.status = UpdateStatus.UP_TO_DATE
            logger.info("No update available (current=%s)", self.config.current_version)
            return None
        self.state.available_update = update_info
        self.state.status = UpdateStatus.UPDATE_AVAILABLE
        logger.info("Update available: %s -> %s", self.config.current_version, update_info.version)
        self._emit("update_available", update_info)
        return update_info

    def download_update(self, update_info: Optional[UpdateInfo] = None) -> Optional[Path]:
        """Download the update installer and return the local path."""
        info = update_info or self.state.available_update
        if info is None:
            logger.error("No update info available to download")
            return None
        dest = self.config.download_dir / Path(urllib.parse.urlparse(info.download_url).path).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        def _progress(pct: float) -> None:
            self.state.download_progress = pct
        ok = self._download_file(info.download_url, dest, _progress)
        if not ok:
            return None
        if not self._verify_checksum(dest, info.checksum_sha256):
            dest.unlink(missing_ok=True)
            self.state.status = UpdateStatus.ERROR
            return None
        self.state.downloaded_file = dest
        self.state.status = UpdateStatus.DOWNLOADED
        logger.info("Downloaded to %s", dest)
        self._emit("download_complete", dest)
        return dest

    def prompt_restart(self) -> None:
        """Notify the user that a restart is required to apply the update."""
        info = self.state.available_update
        if info is None:
            return
        msg = (f"Update {info.version} is ready.\n"
               f"Release notes: {info.release_notes}\n"
               "Please restart the application to apply the update.")
        logger.info("PROMPT: %s", msg)
        self._emit("install_ready", info)

    def auto_install_if_critical(self) -> bool:
        """Automatically install if the update is marked critical."""
        info = self.state.available_update
        if info is None or not info.critical or not self.config.auto_install_critical:
            return False
        if self.state.downloaded_file is None:
            self.download_update(info)
        if self.state.downloaded_file is None:
            return False
        logger.info("Auto-installing critical update %s", info.version)
        self._emit("install_ready", info)
        return True

    def _daemon_loop(self) -> None:
        """Background polling loop (runs in a separate thread)."""
        interval = self.config.poll_interval_hours * 3600
        while not self._stop_event.is_set():
            try:
                self.check_for_update()
                if self.state.status == UpdateStatus.UPDATE_AVAILABLE:
                    self.download_update()
                    if self.state.status == UpdateStatus.DOWNLOADED:
                        if self.auto_install_if_critical():
                            logger.info("Critical update auto-installed")
                        else:
                            self.prompt_restart()
            except Exception as e:
                logger.debug("Daemon loop error: %s", repr(e))
                logger.exception("Daemon loop error")
            self._stop_event.wait(interval)

    def start_daemon(self) -> None:
        """Start the background update-check daemon."""
        if self._daemon_thread is not None and self._daemon_thread.is_alive():
            logger.warning("Daemon already running")
            return
        self._stop_event.clear()
        self._daemon_thread = threading.Thread(target=self._daemon_loop, name="updater-daemon", daemon=True)
        self._daemon_thread.start()
        logger.info("Updater daemon started (interval=%dh)", self.config.poll_interval_hours)

    def stop_daemon(self) -> None:
        """Signal the daemon to stop and wait for it."""
        self._stop_event.set()
        if self._daemon_thread is not None:
            self._daemon_thread.join(timeout=10)
            logger.info("Updater daemon stopped")

def _build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for the CLI."""
    parser = argparse.ArgumentParser(description="Auto-update mechanism (WinSparkle / Squirrel.Mac style)")
    parser.add_argument("--url", required=True, help="Update server URL (XML appcast or JSON feed)")
    parser.add_argument("--version", required=True, help="Current application version (e.g. 1.2.3)")
    parser.add_argument("--app-name", default="MyApp", help="Application name for User-Agent header")
    parser.add_argument("--poll-hours", type=int, default=24, help="Polling interval in hours (default: 24)")
    parser.add_argument("--auto-install-critical", action="store_true", help="Auto-install critical updates")
    parser.add_argument("--download-dir", type=Path, default=None, help="Directory for downloaded installers")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Check for available updates")
    sub.add_parser("download", help="Download the latest update")
    sub.add_parser("daemon", help="Run the background update daemon")
    return parser

def main(argv: Optional[List[str]] = None) -> int:
    """Entry point for the auto-updater CLI. Returns exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    config = AppConfig(
        app_name=args.app_name, current_version=args.version, update_url=args.url,
        poll_interval_hours=args.poll_hours, auto_install_critical=args.auto_install_critical,
        download_dir=args.download_dir,
    )
    updater = AutoUpdater(config)
    if args.command == "check":
        info = updater.check_for_update()
        if info:
            print(json.dumps(info.to_dict(), indent=2))
        else:
            print("No update available.")
        return 0
    if args.command == "download":
        info = updater.check_for_update()
        if info is None:
            print("No update available to download.")
            return 0
        path = updater.download_update(info)
        if path:
            print(f"Downloaded: {path}")
            updater.prompt_restart()
            return 0
        print("Download failed.")
        return 1
    if args.command == "daemon":
        updater.start_daemon()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            updater.stop_daemon()
            return 0
    return 1  # pragma: no cover

if __name__ == "__main__":
    sys.exit(main())

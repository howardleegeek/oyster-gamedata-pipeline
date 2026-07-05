#!/usr/bin/env python3
"""
graceful_shutdown_handler.py — G248 graceful shutdown coordinator.

Hooks OS shutdown signals (Windows SetConsoleCtrlHandler, POSIX SIGTERM/SIGINT)
and performs orderly teardown: flush in-flight clip writes, close tarballs
atomically, persist queue state, and write a restart-resume checkpoint.
"""

import argparse
import atexit
import json
import logging
import signal
import sys
import tarfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class GracefulShutdownHandler:
    def __init__(self, state_dir: Path, flush_timeout: float = 30.0):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.queue_file = self.state_dir / "queue.json"
        self.flush_timeout = flush_timeout
        
        self._shutdown = threading.Event()
        self._writes: Dict[str, Dict] = {}
        self._tarballs: Dict[str, tarfile.TarFile] = {}
        self._queue: Dict[str, Any] = {"version": 1, "items": [], "cursor": 0}
        self._lock = threading.RLock()
        
        self._load_queue()
        atexit.register(self._atexit)
        self._register_signals()
    
    def _register_signals(self) -> None:
        """Register OS signal handlers for graceful shutdown.

        On Windows, uses SetConsoleCtrlHandler to intercept Ctrl+C, Ctrl+Break,
        and other console events. On POSIX, registers SIGTERM and SIGINT handlers.
        Logs a warning if Windows API is unavailable.
        """
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes
                PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
                def handler(ctrl_type: int) -> bool:
                    """Handle Windows console control events.

                    Called by Windows kernel when a console event occurs (Ctrl+C,
                    Ctrl+Break, etc.). Maps specific event types to SIGTERM to
                    trigger graceful shutdown.

                    Args:
                        ctrl_type: Console event type code.
                            0 = CTRL_C_EVENT (Ctrl+C)
                            1 = CTRL_BREAK_EVENT (Ctrl+Break)
                            2 = CTRL_CLOSE_EVENT (console close)
                            5 = CTRL_LOGOFF_EVENT
                            6 = CTRL_SHUTDOWN_EVENT

                    Returns:
                        True if the event was handled, False otherwise.
                    """
                    if ctrl_type in (0, 1, 2, 5, 6):
                        self._handle_signal(signal.SIGTERM, None)
                        return True
                    return False
                kernel32 = ctypes.WinDLL("kernel32")
                kernel32.SetConsoleCtrlHandler(PHANDLER_ROUTINE(handler), True)
            except ImportError:
                logger.warning("Windows API unavailable")
        else:
            signal.signal(signal.SIGTERM, self._handle_signal)
            signal.signal(signal.SIGINT, self._handle_signal)
        logger.info("Signal handlers registered")
    
    def _handle_signal(self, signum: int, frame: Any) -> None:
        """Handle incoming OS signals by initiating graceful shutdown.

        Args:
            signum: Signal number (e.g., signal.SIGTERM, signal.SIGINT).
            frame: Current stack frame (unused, required by signal handler signature).
        """
        logger.info("Signal %s received", signum)
        self.shutdown()
    
    def shutdown(self) -> None:
        """Perform orderly teardown of the shutdown handler.

        Flushes in-flight writes, closes open tarballs, persists queue
        state, and writes a restart-resume checkpoint. Idempotent:
        subsequent calls after the first are no-ops. Terminates the
        process via sys.exit(0) on completion.
        """
        if self._shutdown.is_set():
            return
        self._shutdown.set()
        logger.info("Starting graceful shutdown")
        try:
            self._flush_writes()
            self._close_tarballs()
            self._save_queue()
            self._create_checkpoint()
            logger.info("Shutdown complete")
        except Exception as e:
            logger.error("Shutdown error: %s", e)
        sys.exit(0)
    
    def _flush_writes(self) -> None:
        """Flush in-flight clip writes to disk.

        Iterates through registered but incomplete writes and persists them
        to disk within the configured flush_timeout. Writes are attempted in
        order; any failures are logged but do not stop processing of remaining
        writes. Successfully written clips are marked as completed.

        The method acquires the internal lock to ensure thread-safe operation.
        """
        with self._lock:
            if not self._writes:
                return
            logger.info("Flushing %d writes", len(self._writes))
            start = time.time()
            for clip_id, write in list(self._writes.items()):
                if not write.get("completed") and (time.time() - start) < self.flush_timeout:
                    try:
                        path = Path(write["path"])
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(write["data"])
                        write["completed"] = True
                    except Exception as e:
                        logger.warning("Write failed %s: %s", clip_id, e)
    
    def _close_tarballs(self):
        with self._lock:
            if not self._tarballs:
                return
            logger.info("Closing %d tarballs", len(self._tarballs))
            for path, tar in self._tarballs.items():
                try:
                    tar.close()
                except Exception as e:
                    logger.error("Close error %s: %s", path, e)
    
    def _save_queue(self):
        with self._lock:
            try:
                temp = self.queue_file.with_suffix('.tmp')
                with open(temp, 'w') as f:
                    json.dump(self._queue, f, indent=2)
                temp.replace(self.queue_file)
                logger.info("Queue saved")
            except Exception as e:
                logger.error("Save error: %s", e)
    
    def _create_checkpoint(self):
        try:
            ts = int(time.time())
            checkpoint = self.state_dir / f"checkpoint_{ts}.json"
            with self._lock:
                data = {
                    "timestamp": ts,
                    "queue": self._queue,
                    "writes": [
                        {"id": k, "path": v["path"], "completed": v.get("completed", False)}
                        for k, v in self._writes.items()
                    ],
                    "tarballs": list(self._tarballs.keys())
                }
            with open(checkpoint, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info("Checkpoint created")
            self._cleanup_checkpoints()
        except Exception as e:
            logger.error("Checkpoint error: %s", e)
    
    def _cleanup_checkpoints(self):
        try:
            checkpoints = sorted(self.state_dir.glob("checkpoint_*.json"),
                               key=lambda p: p.stat().st_mtime)
            for old in checkpoints[:-5]:
                try:
                    old.unlink()
                except OSError:
                    pass
        except Exception:
            pass
    
    def _load_queue(self):
        try:
            if self.queue_file.exists():
                with open(self.queue_file, 'r') as f:
                    self._queue = json.load(f)
        except Exception:
            pass
    
    def _atexit(self):
        if not self._shutdown.is_set():
            self.shutdown()
    
    def is_shutting_down(self) -> bool:
        """Check whether graceful shutdown has been initiated.

        Returns:
            True if shutdown has been triggered (SIGTERM/SIGINT received),
            False otherwise.
        """
        return self._shutdown.is_set()
    
    def wait_for_shutdown(self, timeout: Optional[float] = None) -> bool:
        """Wait for shutdown signal to be triggered.

        Blocks until the shutdown event is set (signal received) or the
        optional timeout expires.

        Args:
            timeout: Maximum seconds to wait. None blocks indefinitely.

        Returns:
            True if shutdown was triggered, False if timeout expired.
        """
        return self._shutdown.wait(timeout)
    
    def register_clip_write(self, clip_id: str, file_path: Path, data: bytes) -> None:
        """Register an in-flight clip write for graceful shutdown tracking.

        Args:
            clip_id: Unique identifier for the clip being written.
            file_path: Destination file path for the clip data.
            data: Raw clip bytes to be persisted.
        """
        with self._lock:
            self._writes[clip_id] = {"path": str(file_path), "data": data, "completed": False}
    
    def mark_write_completed(self, clip_id: str) -> None:
        """Mark a registered clip write as completed.

        Updates the internal tracking dictionary to indicate that the
        in-flight write for the given clip has finished. This is used
        during graceful shutdown to determine which writes still need
        to be flushed.

        Args:
            clip_id: Unique identifier for the clip whose write is complete.
        """
        with self._lock:
            if clip_id in self._writes:
                self._writes[clip_id]["completed"] = True
    
    def open_tarball(self, path: Path, mode: str = "w:gz") -> tarfile.TarFile:
        """Open a tarball and track it for graceful shutdown.

        Opens the specified tarball file with the given mode and registers
        it with the handler so it can be properly closed during shutdown.

        Args:
            path: Path to the tarball file.
            mode: Tarfile open mode (default "w:gz" for gzip compression).

        Returns:
            The opened tarfile.TarFile object.
        """
        with self._lock:
            tar = tarfile.open(path, mode)
            self._tarballs[str(path)] = tar
            return tar
    
    def update_queue(self, items: List[Dict[str, Any]], cursor: int = 0) -> None:
        """Update the persisted queue state with new items and cursor position.

        Args:
            items: List of queue items to persist.
            cursor: Current cursor position for resume tracking. Defaults to 0.
        """
        with self._lock:
            self._queue["items"] = items
            self._queue["cursor"] = cursor


def main(argv: list[str] | None = None) -> int:
    """Run the graceful shutdown handler as a CLI application.

    Initializes a GracefulShutdownHandler with the given state directory and
    flush timeout, then optionally runs as a daemon waiting for shutdown signals.

    Args:
        argv: Command-line arguments (including program name). If None,
            uses sys.argv. Defaults to None.

    Returns:
        Exit code: 0 for normal exit, non-zero for errors.
    """
    parser = argparse.ArgumentParser(description="Graceful shutdown handler")
    parser.add_argument("--state-dir", type=Path, required=True,
                       help="Directory for state and checkpoints")
    parser.add_argument("--flush-timeout", type=float, default=30.0,
                       help="Timeout for flushing writes (seconds)")
    parser.add_argument("--daemon", action="store_true",
                       help="Run as daemon, wait for shutdown")
    parser.add_argument("--log-level", default="INFO",
                       choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                       help="Logging level")
    
    args = parser.parse_args(argv)
    
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    
    handler = GracefulShutdownHandler(
        state_dir=args.state_dir,
        flush_timeout=args.flush_timeout
    )
    
    logger.info("Graceful shutdown handler initialized")
    
    if args.daemon:
        logger.info("Running in daemon mode")
        try:
            while not handler.is_shutting_down():
                time.sleep(1)
        except KeyboardInterrupt:
            handler.shutdown()
    else:
        logger.info("Running in one-shot mode")
        handler._create_checkpoint()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
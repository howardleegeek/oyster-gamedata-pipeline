#!/usr/bin/env python3
"""
Error Client for Python - Global Error Capture and Reporting

Auto-installed Python global error capture: sys.excepthook + threading.excepthook +
asyncio default handler + atexit; non-blocking POST to G231 with retry; PII-strip helper.

Usage:
    python -m oyster_agent_runner.error_client_python [--install] [--endpoint URL]
    from oyster_agent_runner.error_client_python import install_handlers
"""

import argparse
import asyncio
import atexit
import datetime
import json
import logging
import os
import re
import sys
import threading
import traceback
import types
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__version__ = "1.0.0"
DEFAULT_ENDPOINT = "http://localhost:8081/api/v1/errors"
MAX_PAYLOAD_SIZE = 64 * 1024
MAX_RETRIES = 3
RETRY_DELAY_BASE = 0.5
REQUEST_TIMEOUT = 5.0


class PIIStripper:
    """Strip PII from tracebacks and error messages."""

    PATTERNS = [
        (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "[EMAIL]"),
        (
            re.compile(
                r'(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?token)\s*[:=]\s*["\']?([a-zA-Z0-9_\-]{16,})["\']?',
                re.IGNORECASE,
            ),
            r"\1=[REDACTED]",
        ),
        (re.compile(r"(?:AKIA|ABIA|ACCA|ASIA)[A-Z0-9]{16}"), "[AWS_KEY]"),
        (re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]+"), "Bearer [TOKEN]"),
        (re.compile(r"://[^:]+:([^@]+)@"), r"://[USER]:[PASSWORD]@"),
        (re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"), "[CARD]"),
        (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
        (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "[IP]"),
        (re.compile(r"/home/[a-zA-Z0-9_]+/"), "/home/[USER]/"),
        (re.compile(r"C:\\Users\\[a-zA-Z0-9_]+\\"), "C:\\Users\\[USER]\\"),
    ]

    @classmethod
    def strip(cls, text: str) -> str:
        """Strip PII from text."""
        if not text:
            return text
        result = text
        for pattern, replacement in cls.PATTERNS:
            result = pattern.sub(replacement, result)
        return result


class ErrorPayload:
    """Container for error data to send to error collection service."""

    def __init__(
        self,
        error_type: str,
        error_message: str,
        traceback_str: str,
        context: Optional[Dict[str, Any]] = None,
    ):
        self.error_id = str(uuid.uuid4())
        self.timestamp = datetime.datetime.utcnow().isoformat() + "Z"
        self.error_type = error_type
        self.error_message = error_message
        self.traceback_str = traceback_str
        self.context = context or {}
        self.hostname = os.environ.get("HOSTNAME") or os.environ.get("HOST") or "unknown"
        self.pid = os.getpid()
        self.python_version = sys.version

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_id": self.error_id,
            "timestamp": self.timestamp,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "traceback": self.traceback_str,
            "context": self.context,
            "hostname": self.hostname,
            "pid": self.pid,
            "python_version": self.python_version,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class ErrorReporter:
    """Handles error reporting to G231 error collection service with non-blocking operation."""

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        max_retries: int = MAX_RETRIES,
        timeout: float = REQUEST_TIMEOUT,
        max_payload_size: int = MAX_PAYLOAD_SIZE,
    ):
        self.endpoint = endpoint
        self.max_retries = max_retries
        self.timeout = timeout
        self.max_payload_size = max_payload_size
        self._pending: List[ErrorPayload] = []
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def report_error(
        self,
        error_type: str,
        error_message: str,
        traceback_str: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Queue an error for non-blocking reporting."""
        safe_msg = PIIStripper.strip(error_message)
        safe_tb = PIIStripper.strip_traceback(traceback_str)
        safe_ctx = {
            k: (PIIStripper.strip(v) if isinstance(v, str) else v)
            for k, v in (context or {}).items()
        }

        payload = ErrorPayload(error_type, safe_msg, safe_tb, safe_ctx)

        with self._lock:
            self._pending.append(payload)

        self._start_thread()

    def _start_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, daemon=True, name="ErrorReporter")
            self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            to_send = []
            with self._lock:
                if self._pending:
                    to_send = self._pending[:]
                    self._pending.clear()

            for p in to_send:
                self._send_with_retry(p)

            self._stop.wait(0.1)

    def _send_with_retry(self, payload: ErrorPayload) -> bool:
        json_data = payload.to_json()
        if len(json_data) > self.max_payload_size:
            json_data = self._truncate(json_data)

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                self._send(json_data)
                return True
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries - 1:
                    import time

                    time.sleep(RETRY_DELAY_BASE * (2**attempt))
        # All retries exhausted — log before giving up so silent failure is visible.
        logger.warning(
            "ErrorClient failed to send after %d attempts (last error: %s: %s)",
            self.max_retries,
            type(last_error).__name__ if last_error else "unknown",
            last_error,
        )
        return False

    def _send(self, json_data: str) -> None:
        data = json_data.encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"ErrorClient/{__version__}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            _ = resp.read()

    def _truncate(self, json_data: str) -> str:
        if len(json_data) <= self.max_payload_size:
            return json_data
        try:
            data = json.loads(json_data)
            tb = data.get("traceback", "")
            if len(tb) > 5000:
                data["traceback"] = tb[:5000] + "\n... [truncated]"
            return json.dumps(data, ensure_ascii=False)
        except Exception as exc:
            logger.warning(
                "Failed to parse JSON for truncation, falling back to blind slice: %s: %s",
                type(exc).__name__,
                exc,
            )
            return json_data[: self.max_payload_size] + "..."

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)


_global_reporter: Optional[ErrorReporter] = None


def _get_reporter() -> ErrorReporter:
    global _global_reporter
    if _global_reporter is None:
        _global_reporter = ErrorReporter()
    return _global_reporter


def _format_exc(exc_type: type, exc_value: BaseException, tb: types.TracebackType) -> str:
    return "".join(traceback.format_exception(exc_type, exc_value, tb))


def _sys_excepthook(
    exc_type: type, exc_value: BaseException, tb: Optional[types.TracebackType]
) -> None:
    """sys.excepthook for uncaught exceptions."""
    traceback.print_exception(exc_type, exc_value, tb, file=sys.stderr)
    tb_str = _format_exc(exc_type, exc_value, tb) if tb else ""
    _get_reporter().report_error(
        exc_type.__name__,
        str(exc_value),
        tb_str,
        {"source": "sys.excepthook", "thread": threading.current_thread().name},
    )


def _threading_excepthook(args: Any) -> None:
    """threading.excepthook for thread exceptions."""
    exc_type, exc_value, tb = args.exc_type, args.exc_value, args.exc_traceback
    if tb:
        traceback.print_exception(exc_type, exc_value, tb, file=sys.stderr)
    else:
        print(f"Thread exception: {exc_type.__name__}: {exc_value}", file=sys.stderr)
    tb_str = _format_exc(exc_type, exc_value, tb) if tb else ""
    _get_reporter().report_error(
        exc_type.__name__,
        str(exc_value),
        tb_str,
        {"source": "threading.excepthook", "thread": getattr(args, "thread_name", "unknown")},
    )


def _asyncio_handler(loop: asyncio.AbstractEventLoop, context: Dict[str, Any]) -> None:
    """asyncio default exception handler."""
    msg = context.get("message", "Unknown asyncio error")
    exc = context.get("exception")
    if exc:
        tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        err_type, err_msg = type(exc).__name__, str(exc)
    else:
        tb_str, err_type, err_msg = f"Message: {msg}", "AsyncioError", msg
    ctx = {"source": "asyncio", "loop": str(loop)}
    for k, v in context.items():
        if k not in ("message", "exception"):
            ctx[k] = str(v)
    _get_reporter().report_error(err_type, err_msg, tb_str, ctx)


def _atexit_handler() -> None:
    """atexit handler for cleanup."""
    pass


def install_handlers(endpoint: Optional[str] = None) -> None:
    """Install all global error handlers."""
    global _global_reporter
    if endpoint:
        _global_reporter = ErrorReporter(endpoint=endpoint)
    sys.excepthook = _sys_excepthook
    threading.excepthook = _threading_excepthook
    try:
        asyncio.get_event_loop().set_exception_handler(_asyncio_handler)
    except RuntimeError as exc:
        logger.debug(
            "error_client_python: no event loop running, "
            "skipping asyncio handler: %s",
            exc,
        )
    atexit.register(_atexit_handler)


def uninstall_handlers() -> None:
    """Uninstall all error handlers and shut down reporter."""
    global _global_reporter
    sys.excepthook = sys.__excepthook__
    if _global_reporter:
        _global_reporter.shutdown()
        _global_reporter = None


def main(argv: List[str]) -> int:
    """Main entry point for CLI usage."""
    parser = argparse.ArgumentParser(
        description="Python Error Client - Global error capture and reporting"
    )
    parser.add_argument("--install", action="store_true", help="Install global error handlers")
    parser.add_argument(
        "--endpoint",
        type=str,
        default=DEFAULT_ENDPOINT,
        help=f"Error collection endpoint (default: {DEFAULT_ENDPOINT})",
    )
    parser.add_argument("--version", action="store_true", help="Show version information")
    parser.add_argument(
        "--test", action="store_true", help="Test error reporting by raising a test exception"
    )
    args = parser.parse_args(argv)

    if args.version:
        print(f"Error Client Python v{__version__}")
        print(f"Default endpoint: {DEFAULT_ENDPOINT}")
        return 0

    if args.test:
        install_handlers(endpoint=args.endpoint)
        raise RuntimeError("Test exception for error client verification")

    if args.install:
        install_handlers(endpoint=args.endpoint)
        print(f"Error handlers installed. Endpoint: {args.endpoint}")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""
G236 · bin/error_client_pytest_plugin.py

pytest plugin (entry-point: pytest11) that auto-reports test failures and
tracebacks to the G231 error-tracking service.  Includes source=test,
ci_run_id, and commit_sha in every report.  Opt-out via the
OYSTER_NO_ERROR_REPORT environment variable.

Environment variables
---------------------
    OYSTER_NO_ERROR_REPORT   Set to any truthy value to disable reporting.
    G231_ENDPOINT            URL of the G231 error-tracking endpoint.
    CI_RUN_ID                CI pipeline run identifier.
    COMMIT_SHA               Git commit SHA.
    SOURCE                   Source identifier (default: "test").

Entry-point registration (setup.cfg / pyproject.toml)
------------------------------------------------------
    [options.entry_points]
    pytest11 =
        error_client = bin.error_client_pytest_plugin

Usage
-----
    pytest                          # reporting enabled by default
    OYSTER_NO_ERROR_REPORT=1 pytest # opt-out
    pytest --no-error-report        # opt-out via CLI flag
"""

import os
import sys
import json
import hashlib
import argparse
import traceback
from typing import Optional, Dict, Any, List

# ---------------------------------------------------------------------------
# stdlib-only HTTP client (no requests dependency)
# ---------------------------------------------------------------------------
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


# ── helpers ────────────────────────────────────────────────────────────────

def _should_report() -> bool:
    """Return *True* when error reporting is **not** opted out."""
    no_report = os.environ.get("OYSTER_NO_ERROR_REPORT", "").strip().lower()
    return no_report not in ("1", "true", "yes", "on")


def _get_ci_run_id() -> Optional[str]:
    """Resolve CI run ID from common CI environment variables."""
    return (
        os.environ.get("CI_RUN_ID")
        or os.environ.get("GITHUB_RUN_ID")
        or os.environ.get("GITLAB_CI_JOB_ID")
        or os.environ.get("BUILD_ID")
    )


def _get_commit_sha() -> Optional[str]:
    """Resolve commit SHA from common CI environment variables."""
    return (
        os.environ.get("COMMIT_SHA")
        or os.environ.get("GITHUB_SHA")
        or os.environ.get("CI_COMMIT_SHA")
        or os.environ.get("GIT_COMMIT")
    )


def _get_endpoint() -> str:
    """Return the G231 endpoint URL (falls back to localhost)."""
    return os.environ.get("G231_ENDPOINT", "http://localhost:8080/api/errors")


def _build_payload(
    test_name: str,
    traceback_str: str,
    exception_type: str,
    exception_msg: str,
    file_path: str,
    line_number: int,
) -> Dict[str, Any]:
    """Construct the JSON-serialisable error-report payload."""
    return {
        "source": os.environ.get("SOURCE", "test"),
        "ci_run_id": _get_ci_run_id(),
        "commit_sha": _get_commit_sha(),
        "test_name": test_name,
        "exception_type": exception_type,
        "exception_message": exception_msg,
        "traceback": traceback_str,
        "file_path": file_path,
        "line_number": line_number,
        "fingerprint": hashlib.sha256(
            f"{exception_type}:{exception_msg}:{file_path}".encode()
        ).hexdigest(),
    }


def _send_report(payload: Dict[str, Any]) -> bool:
    """POST *payload* to the G231 endpoint.  Returns *True* on HTTP 2xx."""
    endpoint = _get_endpoint()
    try:
        data = json.dumps(payload).encode("utf-8")
        req = Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except (URLError, HTTPError, OSError) as exc:
        print(
            f"[error_client] Failed to send report to {endpoint}: {exc}",
            file=sys.stderr,
        )
        return False


# ── pytest hooks ───────────────────────────────────────────────────────────

def pytest_addoption(parser: Any) -> None:
    """Register CLI flags for the plugin."""
    group = parser.getgroup("error_client", "G231 error reporting")
    group.addoption(
        "--no-error-report",
        action="store_true",
        default=False,
        help="Disable G231 error reporting on test failures",
    )


def pytest_configure(config: Any) -> None:
    """Register custom markers and store plugin state on *config*."""
    config.addinivalue_line(
        "markers",
        "no_error_report: skip G231 error reporting for this test",
    )


def pytest_runtest_makereport(
    item: Any, call: Any
) -> Optional[Any]:
    """
    Hook invoked after each test phase.

    When a test **fails** during the ``call`` phase, collect the exception
    details and traceback, then POST them to G231.
    """
    # Global opt-out
    if not _should_report():
        return None

    # CLI opt-out
    if hasattr(item.config, "getoption"):
        if item.config.getoption("--no-error-report", default=False):
            return None

    # Only care about the actual test execution (not setup/teardown)
    if call.when != "call":
        return None

    # No exception → nothing to report
    if call.excinfo is None:
        return None

    # Per-test marker opt-out
    if item.get_closest_marker("no_error_report"):
        return None

    # Extract exception info
    exc_type = call.excinfo.type
    exc_value = call.excinfo.value
    exc_tb = call.excinfo.tb

    tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
    tb_str = "".join(tb_lines)

    # Source location from pytest item
    file_path = item.location[0] if item.location else "unknown"
    line_number = item.location[1] if item.location else 0

    payload = _build_payload(
        test_name=item.nodeid,
        traceback_str=tb_str,
        exception_type=exc_type.__name__,
        exception_msg=str(exc_value),
        file_path=file_path,
        line_number=line_number,
    )

    _send_report(payload)
    return None


# ── standalone CLI ─────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    """
    CLI entry-point for sending a test-failure report manually.

    Useful for debugging the plugin or integrating with non-pytest runners.
    """
    parser = argparse.ArgumentParser(
        description="G231 error client – send test-failure reports",
    )
    parser.add_argument(
        "--test-name", required=True, help="Fully-qualified test name"
    )
    parser.add_argument(
        "--exception-type", default="Exception", help="Exception class name"
    )
    parser.add_argument(
        "--exception-msg", default="", help="Exception message text"
    )
    parser.add_argument(
        "--traceback", default="", help="Full traceback string"
    )
    parser.add_argument(
        "--file-path", default="", help="Source file path"
    )
    parser.add_argument(
        "--line-number", type=int, default=0, help="Source line number"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print JSON payload to stdout without sending",
    )

    args = parser.parse_args(argv)

    payload = _build_payload(
        test_name=args.test_name,
        traceback_str=args.traceback,
        exception_type=args.exception_type,
        exception_msg=args.exception_msg,
        file_path=args.file_path,
        line_number=args.line_number,
    )

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    success = _send_report(payload)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())

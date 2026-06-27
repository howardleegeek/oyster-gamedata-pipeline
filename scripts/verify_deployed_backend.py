#!/usr/bin/env python3
"""verify_deployed_backend.py – Smoke-test a deployed backend_stub instance.

Usage:
    python scripts/verify_deployed_backend.py --url https://oyster-backend-6qup7rrx2q-uc.a.run.app
    python scripts/verify_deployed_backend.py --url https://oyster-backend-6qup7rrx2q-uc.a.run.app --verbose

Calls four required endpoints and optionally one admin endpoint:
  1. GET  /healthz                        → 200 + {"status": "ok"}
  2. POST /api/v1/testers/apply           → 200 + tester_id
  3. GET  /api/v1/income/today (Bearer)   → 200 + JSON schema valid
  4. GET  /api/v1/updates/appcast.xml     → 200 + valid XML
  5. GET  /api/v1/admin/state             → optional, token read from env only

Exit 0 if all pass, 1 if any fail with details.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

UNRESOLVED_APPCAST_MARKERS = ("PLACE" + "HOLDER",)
STUB_PROVIDER_VALUES = {
    "",
    "dev",
    "demo",
    "fake",
    "local",
    "mock",
    "none",
    "sim",
    "simulator",
    "stub",
    "test",
}

# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class SmokeReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(CheckResult(name=name, passed=passed, detail=detail))

    def summary(self) -> str:
        lines: list[str] = []
        for c in self.checks:
            status = "PASS" if c.passed else "FAIL"
            line = f"  [{status}] {c.name}"
            if c.detail:
                line += f" – {c.detail}"
            lines.append(line)
        total = len(self.checks)
        ok = sum(1 for c in self.checks if c.passed)
        lines.append(f"\n  {ok}/{total} checks passed")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _normalise_provider(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def check_healthz(
    client: httpx.Client,
    verbose: bool,
    expected_backend_mode: str | None = None,
    require_real_providers: bool = False,
) -> CheckResult:
    """GET /healthz → 200 + {"status": "ok"}"""
    if verbose:
        print("  → GET /healthz")
    try:
        resp = client.get("/healthz")
        if resp.status_code == 404:
            # Cloud Run's Google frontend intercepts /healthz on *.run.app
            # and answers its own 404 before the container sees the request.
            # Fall back to the unreserved alias serving the same rich body.
            # Other failure codes (e.g. 500) are real backend answers and
            # must surface immediately.
            if verbose:
                print("  → GET /api/v1/healthz (fallback)")
            resp = client.get("/api/v1/healthz")
        if resp.status_code != 200:
            return CheckResult("GET /healthz", False, f"status={resp.status_code}")
        body = resp.json()
        if body.get("status") != "ok":
            return CheckResult("GET /healthz", False, f"unexpected body: {json.dumps(body)}")
        if expected_backend_mode:
            actual_mode = str(body.get("mode", ""))
            if actual_mode != expected_backend_mode:
                return CheckResult(
                    "GET /healthz",
                    False,
                    f"expected backend mode {expected_backend_mode}, got {actual_mode or '<missing>'}",
                )
        if require_real_providers:
            providers = body.get("providers")
            if not isinstance(providers, dict):
                return CheckResult("GET /healthz", False, "missing providers object")
            stubbed = [
                f"{name}={value}"
                for name, value in providers.items()
                if _normalise_provider(str(value)) in STUB_PROVIDER_VALUES
            ]
            if stubbed:
                return CheckResult(
                    "GET /healthz",
                    False,
                    "stub providers: " + ", ".join(stubbed),
                )
        return CheckResult("GET /healthz", True)
    except Exception as exc:
        return CheckResult("GET /healthz", False, str(exc))


def check_testers_apply(client: httpx.Client, verbose: bool) -> CheckResult:
    """POST /api/v1/testers/apply with the real public schema → 200 + tester_id"""
    if verbose:
        print("  → POST /api/v1/testers/apply")
    try:
        resp = client.post(
            "/api/v1/testers/apply",
            json={
                "email": "smoke@test.com",
                "discord_user": "smoke#0000",
                "why_interested": "deployment smoke test",
            },
        )
        if resp.status_code != 200:
            return CheckResult(
                "POST /api/v1/testers/apply",
                False,
                f"status={resp.status_code}",
            )
        body = resp.json()
        tester_id = body.get("tester_id")
        if not tester_id:
            return CheckResult(
                "POST /api/v1/testers/apply",
                False,
                f"missing tester_id in response: {json.dumps(body)}",
            )
        return CheckResult("POST /api/v1/testers/apply", True)
    except Exception as exc:
        return CheckResult("POST /api/v1/testers/apply", False, str(exc))


def check_income_today(client: httpx.Client, verbose: bool) -> CheckResult:
    """GET /api/v1/income/today (Bearer mock) → 200 + JSON schema valid"""
    if verbose:
        print("  → GET /api/v1/income/today (Bearer mock)")
    try:
        resp = client.get(
            "/api/v1/income/today",
            headers={"Authorization": "Bearer mock"},
        )
        if resp.status_code != 200:
            return CheckResult(
                "GET /api/v1/income/today",
                False,
                f"status={resp.status_code}",
            )
        body = resp.json()
        required_keys = {"date", "total_usd", "sessions_uploaded", "currency"}
        missing = required_keys - set(body.keys())
        if missing:
            return CheckResult(
                "GET /api/v1/income/today",
                False,
                f"missing keys: {missing}",
            )
        return CheckResult("GET /api/v1/income/today", True)
    except Exception as exc:
        return CheckResult("GET /api/v1/income/today", False, str(exc))


def check_appcast(
    client: httpx.Client,
    verbose: bool,
    expected_recorder_tag: str | None = None,
) -> CheckResult:
    """GET /api/v1/updates/appcast.xml → 200 + valid release enclosure XML"""
    if verbose:
        print("  → GET /api/v1/updates/appcast.xml")
    try:
        resp = client.get("/api/v1/updates/appcast.xml")
        if resp.status_code != 200:
            return CheckResult(
                "GET /api/v1/updates/appcast.xml",
                False,
                f"status={resp.status_code}",
            )
        root = ET.fromstring(resp.text)
        enclosure = root.find(".//enclosure")
        if enclosure is None:
            return CheckResult(
                "GET /api/v1/updates/appcast.xml",
                False,
                "missing enclosure",
            )
        url = enclosure.attrib.get("url", "")
        version = _xml_attr(enclosure, "version")
        sha256 = _xml_attr(enclosure, "sha256")
        if any(marker in resp.text for marker in UNRESOLVED_APPCAST_MARKERS):
            return CheckResult(
                "GET /api/v1/updates/appcast.xml",
                False,
                "contains unresolved metadata",
            )
        if not url.startswith(
            "https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download/"
        ):
            return CheckResult(
                "GET /api/v1/updates/appcast.xml",
                False,
                f"unexpected enclosure URL: {url}",
            )
        if expected_recorder_tag:
            tag = _normalise_release_tag(expected_recorder_tag)
            if f"/releases/download/{tag}/" not in url:
                return CheckResult(
                    "GET /api/v1/updates/appcast.xml",
                    False,
                    f"expected {tag} release URL, got: {url}",
                )
            if version.removeprefix("v") != _version_from_tag(tag):
                return CheckResult(
                    "GET /api/v1/updates/appcast.xml",
                    False,
                    f"expected version {tag}, got: {version}",
                )
        if not version:
            return CheckResult(
                "GET /api/v1/updates/appcast.xml",
                False,
                "missing release version",
            )
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            return CheckResult(
                "GET /api/v1/updates/appcast.xml",
                False,
                "missing or invalid sha256",
            )
        return CheckResult("GET /api/v1/updates/appcast.xml", True)
    except ET.ParseError as exc:
        return CheckResult("GET /api/v1/updates/appcast.xml", False, f"XML parse error: {exc}")
    except Exception as exc:
        return CheckResult("GET /api/v1/updates/appcast.xml", False, str(exc))


def check_appcast_with_retry(
    client: httpx.Client,
    verbose: bool,
    expected_recorder_tag: str | None = None,
    retry_seconds: float = 0.0,
    retry_interval_seconds: float = 5.0,
) -> CheckResult:
    """Check appcast, allowing short release/appcast sync races to settle."""
    deadline = time.monotonic() + max(0.0, retry_seconds)
    result = check_appcast(client, verbose, expected_recorder_tag)
    while (
        not result.passed
        and expected_recorder_tag
        and retry_seconds > 0
        and time.monotonic() < deadline
    ):
        if verbose:
            print(
                f"  → appcast not yet at expected release; retrying in {retry_interval_seconds:g}s"
            )
        time.sleep(max(0.0, retry_interval_seconds))
        result = check_appcast(client, verbose, expected_recorder_tag)
    return result


def check_admin_state(
    client: httpx.Client,
    verbose: bool,
    admin_token: str,
    expected_recorder_tag: str | None = None,
) -> CheckResult:
    """GET /api/v1/admin/state → 200 + non-PII state summary."""
    name = "GET /api/v1/admin/state"
    if verbose:
        print("  → GET /api/v1/admin/state (admin token from env)")
    try:
        resp = client.get(
            "/api/v1/admin/state",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        if resp.status_code != 200:
            return CheckResult(name, False, f"status={resp.status_code}")

        body = resp.json()
        if not isinstance(body, dict):
            return CheckResult(name, False, "response is not a JSON object")
        if body.get("status") != "ok":
            return CheckResult(name, False, "status field is not ok")

        required_keys = {"counts", "income_today", "recorder_release"}
        missing = sorted(required_keys - set(body))
        if missing:
            return CheckResult(name, False, f"missing keys: {missing}")

        serialized = json.dumps(body, sort_keys=True, default=str)
        if "@" in serialized or "download_url" in serialized:
            return CheckResult(name, False, "response contains PII marker")

        recorder_release = body.get("recorder_release")
        if not isinstance(recorder_release, dict):
            return CheckResult(name, False, "recorder_release is not an object")
        if expected_recorder_tag:
            expected_tag = _normalise_release_tag(expected_recorder_tag)
            actual_tag = str(recorder_release.get("tag", ""))
            if _normalise_release_tag(actual_tag) != expected_tag:
                return CheckResult(
                    name,
                    False,
                    f"expected recorder tag {expected_tag}, got: {actual_tag or '<missing>'}",
                )

        return CheckResult(name, True)
    except Exception as exc:
        return CheckResult(name, False, _mask_secret(str(exc), admin_token))


def _xml_attr(element: ET.Element, local_name: str) -> str:
    for key, value in element.attrib.items():
        if key == local_name or key.endswith(f"}}{local_name}"):
            return value
    return ""


def _version_from_tag(tag: str) -> str:
    """Bare semver from either consumer scheme (recorder-v2.6.15 -> 2.6.15)."""
    return tag.strip().removeprefix("recorder-").removeprefix("v")


def _normalise_release_tag(tag: str) -> str:
    """Both consumer schemes pass through: v0.16.0 and recorder-v2.6.15."""
    tag = tag.strip()
    if tag.startswith(("v", "recorder-v")):
        return tag
    return f"v{tag}"


def _mask_secret(text: str, secret: str) -> str:
    if not secret:
        return text
    return text.replace(secret, "<redacted>")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(
    url: str,
    verbose: bool = False,
    expected_recorder_tag: str | None = None,
    admin_token_env: str | None = None,
    appcast_retry_seconds: float = 0.0,
    appcast_retry_interval: float = 5.0,
    expected_backend_mode: str | None = None,
    require_real_providers: bool = False,
) -> int:
    """Run all smoke checks against *url*. Returns exit code."""
    report = SmokeReport()

    if verbose:
        print(f"Smoke-testing {url}\n")

    with httpx.Client(base_url=url.rstrip("/"), timeout=15) as client:
        report.add(
            *_unwrap(
                check_healthz(
                    client,
                    verbose,
                    expected_backend_mode=expected_backend_mode,
                    require_real_providers=require_real_providers,
                )
            )
        )
        report.add(*_unwrap(check_testers_apply(client, verbose)))
        report.add(*_unwrap(check_income_today(client, verbose)))
        report.add(
            *_unwrap(
                check_appcast_with_retry(
                    client,
                    verbose,
                    expected_recorder_tag,
                    retry_seconds=appcast_retry_seconds,
                    retry_interval_seconds=appcast_retry_interval,
                )
            )
        )
        if admin_token_env:
            admin_token = os.getenv(admin_token_env, "").strip()
            if not admin_token:
                report.add(
                    "GET /api/v1/admin/state",
                    False,
                    f"admin token env {admin_token_env} is not set",
                )
            else:
                report.add(
                    *_unwrap(
                        check_admin_state(
                            client,
                            verbose,
                            admin_token,
                            expected_recorder_tag,
                        )
                    )
                )

    print(report.summary())

    if report.all_passed:
        print("\nAll smoke checks passed.")
        return 0
    else:
        print("\nSome smoke checks FAILED.", file=sys.stderr)
        return 1


def _unwrap(result: CheckResult) -> tuple[str, bool, str]:
    return result.name, result.passed, result.detail


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test a deployed backend_stub instance.")
    parser.add_argument(
        "--url",
        required=True,
        help="Base URL of the deployed backend (e.g. https://oyster-backend-6qup7rrx2q-uc.a.run.app)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show each step as it runs.",
    )
    parser.add_argument(
        "--expected-recorder-tag",
        default=os.getenv("EXPECTED_RECORDER_RELEASE_TAG"),
        help="Require appcast.xml to point at this GitHub release tag.",
    )
    parser.add_argument(
        "--admin-token-env",
        default=os.getenv("BACKEND_ADMIN_TOKEN_ENV", ""),
        help="Optional env var name containing the admin token for /api/v1/admin/state.",
    )
    parser.add_argument(
        "--appcast-retry-seconds",
        type=float,
        default=float(os.getenv("APPCAST_RETRY_SECONDS", "0")),
        help="Retry appcast tag mismatch for this many seconds before failing.",
    )
    parser.add_argument(
        "--appcast-retry-interval",
        type=float,
        default=float(os.getenv("APPCAST_RETRY_INTERVAL_SECONDS", "5")),
        help="Seconds to wait between appcast retry attempts.",
    )
    parser.add_argument(
        "--expected-backend-mode",
        default=os.getenv("EXPECTED_BACKEND_MODE", ""),
        help="Optional backend /healthz mode value to require, e.g. production.",
    )
    parser.add_argument(
        "--require-real-providers",
        action="store_true",
        default=os.getenv("REQUIRE_REAL_PROVIDERS", "").lower() == "true",
        help="Fail if /healthz reports mock/local/simulator providers.",
    )
    args = parser.parse_args()
    sys.exit(
        run(
            args.url,
            args.verbose,
            args.expected_recorder_tag,
            args.admin_token_env,
            args.appcast_retry_seconds,
            args.appcast_retry_interval,
            args.expected_backend_mode or None,
            args.require_real_providers,
        )
    )


if __name__ == "__main__":
    main()

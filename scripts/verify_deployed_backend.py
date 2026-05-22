#!/usr/bin/env python3
"""verify_deployed_backend.py – Smoke-test a deployed backend_stub instance.

Usage:
    python scripts/verify_deployed_backend.py --url https://oyster-backend-stub.fly.dev
    python scripts/verify_deployed_backend.py --url https://oyster-backend-stub.fly.dev --verbose

Calls four endpoints and validates responses:
  1. GET  /healthz                        → 200 + {"status": "ok"}
  2. POST /api/v1/testers/apply           → 200 + tester_id
  3. GET  /api/v1/income/today (Bearer)   → 200 + JSON schema valid
  4. GET  /api/v1/updates/appcast.xml     → 200 + valid XML

Exit 0 if all pass, 1 if any fail with details.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

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


def check_healthz(client: httpx.Client, verbose: bool) -> CheckResult:
    """GET /healthz → 200 + {"status": "ok"}"""
    if verbose:
        print("  → GET /healthz")
    try:
        resp = client.get("/healthz")
        if resp.status_code != 200:
            return CheckResult("GET /healthz", False, f"status={resp.status_code}")
        body = resp.json()
        if body.get("status") != "ok":
            return CheckResult("GET /healthz", False, f"unexpected body: {json.dumps(body)}")
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


def check_appcast(client: httpx.Client, verbose: bool) -> CheckResult:
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
        if "PLACEHOLDER" in resp.text:
            return CheckResult(
                "GET /api/v1/updates/appcast.xml",
                False,
                "contains placeholder metadata",
            )
        if not url.startswith(
            "https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download/"
        ):
            return CheckResult(
                "GET /api/v1/updates/appcast.xml",
                False,
                f"unexpected enclosure URL: {url}",
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


def _xml_attr(element: ET.Element, local_name: str) -> str:
    for key, value in element.attrib.items():
        if key == local_name or key.endswith(f"}}{local_name}"):
            return value
    return ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(url: str, verbose: bool = False) -> int:
    """Run all smoke checks against *url*. Returns exit code."""
    report = SmokeReport()

    if verbose:
        print(f"Smoke-testing {url}\n")

    with httpx.Client(base_url=url.rstrip("/"), timeout=15) as client:
        report.add(*_unwrap(check_healthz(client, verbose)))
        report.add(*_unwrap(check_testers_apply(client, verbose)))
        report.add(*_unwrap(check_income_today(client, verbose)))
        report.add(*_unwrap(check_appcast(client, verbose)))

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
        help="Base URL of the deployed backend (e.g. https://oyster-backend-stub.fly.dev)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show each step as it runs.",
    )
    args = parser.parse_args()
    sys.exit(run(args.url, args.verbose))


if __name__ == "__main__":
    main()

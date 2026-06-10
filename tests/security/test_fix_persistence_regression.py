"""Regression guard — assert the top-3 security fixes remain in place.

This complements the audit reproducer tests (test_finding_01/02/08) by reading
the actual source files at HEAD and asserting the FIX patterns are present.
If a future PR accidentally regresses one of these fixes, this test fails
in CI — which is exactly the protection the audit asked for.

References:
  - SECURITY_AUDIT_2026_05_13.md, findings #01, #02, #03
  - PR: 🚨 fix(sec): Top-3 critical + high security findings
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


class Fix01StripeReturnRouteTest(unittest.TestCase):
    """Finding #01 — Stripe Connect return must NOT trust ?account=."""

    PATH = "web-tester/app/api/stripe/connect/return/route.ts"

    def test_does_not_read_account_query_param(self) -> None:
        src = _read(self.PATH)
        # The vulnerable code read `searchParams.get('account')` — that
        # call must not be present in the fix.
        self.assertNotIn(
            "searchParams.get('account')",
            src,
            "Regression: /api/stripe/connect/return now reads ?account= "
            "from the query string. This re-introduces Finding #01 "
            "(CVSS 9.6 funds-theft) — see SECURITY_AUDIT_2026_05_13.md.",
        )

    def test_account_id_sourced_from_db_only(self) -> None:
        src = _read(self.PATH)
        # The fix sources accountId ONLY from tester?.stripe_account_id.
        self.assertIn("tester?.stripe_account_id", src)
        # And it must NOT overwrite stripe_account_id on /return (only the
        # _enabled flags get updated; the account id was set by /onboard).
        # Find the update block and assert stripe_account_id is not in it.
        m = re.search(
            r"\.from\('testers'\)\s*\.update\(\{([^}]*)\}\)",
            src,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(m, "could not find testers.update(...) block")
        update_body = m.group(1)
        self.assertNotIn(
            "stripe_account_id:",
            update_body,
            "Regression: /api/stripe/connect/return is writing back "
            "stripe_account_id. The /return endpoint must never mutate "
            "this field — it is set once by /onboard.",
        )


class Fix02ServiceRoleSafeEqualTest(unittest.TestCase):
    """Finding #02 — service-role admin compare must use constant-time eq."""

    STATS_PATH = "web-tester/app/api/stats/[testerId]/route.ts"
    DOWNLOADS_PATH = "web-buyer/app/api/downloads/[purchaseId]/route.ts"
    HELPER_TESTER = "web-tester/lib/safe-equal.ts"
    HELPER_BUYER = "web-buyer/lib/safe-equal.ts"

    def test_safe_equal_helper_exists_in_both_portals(self) -> None:
        for rel in [self.HELPER_TESTER, self.HELPER_BUYER]:
            src = _read(rel)
            self.assertIn("crypto.timingSafeEqual", src, f"{rel} missing timingSafeEqual")
            self.assertIn("export function safeEqual", src, f"{rel} missing exported safeEqual")

    def test_stats_route_uses_safe_equal_not_strict_equality(self) -> None:
        src = _read(self.STATS_PATH)
        # Must import the helper.
        self.assertIn("import { safeEqual }", src)
        # Must NOT use === against SUPABASE_SERVICE_ROLE_KEY.
        self.assertNotRegex(
            src,
            r"===\s*process\.env\.SUPABASE_SERVICE_ROLE_KEY",
            "Regression: /api/stats uses non-constant-time === for "
            "service-role compare. Re-introduces Finding #02 timing oracle.",
        )
        self.assertIn("safeEqual(adminHeader", src)

    def test_downloads_route_uses_safe_equal_not_strict_equality(self) -> None:
        src = _read(self.DOWNLOADS_PATH)
        self.assertIn("import { safeEqual }", src)
        self.assertNotRegex(
            src,
            r"===\s*env\.supabaseServiceRoleKey",
            "Regression: /api/downloads uses non-constant-time === for "
            "service-role compare. Re-introduces Finding #02 timing oracle.",
        )
        self.assertIn("safeEqual(adminHeader", src)


class Fix03XffRateLimitTest(unittest.TestCase):
    """Finding #03 — rate-limit must prefer non-spoofable Vercel header."""

    PATH = "web-tester/lib/rate-limit.ts"

    def test_reads_vercel_forwarded_for_first(self) -> None:
        src = _read(self.PATH)
        # The fix reads x-vercel-forwarded-for first (it's signed by Vercel
        # edge and not spoofable from clients).
        self.assertIn(
            "x-vercel-forwarded-for",
            src,
            "Regression: rate-limit no longer reads the non-spoofable "
            "x-vercel-forwarded-for header first. Re-introduces Finding #03.",
        )
        # And: the position of x-vercel-forwarded-for must appear BEFORE
        # any x-forwarded-for reference in the function body — Python
        # readers don't have ordering guarantees, but TypeScript source
        # order matters for the early-return semantics.
        vercel_idx = src.find("x-vercel-forwarded-for")
        xff_idx = src.find("x-forwarded-for")
        self.assertGreaterEqual(vercel_idx, 0)
        self.assertGreaterEqual(xff_idx, 0)
        self.assertLess(
            vercel_idx,
            xff_idx,
            "x-vercel-forwarded-for must be checked BEFORE x-forwarded-for; "
            "otherwise the attacker-controlled header wins.",
        )

    def test_xff_fallback_uses_rightmost_not_leftmost(self) -> None:
        """When falling back to XFF, take the rightmost (trusted) IP.

        The leftmost element of x-forwarded-for is whatever the CLIENT
        prepended; the rightmost is the address the LAST trusted proxy
        appended. Taking the rightmost makes XFF parsing safe even when
        x-vercel-forwarded-for is missing (e.g. local dev / non-Vercel).
        """
        src = _read(self.PATH)
        # The old vulnerable code was `xff.split(',')[0]`. That must be
        # gone; the fix uses `parts[parts.length - 1]` or equivalent.
        self.assertNotRegex(
            src,
            r"xff\.split\(','\)\[0\]",
            "Regression: rate-limit falls back to leftmost XFF element, "
            "which is attacker-controlled. Use parts[parts.length - 1].",
        )


if __name__ == "__main__":
    unittest.main()

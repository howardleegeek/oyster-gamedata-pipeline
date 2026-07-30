"""Finding #06 — No per-tester storage quota → disk-fill DoS via legit recorder.

File: web-tester/app/api/upload-tarball/route.ts lines 175-180, 200-215

Threat model
============
Each upload is capped at 1 GiB (line 45) and rate-limited at 30 req/hr per
tester (line 53). But there's NO cumulative storage quota per tester. With
30 uploads/hr × 1 GiB each × 24 hr = 720 GiB/day per tester. With 10 testers
that's 7.2 TiB/day filling the Supabase `tarballs` bucket. Supabase pricing
is ~$25/TB/month → ~$5,400/month/test-account in storage costs.

Worse: the rate-limit window is per-instance (in-memory Map, rate-limit.ts
lines 30-31). The comment "module-level state — survives across requests
within one serverless instance" means each cold-start Vercel instance
gets its OWN bucket. With Vercel's elastic scaling spinning up dozens
of instances, the EFFECTIVE limit is ~N×instance-count × 30/hr.

Severity: MEDIUM (CVSS 6.5 — storage billing DoS; potentially HIGH
post-launch if Stripe Connect onboarding allows ANY signed-up tester
to begin uploading immediately with no D5 gating).

Reference: rate-limit.ts line 6-9 documents the per-instance soft-limit
as "acceptable", but that's only true if a global storage quota exists
as the second line of defence. There isn't one.

Repro
=====
Show that 720 GiB/day is realistically reachable for one tester within
the documented rate-limit ceiling.
"""

from __future__ import annotations

import unittest


def daily_uploads_per_tester(
    *,
    rl_per_tester_limit: int = 30,
    rl_per_tester_window_ms: int = 60 * 60_000,
    max_bytes: int = 1024 * 1024 * 1024,
    vercel_instance_multiplier: int = 1,
) -> int:
    """Theoretical daily byte-volume one tester can push."""
    seconds_per_window = rl_per_tester_window_ms / 1000
    windows_per_day = 86400 / seconds_per_window
    return int(rl_per_tester_limit * windows_per_day * max_bytes * vercel_instance_multiplier)


class DiskFillDosTest(unittest.TestCase):
    def test_single_tester_can_push_720gib_per_day(self) -> None:
        gib = 1024**3
        per_day = daily_uploads_per_tester()
        # 30 * 24 * 1 GiB = 720 GiB/day
        self.assertGreaterEqual(per_day, 720 * gib)
        self.assertLessEqual(per_day, 730 * gib)

    def test_vercel_horizontal_scaling_multiplies_the_limit(self) -> None:
        """With N hot instances each having an independent in-memory bucket,
        the effective limit is N× the documented one.
        """
        gib = 1024**3
        single = daily_uploads_per_tester()
        many = daily_uploads_per_tester(vercel_instance_multiplier=8)
        self.assertEqual(many, single * 8)
        # 5.76 TiB/day if 8 instances are hot — single tester.
        self.assertGreaterEqual(many, 5 * 1024 * gib)

    def test_recommended_fix_global_quota(self) -> None:
        """Fix: enforce a daily and cumulative storage quota at the DB.

        Suggested implementation:
          1. Add `testers.storage_quota_bytes_per_day` and
             `testers.storage_used_bytes` columns.
          2. In /api/upload-tarball, after computing `file.size`, check
             `storage_used_bytes + file.size <= storage_quota_bytes_per_day`
             via a SELECT FOR UPDATE inside the same transaction as the
             tarballs insert. Return 429 if over.
          3. Add a daily cron to reset storage_used_bytes — or compute
             quota usage from `tarballs` rows in the trailing 24h.
        Skeleton:
        """

        def upload_allowed(current_used: int, new_size: int, quota_per_day: int) -> bool:
            return current_used + new_size <= quota_per_day

        gib = 1024**3
        quota = 50 * gib  # 50 GiB/day generous ceiling
        # 49 GiB used, 1 GiB request — allowed
        self.assertTrue(upload_allowed(49 * gib, 1 * gib, quota))
        # 50 GiB used, 1 GiB request — denied
        self.assertFalse(upload_allowed(50 * gib, 1 * gib, quota))


if __name__ == "__main__":
    unittest.main()

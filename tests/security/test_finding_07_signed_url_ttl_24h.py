"""Finding #07 — 24-hour signed download URLs survive license revocation.

Files:
  - web-buyer/lib/env.ts line 32 (`downloadLinkTtlSeconds: ... '86400'`)
  - web-buyer/app/api/downloads/[purchaseId]/route.ts lines 178-183

Threat model
============
Supabase signed URLs are minted with a 24-hour TTL (`86400` seconds default).
Implications:

1. If a license is later revoked (chargeback, fraud, DMCA takedown,
   buyer-policy violation), the buyer can still download the tarball
   for up to 24 hours via the cached URL.
2. If a buyer's account is compromised between minting and TTL, the
   attacker has 24 hours to scrape every signed URL the legit buyer ever
   loaded.
3. The signed URL contains no buyer identity — once leaked (e.g. shared
   on Reddit, posted in a Discord, pasted into Slack), ANYONE can pull
   the tarball. No auth on the underlying CDN GET.
4. Signed URLs do not appear in the audit log of /api/downloads — only
   the mint is logged. So forensic IR can't tell which buyer leaked.

Severity: MEDIUM (CVSS 5.4 — extended access window, no revocation,
no audit trail on download). For a buyer marketplace where tarballs are
the *product*, this directly impacts piracy posture.

Recommended remediations
========================
  - Drop TTL to 1 hour (default Supabase docs recommend ≤ 1h).
  - Stream the file through a custom route handler instead of redirecting,
    so revocation + audit work.
  - Or: bind the URL to the buyer's IP (Supabase allows custom claims).

Repro
=====
Demonstrate that the signed URL survives a license revocation that
happens after mint but before TTL expiry.
"""

from __future__ import annotations

import time
import unittest


class _FakeSignedUrl:
    """Models a Supabase signed URL with embedded expiry."""

    def __init__(self, path: str, expires_at_unix: int) -> None:
        self.path = path
        self.expires_at_unix = expires_at_unix

    def is_valid(self, *, now: int) -> bool:
        return now < self.expires_at_unix


def mint_signed_url(path: str, ttl_seconds: int = 86400, *, now: int) -> _FakeSignedUrl:
    return _FakeSignedUrl(path, expires_at_unix=now + ttl_seconds)


class SignedUrlTtlTest(unittest.TestCase):
    def test_signed_url_survives_license_revocation(self) -> None:
        """At t=0, buyer downloads. At t=1h, ops revokes the license.
        At t=23h, the leaked URL still works — Supabase doesn't know about
        the revocation."""
        now0 = int(time.time())
        url = mint_signed_url("tarballs/A/sha.tar.gz", now=now0)
        # 23 hours later
        self.assertTrue(url.is_valid(now=now0 + 23 * 3600))

    def test_one_hour_ttl_reduces_window(self) -> None:
        now0 = int(time.time())
        # Recommended TTL = 1 hour
        url = mint_signed_url("tarballs/A/sha.tar.gz", ttl_seconds=3600, now=now0)
        self.assertFalse(url.is_valid(now=now0 + 23 * 3600))
        self.assertTrue(url.is_valid(now=now0 + 30 * 60))

    def test_recommended_stream_through_route(self) -> None:
        """Streaming-through pattern: the server checks license validity
        on EVERY byte request. The signed URL approach checks ONCE at mint.
        """

        def can_download_through_proxy(purchase_id: str, *, revoked_purchases: set[str]) -> bool:
            return purchase_id not in revoked_purchases

        revoked: set[str] = set()
        self.assertTrue(can_download_through_proxy("pur-1", revoked_purchases=revoked))
        revoked.add("pur-1")
        # Stream proxy can revoke MID-DOWNLOAD; signed URL cannot.
        self.assertFalse(can_download_through_proxy("pur-1", revoked_purchases=revoked))


if __name__ == "__main__":
    unittest.main()

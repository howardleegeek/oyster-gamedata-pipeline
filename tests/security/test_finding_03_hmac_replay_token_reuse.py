"""Finding #03 — HMAC upload tokens are static (no nonce, no expiry).

File: web-tester/lib/upload-auth.ts, bin/upload_auth.py

Threat model
============
The token is `HMAC_SHA256(secret, tester_id)` — a deterministic function
of just the tester_id. Therefore:

  1. The SAME token authenticates every request for that tester forever.
  2. Token is embedded in the .exe filename (16-hex prefix) — anyone who
     gets the filename has lifetime upload authority.
  3. If the recorder log or the .exe filename leaks (screenshot, support
     ticket, error report uploaded to Sentry, GitHub Issue, etc.) the
     attacker can hammer /api/upload-tarball as that tester until secret
     rotation.

There's NO:
  - per-request nonce
  - timestamp + freshness window
  - rolling counter
  - revocation list
  - bind to upload content (no sha256-in-MAC)

Severity: MEDIUM (CVSS 6.5 — leaked-token replay → fraudulent attribution
+ rate-limit-bound bandwidth / quota burn against legit tester).

Known to team
=============
`upload-auth.ts` line 19-22 says backwards-compat fallback is warn-only,
and `cluster/gap6-upload-auth` docs note replay risk as "residual". This
finding raises a CONCRETE attack the docs don't enumerate: the token also
authenticates UPLOADS-OF-OTHER-CONTENT, not just uploads of recorded
gameplay. An attacker who phishes a tester's filename can:
  - upload junk tarballs (rate-limited but harvests the per-tester
    30-uploads/hr quota)
  - exhaust the tester's storage_path namespace
  - corrupt the tester's reputation (d5 rejection rate)
"""

from __future__ import annotations

import hashlib
import hmac
import unittest

SECRET = "deadbeef" * 8  # 64-hex realistic prod secret


def compute_token(tester_id: str, secret: str = SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), tester_id.encode("utf-8"), hashlib.sha256).hexdigest()


class HmacReplayTest(unittest.TestCase):
    def test_token_is_deterministic_across_time(self) -> None:
        """Same input -> same output. There is NO freshness component."""
        tester = "00000000-0000-0000-0000-000000000001"
        first = compute_token(tester)
        # 1ms later, 1 day later, 1 year later — still the same.
        second = compute_token(tester)
        self.assertEqual(first, second, "token must be deterministic — proves replay vuln")

    def test_token_is_independent_of_payload(self) -> None:
        """Token does NOT cover the tarball SHA-256. Same token, any payload."""
        tester = "00000000-0000-0000-0000-000000000001"
        # Both a 1 KB junk blob and a legit 1 GB recording would pass
        # the same token check. That's the bug.
        legitimate_tarball_sha = "a" * 64
        junk_tarball_sha = "b" * 64
        token_for_legit = compute_token(tester)
        token_for_junk = compute_token(tester)
        self.assertEqual(
            token_for_legit,
            token_for_junk,
            "token is independent of payload — proves no replay-on-content binding",
        )

    def test_recommended_fix_includes_timestamp_in_mac(self) -> None:
        """Recommended fix: incorporate a freshness signal.

        Two practical options (Engineer B's PR can pick whichever fits):

        Option A — short-lived signed bundle (15-min expiry):
            message  = f"{tester_id}|{utcnow_unix}"
            token    = HMAC(secret, message)
            header   = f"{utcnow_unix}.{token}"
        Server splits on '.', rejects if |now - ts| > 900s, then verifies.

        Option B — content-bound:
            message  = f"{tester_id}|{sha256_of_tarball}"
            token    = HMAC(secret, message)
        Server recomputes HMAC after streaming the body (already does
        SHA-256 for verification, so cheap to extend).
        """
        # Demonstrate Option A with a 60-second window.
        import time

        def make_token(tester: str, ts: int) -> str:
            msg = f"{tester}|{ts}".encode("utf-8")
            return hmac.new(SECRET.encode(), msg, hashlib.sha256).hexdigest()

        def verify(tester: str, header: str, *, now: int, window_s: int = 900) -> bool:
            try:
                ts_str, mac = header.split(".", 1)
                ts = int(ts_str)
            except (ValueError, AttributeError):
                return False
            if abs(now - ts) > window_s:
                return False
            return hmac.compare_digest(mac, make_token(tester, ts))

        tester = "00000000-0000-0000-0000-000000000001"
        now = int(time.time())
        good_header = f"{now}.{make_token(tester, now)}"
        self.assertTrue(verify(tester, good_header, now=now))

        # An hour-old token is rejected.
        old_header = f"{now - 3600}.{make_token(tester, now - 3600)}"
        self.assertFalse(verify(tester, old_header, now=now))


if __name__ == "__main__":
    unittest.main()

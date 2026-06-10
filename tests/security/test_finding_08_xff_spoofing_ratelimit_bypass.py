"""Finding #08 — Rate-limit keyed on X-Forwarded-For trivially bypassable.

File: web-tester/lib/rate-limit.ts lines 76-82

Threat model
============
`clientIpFromHeaders()` extracts the IP from the leftmost `X-Forwarded-For`
entry, with no validation that the request actually arrived through a
trusted reverse proxy:

    export function clientIpFromHeaders(headers: Headers): string {
      const xff = headers.get('x-forwarded-for');
      if (xff) return xff.split(',')[0]!.trim();
      const real = headers.get('x-real-ip');
      if (real) return real.trim();
      return 'unknown';
    }

If a request comes in DIRECTLY (no Vercel edge in front of it — e.g. via a
preview deployment, a leaked direct-origin URL, an internal load balancer
test endpoint, or a vendor portal that bypasses the WAF) the attacker
controls the X-Forwarded-For header.

Even WHEN behind Vercel, Vercel APPENDS to X-Forwarded-For but does not
sanitise the client-supplied portion. So a request like:

    curl -H "X-Forwarded-For: 1.1.1.1" https://oyster.example/api/upload-tarball

will be forwarded by Vercel as:
    X-Forwarded-For: 1.1.1.1, <real_client_ip>

The leftmost element is `1.1.1.1` — controlled by the attacker. Each new
random value gets a fresh rate-limit bucket. With 12 req/min/IP and 32-bit
IPv4 space, the per-IP rate-limit collapses to "infinite for one client".

Severity: HIGH (CVSS 7.5 — completely defeats one of two rate-limit gates
on the upload endpoint; per-tester gate still applies but is also 30/hr
which is HMAC-token-bound — see Finding #03)

Reference: OWASP API4:2023 — Unrestricted Resource Consumption.
The Vercel documentation explicitly warns about this:
  https://vercel.com/docs/edge-network/headers#x-forwarded-for

Repro
=====
"""

from __future__ import annotations

import unittest


def client_ip_from_headers(headers: dict) -> str:
    """Faithful port of rate-limit.ts:76-82."""
    xff = headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    real = headers.get("x-real-ip")
    if real:
        return real.strip()
    return "unknown"


class XffSpoofingTest(unittest.TestCase):
    def test_attacker_controls_perceived_ip(self) -> None:
        """Attacker prepends fake IP to X-Forwarded-For."""
        # When Vercel appends: leftmost is still attacker-controlled.
        forwarded_by_vercel = "1.1.1.1, 198.51.100.42"
        seen_ip = client_ip_from_headers({"x-forwarded-for": forwarded_by_vercel})
        self.assertEqual(
            seen_ip,
            "1.1.1.1",
            "Rate-limiter sees attacker-controlled IP — perfect bypass",
        )

    def test_each_request_can_use_different_ip(self) -> None:
        """Attacker iterates IPs to defeat the 12/min/IP limit."""
        buckets: set[str] = set()
        for i in range(1000):
            ip = client_ip_from_headers({"x-forwarded-for": f"10.0.0.{i % 256}, 198.51.100.42"})
            buckets.add(ip)
        self.assertEqual(
            len(buckets),
            256,
            f"Attacker fragments rate-limit across 256 buckets — "
            f"effective limit becomes 256× the configured one",
        )

    def test_recommended_fix_trust_only_vercel_appended_position(self) -> None:
        """Fix: take the RIGHTMOST element OR the second-to-last when
        Vercel is the known proxy.

        Vercel-specific (preferred):
            req.headers.get('x-vercel-forwarded-for')
        — Vercel's own header is set after their edge and not honored by
        external clients. Validate the header signature with X-Vercel-IP-*.
        """

        def fixed_client_ip_from_headers(headers: dict) -> str:
            # On Vercel: prefer their signed header.
            vercel = headers.get("x-vercel-forwarded-for")
            if vercel:
                # Vercel's header is a single client IP, not a chain.
                return vercel.strip()
            # If we MUST parse XFF, take the rightmost (the one our last
            # trusted proxy appended). Strip any client-controlled
            # leading entries.
            xff = headers.get("x-forwarded-for")
            if xff:
                parts = [p.strip() for p in xff.split(",")]
                return parts[-1] if parts else "unknown"
            return "unknown"

        seen = fixed_client_ip_from_headers(
            {
                "x-forwarded-for": "1.1.1.1, 198.51.100.42",
                "x-vercel-forwarded-for": "198.51.100.42",
            }
        )
        # The signed Vercel header gives us the real client IP.
        self.assertEqual(seen, "198.51.100.42")

        # Even fall-back-to-XFF picks the rightmost (real) IP.
        seen2 = fixed_client_ip_from_headers({"x-forwarded-for": "1.1.1.1, 198.51.100.42"})
        self.assertEqual(seen2, "198.51.100.42")


if __name__ == "__main__":
    unittest.main()

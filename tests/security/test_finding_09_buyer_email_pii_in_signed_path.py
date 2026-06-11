"""Finding #09 — Tester storage-RLS dead because uploads go via service-role.

File: web-tester/supabase/migrations/20260507000000_init.sql lines 153-159
      web-tester/app/api/upload-tarball/route.ts line 196-215

Threat model
============
The storage RLS policy

    create policy "tester reads own tarball blobs" on storage.objects
      for select to authenticated
      using (
        bucket_id = 'tarballs'
        and (storage.foldername(name))[1] = auth.uid()::text
      );

restricts authenticated users to their own folder. However, the upload
endpoint uses `getSupabaseServiceClient()` (service role — bypasses RLS),
and the buyer-side download endpoint signs URLs via service-role as well.
So this policy is NEVER consulted at runtime for the normal flow.

That means:
  - A misconfigured admin who later DISABLES service-role and falls back
    to anon won't get the policy as a safety net — the policy was never
    written to handle anon-with-tester-id-in-path.
  - There is NO defense-in-depth: a leaked storage.path from a
    misconfigured query can be fetched directly via
    /storage/v1/object/tarballs/<tester_uuid>/<sha>.tar.gz with anon key
    if and only if the bucket public-flag is later flipped. The migration
    sets `public=false` (line 148), so anon cannot SELECT — currently safe.
  - But the `for select to authenticated` policy claims to be the auth
    boundary while in reality the boundary is "is service-role used or
    not". If a future contributor adds a route that uses the user's
    Supabase client (cookie-bound, anon role) to download, it will FAIL
    silently because the RLS policy doesn't account for the buyer-side
    flow where the buyer-uid != tester-uid.

Severity: LOW (CVSS 3.7 — defense-in-depth gap; no exploitable path
TODAY because service-role is the only writer, but the policy is
written as if it's load-bearing when it isn't).

Repro
=====
Demonstrate that the policy would block a legitimate buyer-download flow
if it were ever consulted. (i.e., the policy is wrong for the actual
threat model.)
"""

from __future__ import annotations

import unittest


def storage_rls_check(*, auth_uid: str, object_name: str) -> bool:
    """Faithful port of the `tester reads own tarball blobs` policy."""
    # foldername()[1] is the FIRST path segment.
    first_folder = object_name.split("/", 1)[0]
    return first_folder == auth_uid


class TesterStorageRlsTest(unittest.TestCase):
    def test_tester_can_read_own_file(self) -> None:
        """The expected positive case — also reachable today only via
        service-role bypass."""
        self.assertTrue(
            storage_rls_check(
                auth_uid="tester-A",
                object_name="tester-A/abc.tar.gz",
            )
        )

    def test_buyer_cannot_read_via_this_policy(self) -> None:
        """A buyer signing in as buyer-B has auth.uid()=buyer-B. The
        tarball is in tester-A's folder. Policy denies — even though
        buyer-B has a paid license."""
        self.assertFalse(
            storage_rls_check(
                auth_uid="buyer-B-uuid",
                object_name="tester-A/abc.tar.gz",
            ),
            "Policy is correct in denying — but the bug is that this "
            "path is never taken anyway (service-role bypasses RLS). "
            "Adding a buyer-side download policy is the real fix.",
        )

    def test_recommended_additional_policy_for_buyer_downloads(self) -> None:
        """Recommended: add a SELECT policy for purchasers."""

        def buyer_can_read(
            *,
            auth_uid: str,
            object_name: str,
            purchases: list[dict],
            tarballs: list[dict],
        ) -> bool:
            tarball_path = object_name
            tarball = next((t for t in tarballs if t["storage_path"] == tarball_path), None)
            if not tarball:
                return False
            return any(
                p
                for p in purchases
                if p["buyer_id"] == auth_uid and p["tarball_id"] == tarball["id"]
            )

        tarballs = [{"id": "tb-1", "storage_path": "tester-A/abc.tar.gz"}]
        purchases = [{"buyer_id": "buyer-B-uuid", "tarball_id": "tb-1"}]

        # B has purchased — allowed.
        self.assertTrue(
            buyer_can_read(
                auth_uid="buyer-B-uuid",
                object_name="tester-A/abc.tar.gz",
                purchases=purchases,
                tarballs=tarballs,
            )
        )

        # C has NOT purchased — denied.
        self.assertFalse(
            buyer_can_read(
                auth_uid="buyer-C-uuid",
                object_name="tester-A/abc.tar.gz",
                purchases=purchases,
                tarballs=tarballs,
            )
        )


if __name__ == "__main__":
    unittest.main()

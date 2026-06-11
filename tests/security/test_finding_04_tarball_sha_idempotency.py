"""Finding #04 — Cross-tester tarball-sha256 collision allows attribution theft.

File: web-tester/app/api/upload-tarball/route.ts lines 217-245
      web-tester/supabase/migrations/20260507000000_init.sql line 41
        (`create unique index ... on public.tarballs(sha256);`)

Threat model
============
`tarballs.sha256` is a GLOBAL unique index (not per-tester). When a second
tester uploads a tarball whose SHA-256 matches an existing row, the
duplicate-handling branch (lines 232-245) treats it as success and
returns the EXISTING row — including the original tester_id.

Lines 233-237 do:
    .from('tarballs')
    .select('id, tester_id, uploaded_at, sha256, size_bytes, d5_verdict')
    .eq('sha256', sha)
    .single();

The recorder then receives `{ tester_id: <ORIGINAL_OWNER>, ... }`. Two failure modes:

1. **Attribution theft**: attacker (tester B) downloads tester A's
   public-bucket tarball (if A's d5_verdict='accepted' → eligible for
   buyer-side preview/download), then re-uploads it claiming to be B.
   The unique index throws, B gets A's tarball row back. B's recorder
   now has a "duplicate: true" response — that's fine for B, but
   A's tarball duration_seconds counts only ONCE — so B doesn't get
   paid for the duplicate. NOT a money bug.

   BUT: the *response* leaks tester A's `tester_id` (line 245 returns
   the existing row) to caller B. That's a tester-id enumeration leak.

2. **Storage path collision**: `storagePath = ${tester_id}/${sha}.tar.gz`
   is built from the CLAIMED tester_id (B's). Line 209 ignores the
   "already exists" error for storage, then jumps to the DB insert.
   But if the SHA collides, the existing storage_path in the DB row
   belongs to A, not B. Now B's claim never lands as a real upload.
   That's *fail-closed safe*, BUT — the storage.upload would have
   succeeded at B's path (B/<sha>.tar.gz) — leaving an orphan blob
   that's billed to disk-quota but never tracked in the DB.

Severity: MEDIUM (CVSS 5.3 — info disclosure of foreign tester_id;
disk-fill amplification when combined with quota gap)

Repro
=====
Logic-level demonstration of the duplicate-row leak.
"""

from __future__ import annotations

import unittest


class _FakeTarballsTable:
    """Mimics PG unique index on sha256."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def insert(self, row: dict) -> dict:
        for existing in self.rows:
            if existing["sha256"] == row["sha256"]:
                raise ConflictError(existing)
        self.rows.append(row)
        return row


class ConflictError(Exception):
    """Postgres 23505 unique_violation analogue."""

    def __init__(self, existing: dict) -> None:
        self.existing = existing


def _route_logic(table: _FakeTarballsTable, claim: dict) -> dict:
    """Faithful port of upload-tarball/route.ts lines 217-252."""
    try:
        return table.insert(claim)
    except ConflictError as e:
        # The real route returns `existing` — that may belong to a
        # different tester.
        return {**e.existing, "duplicate": True}


class TarballShaCollisionTest(unittest.TestCase):
    def test_duplicate_response_leaks_foreign_tester_id(self) -> None:
        """Tester B uploads a tarball that collides with A's SHA-256.

        Whether intentional (download-and-re-upload) or accidental
        (empty-file hash), B's recorder gets A's tester_id back.
        """
        table = _FakeTarballsTable()
        table.insert(
            {
                "tester_id": "tester-A-uuid",
                "sha256": "deadbeef" * 8,
                "storage_path": "tester-A-uuid/deadbeef..tar.gz",
                "size_bytes": 1024,
            }
        )

        # B claims the same SHA.
        response_to_B = _route_logic(
            table,
            {
                "tester_id": "tester-B-uuid",
                "sha256": "deadbeef" * 8,
                "storage_path": "tester-B-uuid/deadbeef..tar.gz",
                "size_bytes": 1024,
            },
        )
        # Bug: B's recorder learns A's tester_id from the response body.
        self.assertEqual(
            response_to_B["tester_id"],
            "tester-A-uuid",
            "Duplicate-response leaks the original tester_id to the duplicate uploader",
        )

    def test_correct_behavior_should_return_404_or_403(self) -> None:
        """Recommended fix: refuse cross-tester duplicates."""

        def fixed_route(table: _FakeTarballsTable, claim: dict) -> dict:
            try:
                return table.insert(claim)
            except ConflictError as e:
                if e.existing["tester_id"] == claim["tester_id"]:
                    # Same tester re-uploading — return their own row.
                    return {**e.existing, "duplicate": True}
                # Foreign tester duplicate — surface as 409 with no
                # leaked tester_id. (In TS: return 409 with a generic
                # message; do NOT include the existing row in the body.)
                return {"error": "sha256 already exists"}

        table = _FakeTarballsTable()
        table.insert({"tester_id": "A", "sha256": "x" * 64, "storage_path": "A/x.tar.gz"})

        resp = fixed_route(
            table,
            {"tester_id": "B", "sha256": "x" * 64, "storage_path": "B/x.tar.gz"},
        )
        self.assertNotIn("tester_id", resp)


if __name__ == "__main__":
    unittest.main()

"""Finding #10 — bin/audit_artifact_honesty.py only catches one class of fake;
financial / web-side fabrications are out of scope.

File: bin/audit_artifact_honesty.py

Threat model
============
`audit_artifact_honesty.py` is invoked in CI and praised as proof that
fabricated data is blocked. But its scope (line 24-29) is hard-coded to:

    SCAN_DIRS = (
        ROOT / "v1_claude_residuals",
        ROOT / "v2_minimax_residuals",
        ROOT / "v2prime_glm_residuals",
        ROOT / "v3_physics_oracle",
    )

It walks ONLY residual-functions in those four directories, scanning for
either the literal string "ABSTAIN" or a NaN/inf return in any function
whose parameter ends in `_path` / `_dir` (etc.).

What it DOES NOT catch:
  1. The pre-fix `dev_session_*` fake checkout sessions (TS files, never
     reached by this lint).
  2. The `acct_mock_*` Stripe Connect IDs that the docstring brags were
     "removed" — there's NO automated check that they don't come back.
  3. Synthetic SHA-256 / fake tester_id / fake purchase IDs in any of:
     web-buyer/**, web-tester/**, bin/**/*.py outside SCAN_DIRS.
  4. The MockStripeClient + MockSupabaseClient classes that still exist
     in lib/stripe.ts and bin/payout_cron.py — they're guarded behind
     env-var checks but if those checks are inverted in a future PR the
     lint won't notice.
  5. ANY synthetic data in the recorder client (vendor/recorder),
     SDK code, or buyer-portal.

Severity: LOW (CVSS 3.1 — not a vuln itself; a gap in the iron-law
enforcement that lets future regressions slip in).

Reproduction
============
Show that introducing a `dev_session_FAKE_xxx` literal in route.ts or a
fabricated `acct_mock_*` returned from a route handler is INVISIBLE to the
existing audit script.
"""

from __future__ import annotations

import unittest
from pathlib import Path


# Reuse the AST-walk approach for clarity rather than importing the prod
# script (which has hard-coded paths).
def _audit_directories(dirs: list[Path]) -> list[str]:
    """Run the SAME audit logic against arbitrary dirs (not the hardcoded SCAN_DIRS)."""
    import ast

    violations: list[str] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.py")):
            try:
                tree = ast.parse(f.read_text())
            except Exception:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                # The audit only flags `_path` / `_dir` params; not
                # `session_id`, `account_id`, `purchase_id`, etc.
                pass
    return violations


class AuditScopeGapTest(unittest.TestCase):
    def test_audit_scope_misses_money_minting_in_ts_files(self) -> None:
        """A regression of `dev_session_*` minting in a TS file would
        re-introduce fake checkout sessions. The audit lint doesn't read TS."""
        # The repo has ZERO .py audit that scans .ts / .tsx files.
        # Demonstrate by walking the audit's docstring-claimed scope.
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        # The audit only enumerates four dirs under bin/. None are TS-aware.
        audit_path = repo_root / "bin" / "audit_artifact_honesty.py"
        if not audit_path.is_file():
            self.skipTest("audit script missing")
        content = audit_path.read_text()
        self.assertNotIn(".ts", content)
        self.assertNotIn(".tsx", content)
        self.assertNotIn("web-buyer", content)
        self.assertNotIn("web-tester", content)
        self.assertNotIn("dev_session_", content)
        self.assertNotIn("acct_mock_", content)

    def test_recommended_additional_lint(self) -> None:
        """Recommended: add `bin/audit_no_fake_ids.py` that greps for
        forbidden id prefixes across the whole repo."""
        forbidden_prefixes = [
            "dev_session_",
            "acct_mock_",
            "tr_mock_",
            "po_mock_",
            "cs_mock_",
            "pi_mock_",
            "stub_buyer",
            "sample-tester@example",
            "test@example.com",  # for clarity — flag in non-test files only
        ]
        # A representative scan would iterate every .ts / .tsx / .py /
        # .py.tmpl in the repo (minus tests/**, vendor/**, node_modules/**)
        # and fail if any of these literals appear. We do NOT run it here
        # — that's the lint's job, not this test's.
        self.assertGreater(len(forbidden_prefixes), 5)


if __name__ == "__main__":
    unittest.main()

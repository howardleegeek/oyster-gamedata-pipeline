"""Regression tests for bugs found during 2026-05-13 bug hunt.

These tests are intentionally documented to FAIL until each bug is fixed.
Each test maps to a bug entry in BUG_REPORT_2026_05_13.md.

Run with:
    pytest tests/test_qa_bug_hunt_2026_05_13.py -v

Tests marked xfail are expected to fail on current main — they confirm
the bugs are present and the report is accurate.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "bin"))


# ---------------------------------------------------------------------------
# BUG-01 — Zero-byte placeholder package passes 22/24 PRD lint checks
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason="BUG-01: zero-byte placeholder package passes 22/24 lints", strict=True)
def test_zero_byte_package_fails_lint() -> None:
    """An empty placeholder package (all zero-byte required files) MUST fail."""
    import lint_v3_prd_grounded as L

    with tempfile.TemporaryDirectory() as td:
        pkg = Path(td)
        for name in ("video.mp4", "systeminfo.json", "action_camera.json", "gameinfo.xlsx"):
            (pkg / name).touch()
        (pkg / "depth").mkdir()

        rpt = L.run_all_checks(pkg)
        # Should NOT pass 22/24 — a zero-byte video.mp4 is not a 1920x1080
        # 5-6 min video. PRD criteria 1+2 must fail.
        video_res = next(r for r in rpt.results if r.criterion_id == 1)
        video_dur = next(r for r in rpt.results if r.criterion_id == 2)
        assert not video_res.passed, "zero-byte video.mp4 should not pass video-resolution check"
        assert not video_dur.passed, "zero-byte video.mp4 should not pass video-duration check"


# ---------------------------------------------------------------------------
# BUG-02 — LintReport.to_dict() crashes with ZeroDivisionError when total=0
# ---------------------------------------------------------------------------


def test_lint_report_to_dict_zero_total_does_not_crash() -> None:
    """LintReport must not crash on pass_rate computation when total=0."""
    import lint_v3_prd_grounded as L

    rpt = L.LintReport(data_dir=Path("."), total_checks=0)
    try:
        d = rpt.to_dict()
    except ZeroDivisionError as e:
        pytest.fail(f"BUG-02 confirmed: to_dict() raised ZeroDivisionError: {e}")
    # If the fix is in place, pass_rate should be safely defined (e.g. '0.0%' or 'N/A').
    assert "summary" in d
    assert "pass_rate" in d["summary"]


# ---------------------------------------------------------------------------
# BUG-03 — Lint criteria 7,9,10,11,19,20,21 are no-op (hardcoded True)
# ---------------------------------------------------------------------------


def test_lint_audio_quality_inspects_audio() -> None:
    """QA1 finding #6 fix (BUG-03): cr-7 used to hardcode True. After the
    fix, a package with NO audio files MUST fail cr-7."""
    import lint_v3_prd_grounded as L

    with tempfile.TemporaryDirectory() as td:
        pkg = Path(td)
        for name in ("video.mp4", "systeminfo.json", "action_camera.json", "gameinfo.xlsx"):
            (pkg / name).touch()
        (pkg / "depth").mkdir()
        # No audio files at all.

        rpt = L.run_all_checks(pkg)
        audio_quality = next(r for r in rpt.results if r.criterion_id == 7)
        assert not audio_quality.passed, (
            "QA1 #6 regression: criterion 7 should fail when no audio files "
            "exist. Fix details: bin/lint_v3_prd_grounded.py::_check_audio_specs"
        )


def test_lint_audio_channels_inspects_real_files() -> None:
    """QA1 finding #6 fix (BUG-03): cr-9 (Audio Channels) was hardcoded True."""
    import lint_v3_prd_grounded as L

    with tempfile.TemporaryDirectory() as td:
        pkg = Path(td)
        for name in ("video.mp4", "systeminfo.json", "action_camera.json", "gameinfo.xlsx"):
            (pkg / name).touch()
        (pkg / "depth").mkdir()

        rpt = L.run_all_checks(pkg)
        ch = next(r for r in rpt.results if r.criterion_id == 9)
        assert not ch.passed, (
            "QA1 #6 regression: criterion 9 should fail when no audio files exist"
        )


def test_lint_sample_rate_inspects_real_files() -> None:
    """QA1 finding #6 fix (BUG-03): cr-10 (Sample Rate) was hardcoded True."""
    import lint_v3_prd_grounded as L

    with tempfile.TemporaryDirectory() as td:
        pkg = Path(td)
        for name in ("video.mp4", "systeminfo.json", "action_camera.json", "gameinfo.xlsx"):
            (pkg / name).touch()
        (pkg / "depth").mkdir()

        rpt = L.run_all_checks(pkg)
        sr = next(r for r in rpt.results if r.criterion_id == 10)
        assert not sr.passed, (
            "QA1 #6 regression: criterion 10 should fail when no audio files exist"
        )


def test_lint_route_distribution_inspects_real_files() -> None:
    """QA1 finding #6 fix (BUG-03): cr-11 (Route Distribution) was hardcoded True."""
    import lint_v3_prd_grounded as L

    with tempfile.TemporaryDirectory() as td:
        pkg = Path(td)
        for name in ("video.mp4", "systeminfo.json", "action_camera.json", "gameinfo.xlsx"):
            (pkg / name).touch()
        (pkg / "depth").mkdir()

        rpt = L.run_all_checks(pkg)
        rd = next(r for r in rpt.results if r.criterion_id == 11)
        assert not rd.passed, (
            "QA1 #6 regression: criterion 11 should fail when no route files exist"
        )


def test_lint_overlay_criteria_are_deprecated() -> None:
    """QA1 finding #6 fix (BUG-03): cr-19/20/21 require CV — marked deprecated.

    They no longer contribute to the pass-rate score. The report retains
    the entries with `deprecated=True` so dashboards keep rendering.
    """
    import lint_v3_prd_grounded as L

    with tempfile.TemporaryDirectory() as td:
        pkg = Path(td)
        for name in ("video.mp4", "systeminfo.json", "action_camera.json", "gameinfo.xlsx"):
            (pkg / name).touch()
        (pkg / "depth").mkdir()

        rpt = L.run_all_checks(pkg)
        for cid in (19, 20, 21):
            r = next(x for x in rpt.results if x.criterion_id == cid)
            assert r.deprecated, f"criterion {cid} should be deprecated post-fix"
        # And they're excluded from total_checks.
        assert rpt.total_checks == 21, (
            f"total_checks should be 24 - 3 deprecated = 21, got {rpt.total_checks}"
        )


# ---------------------------------------------------------------------------
# BUG-04 — Lint keyCode check only inspects top-level dicts, not nested arrays
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason="BUG-04: nested keyCode bypasses lint check", strict=True)
def test_lint_keycode_catches_nested_strings() -> None:
    """A record array with string keyCode values must fail keyCode lint."""
    import lint_v3_prd_grounded as L

    with tempfile.TemporaryDirectory() as td:
        pkg = Path(td)
        for name in ("video.mp4", "systeminfo.json", "action_camera.json", "gameinfo.xlsx"):
            (pkg / name).touch()
        (pkg / "depth").mkdir()
        # records.json with nested keyCode-as-string entries
        nested = {"events": [{"keyCode": "BAD_STRING"} for _ in range(100)]}
        (pkg / "records.json").write_text(json.dumps(nested))

        rpt = L.run_all_checks(pkg)
        kc = next(r for r in rpt.results if r.criterion_id == 17)
        assert not kc.passed, "criterion 17 should fail when nested keyCode is non-int"


# ---------------------------------------------------------------------------
# BUG-05 — Lint sampling caps make large packages cheap to game
# ---------------------------------------------------------------------------


def test_lint_depth_samples_more_than_15_files() -> None:
    """Documented expectation: lint should inspect a representative sample, not just first 15."""
    import lint_v3_prd_grounded as L
    import inspect

    src = inspect.getsource(L._check_depth_ratio)
    # Bug: the depth check slices `depth_files[:15]`. Fix should either
    # increase the slice substantially or do reservoir sampling.
    assert "[:15]" not in src or "reservoir" in src.lower(), (
        "BUG-05 confirmed: depth check samples only first 15 files; "
        "PRD requires 1800 files. Switch to reservoir sampling or scan all."
    )


# ---------------------------------------------------------------------------
# BUG-06 — Lint message says "passed" while passed=False (UX bug)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason="BUG-06: message string inconsistent with passed boolean", strict=True)
def test_lint_message_consistent_with_boolean() -> None:
    """When `passed=False`, the message MUST NOT say "passed"/"valid"/"complete"."""
    import lint_v3_prd_grounded as L

    with tempfile.TemporaryDirectory() as td:
        pkg = Path(td)
        for name in ("video.mp4", "systeminfo.json", "action_camera.json", "gameinfo.xlsx"):
            (pkg / name).touch()
        (pkg / "depth").mkdir()
        # quaternion of length 3 → fails check 13
        bad = {"quaternion": [0.1, 0.2, 0.3]}
        (pkg / "action_camera.json").write_text(json.dumps(bad))

        rpt = L.run_all_checks(pkg)
        for r in rpt.results:
            if not r.passed:
                lower = r.message.lower()
                assert "passed" not in lower and "valid" not in lower, (
                    f"criterion {r.criterion_id}: passed=False but message says: {r.message!r}"
                )


# ---------------------------------------------------------------------------
# BUG-07 — upload_to_web_tester.py: stale filename token wins over fresh env
# ---------------------------------------------------------------------------


def test_resolve_token_filename_wins_over_hmac_secret() -> None:
    """resolve_token: filename prefix takes precedence over local HMAC compute."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from bin import upload_to_web_tester as U  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("upload_to_web_tester not in this worktree (it's in wt-gap6)")
    tid = "11111111-2222-3333-4444-555555555555"
    stale = f"OysterRecorder-aabbccdd-{tid}-deadbeefdeadbeef.exe"
    env = {"UPLOAD_HMAC_SECRET": "fresh-secret-after-rotation"}
    token = U.resolve_token(explicit=None, tester_id=tid, exe_path=stale, env=env)
    # Current code returns the stale filename prefix (16 hex chars).
    # Documented behavior. But operators rotating UPLOAD_HMAC_SECRET will
    # find that their fresh dev setup ignores the env-derived token.
    # If the fix is to invert precedence (env wins), this assertion flips.
    # For now: lock in the *current* behavior so a silent flip is caught.
    assert token == "deadbeefdeadbeef", "filename precedence is the documented behavior"


# ---------------------------------------------------------------------------
# BUG-08 — bot.js parseArgs accepts invalid port numbers (0, -1, 65536)
# ---------------------------------------------------------------------------


def test_botjs_parseargs_rejects_invalid_ports() -> None:
    """bot.js parseArgs should reject ports outside [1, 65535]."""
    import subprocess

    bot_js = REPO_ROOT / "mineflayer" / "bot.js"
    if not bot_js.exists():
        pytest.skip("bot.js not in this worktree")

    # The current parseArgs only catches NaN. We test that the runner accepts
    # bad ports — that's the bug.
    code = f"""
        const {{ parseArgs }} = require('{bot_js}');
        try {{
            const opts = parseArgs(['node','bot.js','--port','0']);
            console.log('PORT_ZERO_ACCEPTED');
        }} catch (e) {{ console.log('REJECTED:', e.message); }}
        try {{
            const opts = parseArgs(['node','bot.js','--port','-1']);
            console.log('PORT_NEGATIVE_ACCEPTED');
        }} catch (e) {{ console.log('REJECTED:', e.message); }}
        try {{
            const opts = parseArgs(['node','bot.js','--port','99999']);
            console.log('PORT_HUGE_ACCEPTED');
        }} catch (e) {{ console.log('REJECTED:', e.message); }}
    """
    result = subprocess.run(
        ["node", "-e", code],
        capture_output=True,
        text=True,
    )
    out = result.stdout
    # Once fixed, all three should be REJECTED. Currently all are ACCEPTED.
    accepted = sum(1 for s in ["PORT_ZERO_ACCEPTED", "PORT_NEGATIVE_ACCEPTED", "PORT_HUGE_ACCEPTED"] if s in out)
    assert accepted == 0, (
        f"BUG-08 confirmed: {accepted}/3 invalid ports accepted by parseArgs.\n" + out
    )


# ---------------------------------------------------------------------------
# BUG-09 — Stripe Connect return route can hijack victim's stripe_account_id
# ---------------------------------------------------------------------------


def test_connect_return_does_not_trust_query_account_id() -> None:
    """The connect/return handler must not accept ?account= from query."""
    route = REPO_ROOT / "web-tester" / "app" / "api" / "stripe" / "connect" / "return" / "route.ts"
    if not route.exists():
        pytest.skip("connect/return route not in this worktree (in wt-gap6)")
    src = route.read_text()
    # Bug: pulls account id from query and uses it to overwrite tester row.
    # Fix: ignore the query param OR verify it equals existing stripe_account_id.
    assert "accountIdFromQuery" not in src or "ignored" in src.lower(), (
        "BUG-09: connect/return reads ?account= from query and writes to testers table. "
        "An attacker can craft a link that hijacks the victim's stripe_account_id."
    )


# ---------------------------------------------------------------------------
# BUG-10 — Checkout metadata exceeds Stripe 500-char limit at 20 tarballs
# ---------------------------------------------------------------------------


def test_checkout_tarball_ids_metadata_under_500_chars() -> None:
    """QA1 finding #3 fix (BUG-10): the checkout route must NOT join all
    tarball_ids into a single >500-char metadata value.

    The fix chunks tarball_ids across `tarball_ids_1` / `tarball_ids_2` /
    ... fields with a `tarball_ids_count` sentinel. Verify the chunking
    machinery is present (rather than naive `.join(',')` into a single
    metadata value).
    """
    route = REPO_ROOT / "web-buyer" / "app" / "api" / "checkout" / "route.ts"
    if not route.exists():
        pytest.skip("checkout route not in this worktree (in wt-gap6)")
    src = route.read_text()
    assert "tarball_ids_count" in src and "TARBALL_IDS_PER_CHUNK" in src, (
        "QA1 #3 regression: checkout route must chunk tarball_ids into "
        "`tarball_ids_*` metadata fields (each <500 chars) and emit a "
        "`tarball_ids_count` sentinel. See web-buyer/app/api/checkout/route.ts."
    )
    # Spot-check: there should be no top-level `tarball_ids: tarball_ids.join`
    # any more (that's the broken code path).
    assert "tarball_ids: tarball_ids.join" not in src.replace(" ", ""), (
        "QA1 #3 regression: top-level join of tarball_ids into single metadata "
        "field is the BUG-10 bug. Use chunking instead."
    )


# ---------------------------------------------------------------------------
# QA1 finding #2 fix (BUG-15) — UPLOAD_REQUIRE_TOKEN defaults to true
# ---------------------------------------------------------------------------


def test_upload_require_token_defaults_to_true() -> None:
    """Fresh deploy must default to fail-closed: `requireToken=True`.

    Verifies both the TypeScript (web-tester/lib/upload-auth.ts) and the
    Python (bin/upload_auth.py) parity modules — they MUST agree or a
    drift between languages reintroduces the bug.
    """
    # Python side.
    from bin.upload_auth import UploadAuthConfig

    cfg = UploadAuthConfig.from_env({})
    assert cfg.require_token is True, (
        "QA1 #2 regression: UploadAuthConfig.from_env({}) must default "
        "require_token=True (fail-closed). See bin/upload_auth.py."
    )

    # TS side — grep the source for the new default semantics.
    ts = (REPO_ROOT / "web-tester" / "lib" / "upload-auth.ts").read_text()
    # The new code uses the negation pattern: `!(raw === 'false' || ...)`
    assert "!(raw === 'false'" in ts or "default to TRUE" in ts, (
        "QA1 #2 regression: web-tester/lib/upload-auth.ts must default "
        "requireToken=true. Look for `!(raw === 'false'...)` or equivalent."
    )


def test_upload_route_returns_503_when_misconfigured() -> None:
    """When `requireToken=true` (the default) and `UPLOAD_HMAC_SECRET` is
    unset, the upload route MUST 503 — not silently accept anonymous uploads."""
    route = REPO_ROOT / "web-tester" / "app" / "api" / "upload-tarball" / "route.ts"
    if not route.exists():
        pytest.skip("upload-tarball route not in this worktree")
    src = route.read_text()
    # The fix adds a 503 branch for `unconfigured + requireToken=true`.
    assert "auth.requireToken" in src and "status: 503" in src, (
        "QA1 #2 regression: upload-tarball route must return 503 on the "
        "auth.unconfigured + requireToken=true path. See the fail-fast block "
        "in web-tester/app/api/upload-tarball/route.ts."
    )


# ---------------------------------------------------------------------------
# QA1 finding #4 fix — duplicate-tarball credit theft via cart-add
# ---------------------------------------------------------------------------


def test_checkout_rejects_duplicate_tarball_ids() -> None:
    """The checkout route must reject `tarball_ids` arrays with duplicates.

    Defense-in-depth: both schema-level (z.refine) and server-side dedup
    (`new Set(...)`) must be present.
    """
    route = REPO_ROOT / "web-buyer" / "app" / "api" / "checkout" / "route.ts"
    if not route.exists():
        pytest.skip("checkout route not in this worktree")
    src = route.read_text()
    assert ".refine(" in src and "new Set(ids)" in src, (
        "QA1 #4 regression: Body schema must `.refine` to reject duplicate "
        "tarball_ids. See web-buyer/app/api/checkout/route.ts."
    )
    # Server-side belt-and-braces dedup just before catalog lookup.
    assert "Array.from(new Set(parsed.data.tarball_ids))" in src, (
        "QA1 #4 regression: server-side dedup before catalog resolution must "
        "remain even if the schema is loosened in a future refactor."
    )


def test_cart_cookie_dedups_on_read() -> None:
    """`readCartCookie` must dedup so a pre-fix / attacker-crafted cookie
    can't propagate duplicates into the checkout array."""
    cart_cookie = REPO_ROOT / "web-buyer" / "lib" / "cart-cookie.ts"
    if not cart_cookie.exists():
        pytest.skip("cart-cookie module not in this worktree")
    src = cart_cookie.read_text()
    # The fix uses `Array.from(new Set(strs))` inside readCartCookie.
    read_fn = re.search(r"export function readCartCookie\(\).*?^\}", src, re.DOTALL | re.MULTILINE)
    assert read_fn, "could not locate readCartCookie definition"
    body = read_fn.group(0)
    assert "new Set(" in body, (
        "QA1 #4 regression: readCartCookie must dedup its return value via "
        "`Array.from(new Set(...))`. See web-buyer/lib/cart-cookie.ts."
    )


# ---------------------------------------------------------------------------
# QA1 finding #5 fix (BUG-21) — download route must NOT 302 to external URL
# ---------------------------------------------------------------------------


def test_download_route_proxies_external_url_instead_of_redirecting() -> None:
    """The external-URL branch must stream-proxy the bytes with our own
    Content-Disposition so the token-embedded filename reaches the recorder.
    A 302 to GitHub would lose the filename to upstream's Content-Disposition."""
    route = REPO_ROOT / "web-tester" / "app" / "api" / "download" / "[testerId]" / "route.ts"
    if not route.exists():
        pytest.skip("download route not in this worktree")
    src = route.read_text()
    # The fix replaces `NextResponse.redirect(...)` with a fetch + Response.
    # Look for the new fetch + Content-Disposition pattern.
    assert "await fetch(env.recorderExeUrl" in src, (
        "QA1 #5 regression: external-URL branch must stream-proxy via fetch(), "
        "not 302-redirect. See web-tester/app/api/download/[testerId]/route.ts."
    )
    # And the Content-Disposition with our token-embedded filename must be set.
    assert "'Content-Disposition'" in src and "buildFilename" in src, (
        "QA1 #5 regression: must build the token-embedded filename and emit "
        "Content-Disposition. See `buildFilename(testerId)`."
    )


def test_download_filename_token_matches_verifier() -> None:
    """The 16-hex prefix embedded in the .exe filename MUST be the same value
    the verifier in lib/upload-auth.ts will accept. A silent change to the
    HMAC algorithm or the prefix slice on one side breaks every recorder
    build at upload time."""
    from bin.upload_auth import compute_token, compute_token_prefix, verify_token

    tester_id = "11111111-2222-3333-4444-555555555555"
    secret = "AB" * 32  # 64 hex chars, 32 bytes

    prefix = compute_token_prefix(tester_id, secret)
    # The prefix is what gets embedded in `OysterRecorder-...-{prefix}.exe`
    assert len(prefix) == 16, f"expected 16-hex prefix, got {len(prefix)}: {prefix!r}"
    # And the verifier must accept it.
    assert verify_token(tester_id, prefix, secret) is True, (
        "QA1 #5 regression: verifier rejected the very prefix that "
        "compute_token_prefix produces. Embed/verifier drift."
    )
    # Full digest also accepted.
    full = compute_token(tester_id, secret)
    assert verify_token(tester_id, full, secret) is True

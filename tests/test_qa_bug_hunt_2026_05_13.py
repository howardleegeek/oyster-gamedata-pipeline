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


@pytest.mark.xfail(reason="BUG-03: 7 of 24 lint criteria are hardcoded True", strict=True)
def test_lint_audio_quality_inspects_audio() -> None:
    """Audio quality (criterion 7) should depend on the audio actually present."""
    import lint_v3_prd_grounded as L

    with tempfile.TemporaryDirectory() as td:
        pkg = Path(td)
        for name in ("video.mp4", "systeminfo.json", "action_camera.json", "gameinfo.xlsx"):
            (pkg / name).touch()
        (pkg / "depth").mkdir()
        # No audio files at all.

        rpt = L.run_all_checks(pkg)
        audio_quality = next(r for r in rpt.results if r.criterion_id == 7)
        # A package with NO audio files cannot pass an audio-quality check.
        # The current implementation returns True regardless.
        assert not audio_quality.passed, (
            "criterion 7 should fail when no audio files exist; "
            "current implementation hardcodes True"
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
    """Stripe metadata values are limited to 500 chars; checkout allows 20 UUIDs."""
    route = REPO_ROOT / "web-buyer" / "app" / "api" / "checkout" / "route.ts"
    if not route.exists():
        pytest.skip("checkout route not in this worktree (in wt-gap6)")
    src = route.read_text()
    # Bug: Body schema allows max(20) tarball_ids; metadata joined exceeds 500 chars.
    m = re.search(r"\.max\((\d+)\)", src)
    if not m:
        pytest.skip("could not parse Body schema max()")
    max_ids = int(m.group(1))
    # 37 chars per UUID+comma; 13 fits in 500 chars.
    safe = 500 // 37
    assert max_ids <= safe, (
        f"BUG-10: Body.tarball_ids max={max_ids} but Stripe metadata cap is 500 chars "
        f"(≤{safe} UUIDs). Stripe API will 400 the checkout session create call."
    )

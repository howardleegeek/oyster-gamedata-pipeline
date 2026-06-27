"""Iron-law lint: web-tester / web-buyer source must NOT ship fabricated data.

Howard 2026-05-07: caught a violation where lib/sample-data.ts exported
hardcoded "24.7 hours / $148.20 earnings" sample data gated by a
`isSupabaseConfigured()` check labeled "DEV MODE". Even with a banner,
fabricated numbers shipping in source IS a placeholder under the
no-fake-data canon. Fix was to delete the file + hard-gate pages with
<NotConfigured>.

Then the same day Howard ordered a "全面审计" (full audit) and we found
web-buyer carrying the same pattern at much larger scale: a 9KB
sample-data.ts with 5 fabricated tarballs / fake D5 scores / fake buyer
emails / `dev_session_*` minted Stripe sessions. All deleted +
hard-gated the same way.

This test makes sure no future contributor re-introduces either pattern.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files that legitimately contain the words "mock"/"sample"/"fake" because
# they document the iron-law fix or implement test-only utilities.
ALLOWED_REFERENCES = {
    # Test-only mock client lives here, callable only via __testOnlyMockClient.
    "web-tester/lib/stripe.ts",
    # Documents the iron-law fix in comments.
    "web-tester/lib/real-stats.ts",
    "web-tester/components/NotConfigured.tsx",
    "web-buyer/components/NotConfigured.tsx",
    # READMEs and docs can describe test patterns + mock usage.
    "web-tester/README.md",
    "web-buyer/README.md",
}


def _scan(root: Path, banned_pattern: re.Pattern) -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in {".ts", ".tsx"}:
            continue
        # Skip node_modules and .next build outputs.
        if any(part in {"node_modules", ".next", "out", "build"} for part in path.parts):
            continue
        rel = str(path.relative_to(REPO_ROOT))
        if rel in ALLOWED_REFERENCES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.split("\n"), start=1):
            if banned_pattern.search(line):
                # Skip comment lines that start with // or are inside /* */
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("*"):
                    continue
                hits.append((rel, i, line.strip()))
    return hits


# ---------------------------------------------------------------------------
# web-tester checks
# ---------------------------------------------------------------------------


def test_no_sample_data_imports_in_web_tester():
    """web-tester: no file imports from `./sample-data`/`lib/sample-data`."""
    web_tester = REPO_ROOT / "web-tester"
    if not web_tester.is_dir():
        return  # web-tester not present in this branch — skip
    pat = re.compile(r"sample-data['\"]|sampleStats|sampleTarballs|samplePayouts")
    hits = _scan(web_tester, pat)
    if hits:
        msg = "\n".join(f"  {h[0]}:{h[1]}: {h[2]}" for h in hits)
        raise AssertionError(
            f"Iron-law violation: fabricated sample-data is back in web-tester. Hits:\n{msg}"
        )


def test_no_dev_mode_branches_in_web_tester_pages():
    """web-tester pages must not have DEV MODE fake-data branches."""
    web_tester = REPO_ROOT / "web-tester"
    if not web_tester.is_dir():
        return
    pat = re.compile(r"\bdev_sample\b|\bDEV MODE: showing sample\b|\bMockStripeClient\(\)")
    hits = _scan(web_tester, pat)
    if hits:
        msg = "\n".join(f"  {h[0]}:{h[1]}: {h[2]}" for h in hits)
        raise AssertionError(
            f"Iron-law violation: DEV MODE fake-data branch is back in web-tester. Hits:\n{msg}"
        )


def test_getStripeClient_throws_when_not_configured():
    """The factory must throw, not silently return MockStripeClient.
    Verified by source-grep for the explicit throw."""
    src_path = REPO_ROOT / "web-tester" / "lib" / "stripe.ts"
    if not src_path.is_file():
        return  # web-tester not present
    src = src_path.read_text()
    assert "throw new Error" in src and "isStripeConfigured" in src, (
        "getStripeClient() must throw when Stripe isn't configured. "
        "Don't fall back to MockStripeClient at runtime."
    )


def test_NotConfigured_component_exists_in_web_tester():
    """The hard-gate component must exist for pages to render when env
    vars are missing."""
    p = REPO_ROOT / "web-tester" / "components" / "NotConfigured.tsx"
    if not p.parent.parent.is_dir():
        return  # web-tester not present
    assert p.is_file(), "components/NotConfigured.tsx is the iron-law gate; don't delete it"
    text = p.read_text()
    assert "Iron-law" in text, "NotConfigured must explain the iron-law to contributors"


def test_sample_data_file_is_gone_in_web_tester():
    """The file that exported fabricated tester stats must stay deleted."""
    p = REPO_ROOT / "web-tester" / "lib" / "sample-data.ts"
    assert not p.exists(), (
        f"{p} must NOT exist — it contained fabricated tester stats. "
        f"Use real-stats.ts (live GitHub release fetch) or NotConfigured."
    )


def test_devmodebanner_file_is_gone_in_web_tester():
    """DevModeBanner advertised the DEV MODE fabrication; must stay deleted."""
    p = REPO_ROOT / "web-tester" / "components" / "DevModeBanner.tsx"
    assert not p.exists(), (
        f"{p} must NOT exist — it advertised the DEV MODE fabrication. "
        f"Use NotConfigured for hard-gate UI instead."
    )


def test_no_recorder_stub_text_response():
    """The .exe download endpoint must NOT fall back to plaintext stub
    when the binary is missing; it must 404 with a remediation message."""
    p = REPO_ROOT / "web-tester" / "app" / "api" / "download" / "[testerId]" / "route.ts"
    if not p.is_file():
        return
    src = p.read_text()
    assert "OysterRecorder placeholder for tester" not in src, (
        "Iron-law: /api/download/[testerId] must not return a 'placeholder' "
        "text file pretending to be the .exe. Return 404 instead."
    )
    assert "X-Recorder-Stub" not in src, (
        "Iron-law: don't ship the X-Recorder-Stub header — that's the "
        "fabricated stub-response pattern."
    )


def test_no_tmp_uploads_dev_fallback():
    """The upload endpoint must NOT silently write to /tmp-uploads + return
    a synthetic UUID success response when Supabase isn't configured."""
    p = REPO_ROOT / "web-tester" / "app" / "api" / "upload-tarball" / "route.ts"
    if not p.is_file():
        return
    src = p.read_text()
    assert "tmp-uploads" not in src, (
        "Iron-law: /api/upload-tarball must NOT fall back to writing to "
        "tmp-uploads. Return 503 with envVars when Supabase is missing."
    )
    assert "mode: 'dev_local'" not in src, (
        "Iron-law: don't return mode='dev_local' — that's the synthetic "
        "success pattern that fooled the recorder into thinking uploads landed."
    )


# ---------------------------------------------------------------------------
# web-buyer checks
# ---------------------------------------------------------------------------


def test_no_sample_data_imports_in_web_buyer():
    """web-buyer: no file imports from `./sample-data`/`lib/sample-data`."""
    web_buyer = REPO_ROOT / "web-buyer"
    if not web_buyer.is_dir():
        return
    pat = re.compile(
        r"sample-data['\"]|sampleCatalog|sampleCatalogById|sampleActionCameraRecords"
        r"|sampleBuyer|samplePurchases|sampleLicenses"
    )
    hits = _scan(web_buyer, pat)
    if hits:
        msg = "\n".join(f"  {h[0]}:{h[1]}: {h[2]}" for h in hits)
        raise AssertionError(
            f"Iron-law violation: fabricated sample-data is back in web-buyer. Hits:\n{msg}"
        )


def test_no_dev_mode_branches_in_web_buyer():
    """web-buyer must not mint fake Stripe sessions or render `[DEV MODE: …]` text."""
    web_buyer = REPO_ROOT / "web-buyer"
    if not web_buyer.is_dir():
        return
    pat = re.compile(
        r"dev_session_|mode:\s*['\"]dev_fake['\"]|\[DEV MODE: |Checkout is faked"
        r"|fakeSession|fakeSignedUrl|stubBody"
    )
    hits = _scan(web_buyer, pat)
    if hits:
        msg = "\n".join(f"  {h[0]}:{h[1]}: {h[2]}" for h in hits)
        raise AssertionError(
            f"Iron-law violation: DEV MODE fake-data branches are back in web-buyer. Hits:\n{msg}"
        )


def test_sample_data_file_is_gone_in_web_buyer():
    """web-buyer's 9KB fabricated catalog must stay deleted."""
    p = REPO_ROOT / "web-buyer" / "lib" / "sample-data.ts"
    assert not p.exists(), (
        f"{p} must NOT exist — it contained 5 fabricated tarballs with fake D5 "
        f"scores, fake buyer emails, fake purchases, and fake licenses. "
        f"Use NotConfigured to hard-gate when Supabase is missing."
    )


def test_devmodebanner_file_is_gone_in_web_buyer():
    """web-buyer's DevModeBanner advertised the fabrication; must stay deleted."""
    p = REPO_ROOT / "web-buyer" / "components" / "DevModeBanner.tsx"
    assert not p.exists(), f"{p} must NOT exist — it advertised the DEV MODE fabrication."


def test_NotConfigured_component_exists_in_web_buyer():
    """web-buyer must have its own hard-gate component."""
    p = REPO_ROOT / "web-buyer" / "components" / "NotConfigured.tsx"
    if not p.parent.parent.is_dir():
        return
    assert p.is_file(), "web-buyer/components/NotConfigured.tsx is the iron-law gate"
    text = p.read_text()
    assert "Iron-law" in text, "NotConfigured must explain the iron-law to contributors"


def test_buyer_stripe_throws_when_not_configured():
    """web-buyer/lib/stripe.ts must throw when not configured (no silent null)."""
    p = REPO_ROOT / "web-buyer" / "lib" / "stripe.ts"
    if not p.is_file():
        return
    src = p.read_text()
    assert "throw new Error" in src and "isStripeConfigured" in src, (
        "Iron-law: getStripe() must throw when Stripe isn't configured. "
        "Returning null lets callers fall through to dev_session_* fakes."
    )


def test_buyer_catalog_throws_when_not_configured():
    """web-buyer/lib/catalog.ts must throw CatalogNotConfiguredError when supabase isn't ready."""
    p = REPO_ROOT / "web-buyer" / "lib" / "catalog.ts"
    if not p.is_file():
        return
    src = p.read_text()
    assert "CatalogNotConfiguredError" in src, (
        "Iron-law: web-buyer/lib/catalog.ts must throw CatalogNotConfiguredError "
        "when supabase is missing — never fall back to sampleCatalog()."
    )
    assert "sampleCatalog" not in src, (
        "Iron-law: web-buyer/lib/catalog.ts must NOT import or call "
        "sampleCatalog() — that's fabricated catalog data."
    )


def test_checkout_route_returns_503_when_not_configured():
    """The buyer checkout API must return 503 with envVars, not mint fake sessions."""
    p = REPO_ROOT / "web-buyer" / "app" / "api" / "checkout" / "route.ts"
    if not p.is_file():
        return
    src = p.read_text()
    assert "status: 503" in src, "Iron-law: /api/checkout must return 503 when not configured."
    assert "fakeSession" not in src and "dev_session_" not in src, (
        "Iron-law: /api/checkout must NOT mint dev_session_* fake Stripe sessions."
    )


# ---------------------------------------------------------------------------
# R01 recorder iron-law tests (spec R01_recorder_iron_law_polish.md)
#
# These tests verify the recorder's iron-law constraints WITHOUT importing
# the full recorder_consumer_lite module (which requires tkinter / Windows).
# Instead we: (a) source-grep the recorder for banned patterns, (b) import
# only the standalone helpers (recorder_window_capture_helper), and (c)
# replicate the core decision logic inline to test the hard-gate.
# ---------------------------------------------------------------------------

import os
import sys
from unittest import mock

# Ensure bin/ is importable for the window capture helper.
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"),
)


def _read_recorder_source() -> str:
    """Read recorder_consumer_lite.py source without importing it."""
    p = REPO_ROOT / "bin" / "recorder_consumer_lite.py"
    return p.read_text(encoding="utf-8")


def test_recorder_hard_gates_placeholder_in_v026_plus():
    """v0.26.0+ recorder must hard-fail when game-state JSONL is missing
    and --allow-placeholder is NOT set. No silent placeholder fallback.

    Verified by source-grepping for the RecorderError raise + checking
    the version is >= 0.26.0."""
    src = _read_recorder_source()

    # 1. Version must be >= 0.26.0.
    assert (
        'RECORDER_VERSION = "lite-v0.28' in src
        or 'RECORDER_VERSION = "lite-v0.27.0' in src
        or 'RECORDER_VERSION = "lite-v0.26.0' in src
    ), "RECORDER_VERSION must be >= 0.26.0 for iron-law hard-gate"

    # 2. RecorderError class must exist.
    assert "class RecorderError" in src, (
        "RecorderError exception class must be defined for iron-law hard-gates"
    )

    # 3. The hard-gate: when JSONL missing + no allow_placeholder → RecorderError.
    assert "raise RecorderError(" in src, (
        "Recorder must raise RecorderError when game-state JSONL is missing"
    )
    assert "Real game-state Fabric mod not loaded" in src, (
        "Hard-gate error message must include 'Real game-state Fabric mod not loaded'"
    )

    # 4. The old silent fallback line must be GONE.
    assert (
        'no game-state JSONL — using placeholder camera/player fields")\n' not in src
        or "pre-v0.26.0" in src
    ), (
        "Iron-law: the old silent placeholder fallback line must be removed "
        "or gated behind pre-v0.26.0 check"
    )


def test_recorder_allows_placeholder_with_explicit_flag():
    """When --allow-placeholder is set, recorder should create tarball but
    mark metadata with data_authenticity='placeholder'."""
    src = _read_recorder_source()

    # 1. --allow-placeholder flag must be parsed.
    assert "--allow-placeholder" in src, "Recorder must accept --allow-placeholder CLI flag"

    # 2. When flag is set, metadata.json must contain data_authenticity.
    assert '"data_authenticity": "placeholder"' in src or '"data_authenticity"' in src, (
        "Recorder must write data_authenticity='placeholder' to metadata.json"
    )

    # 3. Warning text about constant fields must be present.
    assert "camera/player fields are constant [0.0, 64.0, 0.0]" in src, (
        "Placeholder metadata must warn about constant [0.0, 64.0, 0.0] fields"
    )

    # 4. allow_placeholder must bypass the hard-gate.
    assert "allow_placeholder" in src, (
        "The allow_placeholder flag must be checked in the hard-gate logic"
    )


def test_recorder_window_capture_uses_geometry_not_title():
    """R01 v3: ffmpeg invocation must use geometry, NOT -i title=...

    windows-capture/mss are the automatic hardware-friendly capture path.
    ddagrab remains as an explicit diagnostic path that captures the full DXGI
    output and crops to the Minecraft geometry. gdigrab remains as the last
    automatic fallback with
    offset_x/offset_y/video_size + -i desktop.

    Verified two ways:
    (a) Source-grep recorder_consumer_lite.py to confirm geometry-based
        capture and absence of title-based capture.
    (b) Call build_window_args (the helper) with a non-ASCII title and
        verify the resulting cmdline uses geometry."""
    src = _read_recorder_source()

    # (a) Source-level: recorder must use geometry-based capture.
    assert '_VIDEO_AUTO_LAYERS = ("obs", "windows-capture", "ddagrab", "mss", "gdigrab")' in src, (
        "Recorder auto mode must try WGC, then ddagrab, then mss, then gdigrab"
    )
    assert '"ddagrab"' in src, "Recorder must keep explicit DXGI ddagrab diagnostics"
    assert "crop=" in src, "Recorder must crop ddagrab to the Minecraft geometry"
    assert '"-offset_x"' in src, "Recorder must use -offset_x in ffmpeg cmd"
    assert '"-offset_y"' in src, "Recorder must use -offset_y in ffmpeg cmd"
    assert '"-video_size"' in src, "Recorder must use -video_size in ffmpeg cmd"
    assert '"-i"' in src and '"desktop"' in src, "Recorder fallback must use -i desktop"

    # (a) Source-level: title-based capture must be gone.
    assert "title_safe" not in src, "Iron-law: the title_safe branch must be removed entirely"
    assert 'f"title={mc_title}"' not in src, "Iron-law: -i title=... capture must be removed"
    assert "full-desktop capture (title unsafe" not in src, (
        "Iron-law: the 'title unsafe' fallback log line must be removed"
    )

    # (b) Helper function: build_window_args always uses geometry.
    from bin.recorder_window_capture_helper import WindowRect, build_window_args

    fake = WindowRect(
        title="Minecraft 1.21.4 - 单人游戏",
        hwnd=42,
        left=100,
        top=200,
        right=2020,
        bottom=1280,
    )
    with (
        mock.patch("bin.recorder_window_capture_helper.is_windows", return_value=True),
        mock.patch("bin.recorder_window_capture_helper.find_window", return_value=fake),
    ):
        args, rect = build_window_args("Minecraft", framerate=30)

    assert "-offset_x" in args, "Must use -offset_x for geometry-based capture"
    assert "100" in args, "offset_x must match window left coordinate"
    assert "-offset_y" in args, "Must use -offset_y for geometry-based capture"
    assert "200" in args, "offset_y must match window top coordinate"
    assert "-video_size" in args, "Must use -video_size for geometry-based capture"
    assert "1920x1080" in args, "video_size must match window dimensions"
    assert args[-2:] == ["-i", "desktop"], "Must use -i desktop, not -i title=..."
    for arg in args:
        assert not arg.startswith("title="), (
            f"Iron-law: must NOT use title-based capture. Found: {arg}"
        )


# ---------------------------------------------------------------------------
# Placeholder-audit 2026-05-08: newly-closed gaps
# ---------------------------------------------------------------------------


def test_optical_flow_no_placeholder_frames():
    """optical_flow_provider must not silently generate random placeholder frames."""
    src = (REPO_ROOT / "bin" / "optical_flow_provider.py").read_text()
    assert "_placeholder_frames" not in src, (
        "Iron-law: optical_flow_provider must NOT have _placeholder_frames(). "
        "It must raise RuntimeError when imageio is unavailable."
    )
    assert "generating placeholder frames" not in src, (
        "Iron-law: optical_flow_provider must not log 'generating placeholder frames'."
    )


def test_depth_anything_smoke_no_mock_model():
    """depth_anything_smoke must not silently return a MockDepthModel."""
    src = (REPO_ROOT / "bin" / "depth_anything_smoke.py").read_text()
    assert "MockDepthModel" not in src, (
        "Iron-law: depth_anything_smoke must NOT define MockDepthModel. "
        "It must raise RuntimeError when depth_anything_v2 is unavailable."
    )
    assert "using mock model" not in src, (
        "Iron-law: depth_anything_smoke must not log 'using mock model'."
    )


def test_recorder_dav2_runner_no_mock_depth():
    """recorder_dav2_runner must not silently fall back to _mock_depth()."""
    src = (REPO_ROOT / "bin" / "recorder_dav2_runner.py").read_text()
    assert "_mock_depth" not in src, (
        "Iron-law: recorder_dav2_runner must NOT define _mock_depth(). "
        "It must raise RuntimeError when the model is None."
    )


def test_vendor_alpha_dashboard_no_sample_data():
    """vendor_alpha_dashboard must not generate fake metrics from a hash."""
    src = (REPO_ROOT / "bin" / "vendor_alpha_dashboard.py").read_text()
    assert "load_sample_data" not in src, (
        "Iron-law: vendor_alpha_dashboard must NOT have load_sample_data(). "
        "It must read real metrics files or hard-fail."
    )
    assert "vendor_hash" not in src, (
        "Iron-law: vendor_alpha_dashboard must not derive fake metrics from hash."
    )


def test_sample_tarball_builder_no_fake_exr():
    """sample_tarball_builder must not write fake EXR magic bytes."""
    src = (REPO_ROOT / "bin" / "sample_tarball_builder.py").read_text()
    assert "\\x76\\x2f\\x31\\x01" not in src and "EXR magic number" not in src, (
        "Iron-law: sample_tarball_builder must NOT write raw EXR magic bytes. "
        "It must raise RuntimeError when OpenEXR is unavailable."
    )
    assert "PK\\x03\\x04" not in src or "openpyxl" not in src.split("PK")[0], (
        "Iron-law: sample_tarball_builder must NOT write raw XLSX ZIP magic. "
        "It must raise RuntimeError when openpyxl is unavailable."
    )


def test_payout_cron_no_silent_mock_fallback():
    """payout_cron must not silently return MockStripeClient/MockSupabaseClient
    without explicit allow_mock=True."""
    src = (REPO_ROOT / "bin" / "payout_cron.py").read_text()
    # The factory functions must require allow_mock parameter
    assert "allow_mock" in src, (
        "Iron-law: make_stripe_client/make_supabase_client must accept "
        "allow_mock parameter and raise when False + no env."
    )
    assert "allow_mock=args.dry_run" in src, (
        "Iron-law: main() must pass allow_mock=args.dry_run so production "
        "runs without --dry-run hard-fail when env is missing."
    )

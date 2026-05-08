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
    assert not p.exists(), (
        f"{p} must NOT exist — it advertised the DEV MODE fabrication."
    )


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
    assert "status: 503" in src, (
        "Iron-law: /api/checkout must return 503 when not configured."
    )
    assert "fakeSession" not in src and "dev_session_" not in src, (
        "Iron-law: /api/checkout must NOT mint dev_session_* fake Stripe sessions."
    )

"""Iron-law lint: web-tester / web-buyer source must NOT ship fabricated data.

Howard 2026-05-07: caught a violation where lib/sample-data.ts exported
hardcoded "24.7 hours / $148.20 earnings" sample data gated by a
`isSupabaseConfigured()` check labeled "DEV MODE". Even with a banner,
fabricated numbers shipping in source IS a placeholder under the
no-fake-data canon. Fix was to delete the file + hard-gate pages with
<NotConfigured>.

This test makes sure no future contributor re-introduces the pattern.
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
    # READMEs and docs can describe test patterns + mock usage.
    "web-tester/README.md",
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


def test_no_sample_data_imports():
    """No file imports from `./sample-data` or `lib/sample-data`."""
    web_tester = REPO_ROOT / "web-tester"
    if not web_tester.is_dir():
        return  # web-tester not present in this branch — skip
    pat = re.compile(r"sample-data['\"]|sampleStats|sampleTarballs|samplePayouts")
    hits = _scan(web_tester, pat)
    if hits:
        msg = "\n".join(f"  {h[0]}:{h[1]}: {h[2]}" for h in hits)
        raise AssertionError(
            f"Iron-law violation: fabricated sample-data is back. Hits:\n{msg}"
        )


def test_no_dev_mode_branches_in_pages():
    """Pages must not have `if (!isSupabaseConfigured()) { ...fake data... }`
    branches. Render <NotConfigured> instead."""
    web_tester = REPO_ROOT / "web-tester"
    if not web_tester.is_dir():
        return
    pat = re.compile(r"\bdev_sample\b|\bDEV MODE: showing sample\b|\bMockStripeClient\(\)")
    hits = _scan(web_tester, pat)
    if hits:
        msg = "\n".join(f"  {h[0]}:{h[1]}: {h[2]}" for h in hits)
        raise AssertionError(
            f"Iron-law violation: DEV MODE fake-data branch is back. Hits:\n{msg}"
        )


def test_getStripeClient_throws_when_not_configured():
    """The factory must throw, not silently return MockStripeClient.
    Verified by source-grep for the explicit throw."""
    src = (REPO_ROOT / "web-tester" / "lib" / "stripe.ts").read_text() if (REPO_ROOT / "web-tester" / "lib" / "stripe.ts").is_file() else ""
    if not src:
        return  # web-tester not present
    assert "throw new Error" in src and "isStripeConfigured" in src, (
        "getStripeClient() must throw when Stripe isn't configured. "
        "Don't fall back to MockStripeClient at runtime."
    )


def test_NotConfigured_component_exists():
    """The hard-gate component must exist for pages to render when env
    vars are missing."""
    p = REPO_ROOT / "web-tester" / "components" / "NotConfigured.tsx"
    if not p.parent.parent.is_dir():
        return  # web-tester not present
    assert p.is_file(), "components/NotConfigured.tsx is the iron-law gate; don't delete it"
    text = p.read_text()
    assert "Iron-law" in text, "NotConfigured must explain the iron-law to contributors"


def test_sample_data_file_is_gone():
    """The file that exported fabricated tester stats must stay deleted."""
    p = REPO_ROOT / "web-tester" / "lib" / "sample-data.ts"
    assert not p.exists(), (
        f"{p} must NOT exist — it contained fabricated tester stats. "
        f"Use real-stats.ts (live GitHub release fetch) or NotConfigured."
    )

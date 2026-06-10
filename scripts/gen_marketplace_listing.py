#!/usr/bin/env python3
"""Generate buyer-facing marketplace listing from session sweep results.

Reads ``dashboard/sweep_summary.json`` (produced by S35 / real_session_validator)
and writes ``docs/MARKETPLACE_LISTING_v0.7.x.md`` with:

- Title: "Oyster GameData v0.7.x — X games, Y hours, Z sessions"
- Stats table (sessions count, avg duration, BUYER_READY %)
- Pricing ($X per session)
- Sample data download link (placeholder)
- Provenance verify quickstart
- Contact

Usage:
    python3 scripts/gen_marketplace_listing.py
    python3 scripts/gen_marketplace_listing.py --input dashboard/sweep_summary.json
    python3 scripts/gen_marketplace_listing.py --version 0.7.3
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "dashboard" / "sweep_summary.json"
DEFAULT_OUTPUT = ROOT / "docs" / "MARKETPLACE_LISTING_v0.7.x.md"

# ---------------------------------------------------------------------------
# Pricing constants
# ---------------------------------------------------------------------------
PRICE_PER_SESSION_USD = 12  # placeholder — not a pricing engine

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_sweep(path: Path) -> dict:
    """Load and return the sweep_summary.json dict."""
    if not path.exists():
        raise FileNotFoundError(f"Sweep summary not found: {path}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Computation helpers
# ---------------------------------------------------------------------------


def _count_games(sessions: list[dict]) -> int:
    """Return the number of distinct games in the session list."""
    return len({s.get("game", "unknown") for s in sessions})


def _total_hours(sessions: list[dict]) -> float:
    """Return total session duration in hours."""
    total_seconds = sum(s.get("duration_s", 0) for s in sessions)
    return total_seconds / 3600.0


def _avg_duration_minutes(sessions: list[dict]) -> float:
    """Return average session duration in minutes."""
    if not sessions:
        return 0.0
    total_seconds = sum(s.get("duration_s", 0) for s in sessions)
    return (total_seconds / len(sessions)) / 60.0


def _buyer_ready_pct(sessions: list[dict]) -> float:
    """Return the percentage of sessions with BUYER_READY label."""
    if not sessions:
        return 0.0
    ready = sum(1 for s in sessions if s.get("buyer_label") == "BUYER_READY")
    return ready / len(sessions) * 100.0


def _total_price(sessions: list[dict]) -> float:
    """Return total price for all sessions."""
    return len(sessions) * PRICE_PER_SESSION_USD


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------


def generate_listing(
    sweep: dict,
    version: str = "0.7.x",
    price_per_session: float = PRICE_PER_SESSION_USD,
) -> str:
    """Generate the full marketplace listing markdown string.

    Parameters
    ----------
    sweep : dict
        Parsed sweep_summary.json content.
    version : str
        Version string for the listing title.
    price_per_session : float
        Price per session in USD.

    Returns
    -------
    str
        Complete markdown document.
    """
    sessions = sweep.get("sessions", [])
    summary = sweep.get("summary", {})
    evaluated = sweep.get("evaluated", len(sessions))
    total_found = sweep.get("total_found", len(sessions))

    num_games = _count_games(sessions)
    total_hours = _total_hours(sessions)
    avg_dur_min = _avg_duration_minutes(sessions)
    buyer_ready_pct = _buyer_ready_pct(sessions)
    total_price = evaluated * price_per_session

    buyer_ready_count = summary.get("BUYER_READY", 0)
    degraded_count = summary.get("DEGRADED", 0)
    fail_count = summary.get("FAIL", 0)
    pass_rate = summary.get("pass_rate_pct", 0)

    sweep_started = sweep.get("sweep_started", "N/A")
    sweep_finished = sweep.get("sweep_finished", "N/A")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ------------------------------------------------------------------
    # Build markdown
    # ------------------------------------------------------------------
    lines: list[str] = []

    # --- Section 1: Title & Overview ---
    lines.append(
        f"# Oyster GameData v{version} — {num_games} games, "
        f"{total_hours:.1f} hours, {evaluated} sessions"
    )
    lines.append("")
    lines.append(
        f"> **Generated:** {generated_at}  |  "
        f"**Sweep window:** {sweep_started} → {sweep_finished}  |  "
        f"**Sessions found:** {total_found}  |  "
        f"**Evaluated:** {evaluated}"
    )
    lines.append("")

    # --- Section 2: Stats Table ---
    lines.append("## 📊 Session Statistics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total sessions evaluated | {evaluated} |")
    lines.append(f"| Distinct games | {num_games} |")
    lines.append(f"| Total recording time | {total_hours:.1f} hours |")
    lines.append(f"| Avg session duration | {avg_dur_min:.1f} minutes |")
    lines.append(
        f"| BUYER_READY sessions | {buyer_ready_count} ({buyer_ready_pct:.0f}%) |"
    )
    lines.append(f"| DEGRADED sessions | {degraded_count} |")
    lines.append(f"| FAIL sessions | {fail_count} |")
    lines.append(f"| Overall pass rate | {pass_rate}% |")
    lines.append("")

    # --- Section 3: Pricing ---
    lines.append("## 💰 Pricing")
    lines.append("")
    lines.append("| Item | Price |")
    lines.append("|------|-------|")
    lines.append(f"| Per session | ${price_per_session:.2f} USD |")
    lines.append(f"| Full dataset ({evaluated} sessions) | ${total_price:.2f} USD |")
    lines.append("")
    lines.append(
        "> *Pricing is a placeholder. Contact us for volume discounts and custom bundles.*"
    )
    lines.append("")

    # --- Section 4: Sample Data Download ---
    lines.append("## 📥 Sample Data Download")
    lines.append("")
    lines.append(
        "A representative sample of **5 BUYER_READY sessions** is available for evaluation:"
    )
    lines.append("")
    lines.append(
        f"- **Download:** [sample_data_v{version}.tar.gz](#) _(placeholder — link TBD)_"
    )
    lines.append(
        f"- **Size:** ~{(total_hours / max(evaluated, 1) * 5 * 150):.0f} MB (estimated)"
    )
    lines.append(
        "- **Contents:** recording.mp4, game_state.jsonl, MANIFEST.signed.json per session"
    )
    lines.append("")

    # --- Section 5: Provenance Verify Quickstart ---
    lines.append("## 🔐 Provenance Verify Quickstart")
    lines.append("")
    lines.append(
        "Every session includes an Ed25519-signed manifest. Verify integrity with:"
    )
    lines.append("")
    lines.append("```bash")
    lines.append("# 1. Install dependencies")
    lines.append("pip install nacl")
    lines.append("")
    lines.append("# 2. Verify a single session manifest")
    lines.append("python3 bin/provenance_verify.py path/to/MANIFEST.signed.json")
    lines.append("")
    lines.append("# 3. Verify all sessions in a batch")
    lines.append("for f in sample_data_v*/MANIFEST.signed.json; do")
    lines.append(
        '  python3 bin/provenance_verify.py "$f" && echo "✓ $f" || echo "✗ $f"'
    )
    lines.append("done")
    lines.append("```")
    lines.append("")
    lines.append(
        "All manifests are signed with the Oyster project Ed25519 key. "
        "The public key fingerprint is published in `docs/PROVENANCE.md`."
    )
    lines.append("")

    # --- Section 6: Contact ---
    lines.append("## 📬 Contact")
    lines.append("")
    lines.append("| Channel | Link |")
    lines.append("|---------|------|")
    lines.append("| Email | [data@oyster.gg](mailto:data@oyster.gg) |")
    lines.append("| Discord | [Oyster Discord](#) _(placeholder)_) |")
    lines.append("| GitHub | [github.com/oyster-ai](https://github.com/oyster-ai) |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        f"*This listing was auto-generated by `scripts/gen_marketplace_listing.py` "
        f"at {generated_at}.*"
    )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate buyer-facing marketplace listing from sweep results."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Path to sweep_summary.json (default: dashboard/sweep_summary.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output markdown path (default: docs/MARKETPLACE_LISTING_v0.7.x.md)",
    )
    parser.add_argument(
        "--version",
        type=str,
        default="0.7.x",
        help="Version string for the listing title (default: 0.7.x)",
    )
    parser.add_argument(
        "--price",
        type=float,
        default=PRICE_PER_SESSION_USD,
        help=f"Price per session in USD (default: {PRICE_PER_SESSION_USD})",
    )
    args = parser.parse_args(argv)

    try:
        sweep = load_sweep(args.input)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"ERROR: Invalid JSON in {args.input}: {exc}", file=sys.stderr)
        return 1

    content = generate_listing(
        sweep, version=args.version, price_per_session=args.price
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(f"✓ Marketplace listing written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

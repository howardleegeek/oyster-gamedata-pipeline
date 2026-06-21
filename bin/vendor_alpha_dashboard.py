#!/usr/bin/env python3
"""
vendor_alpha_dashboard.py

Local CLI showing single-vendor daily metrics during alpha phase.
Displays sales, orders, revenue, and customer engagement metrics.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path
from typing import Any


def load_vendor_metrics(vendor_id: str, date: str) -> dict[str, Any]:
    """
    Load real metrics for a vendor on a given date.

    Reads from a local JSON metrics file at
    ``metrics/<vendor_id>/<date>.json``.  If the file does not exist,
    hard-fails with an actionable error.

    Iron-law: never generate fake/sample metrics from a hash.
    """
    metrics_path = Path(f"metrics/{vendor_id}/{date}.json")
    if not metrics_path.is_file():
        raise FileNotFoundError(
            f"No metrics file at {metrics_path}. "
            f"Vendor '{vendor_id}' has no recorded data for {date}. "
            f"Iron-law: never generate synthetic sample data. "
            f"Run the metrics collector first, or verify the vendor_id and date."
        )
    import json as _json

    return _json.loads(metrics_path.read_text(encoding="utf-8"))


def format_currency(amount: float) -> str:
    """Format amount as USD currency string."""
    return f"${amount:,.2f}"


def format_percentage(value: float) -> str:
    """Format value as percentage string."""
    return f"{value * 100:.1f}%"


def format_number(value: int) -> str:
    """Format integer with thousand separators."""
    return f"{value:,}"


def render_dashboard(metrics: dict[str, Any]) -> str:
    """
    Render metrics as a text-based dashboard.

    Args:
        metrics: Dictionary of vendor metrics

    Returns:
        Formatted dashboard string
    """
    lines = []
    width = 50

    # Header
    lines.append("=" * width)
    lines.append("  VENDOR ALPHA DASHBOARD".center(width))
    lines.append(f"  Vendor: {metrics['vendor_id']}".center(width))
    lines.append(f"  Date: {metrics['date']}".center(width))
    lines.append("=" * width)
    lines.append("")

    # Key Metrics Section
    lines.append("┌─ KEY METRICS ─────────────────────────────────────┐")

    revenue = metrics["revenue"]
    orders = metrics["orders"]
    customers = metrics["customers"]
    aov = metrics["avg_order_value"]

    lines.append(f"│  Revenue:      {format_currency(revenue):>20}  │")
    lines.append(f"│  Orders:       {format_number(orders):>20}  │")
    lines.append(f"│  Customers:    {format_number(customers):>20}  │")
    lines.append(f"│  Avg Order:    {format_currency(aov):>20}  │")
    lines.append("└───────────────────────────────────────────────────┘")
    lines.append("")

    # Performance Section
    lines.append("┌─ PERFORMANCE ─────────────────────────────────────┐")
    lines.append(f"│  Return Rate:  {format_percentage(metrics['return_rate']):>20}  │")
    lines.append(f"│  Active Prod:  {format_number(metrics['active_products']):>20}  │")
    lines.append("└───────────────────────────────────────────────────┘")
    lines.append("")

    # Footer
    lines.append("─" * width)
    lines.append("  Report generated for alpha testing purposes only.")
    lines.append("─" * width)

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """
    Main entry point for the vendor alpha dashboard CLI.

    Args:
        argv: Command line arguments (defaults to sys.argv)

    Returns:
        Exit code (0 for success, non-zero for errors)
    """
    parser = argparse.ArgumentParser(
        prog="vendor_alpha_dashboard",
        description="Show daily metrics for a single vendor during alpha phase.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  vendor_alpha_dashboard --vendor V001
  vendor_alpha_dashboard --vendor V001 --date 2024-01-15
  vendor_alpha_dashboard --vendor V001 --output dashboard.txt
  vendor_alpha_dashboard --vendor V001 --quiet
""",
    )

    parser.add_argument("--vendor", "-v", required=True, help="Vendor ID to display metrics for")
    parser.add_argument(
        "--date",
        "-d",
        default=datetime.date.today().isoformat(),
        help="Date for metrics (YYYY-MM-DD, default: today)",
    )
    parser.add_argument("--output", "-o", default=None, help="Path to write dashboard output")
    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress stdout output (useful with --output)"
    )

    args = parser.parse_args(argv)

    # Validate date format
    try:
        datetime.datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print(f"Error: Invalid date format '{args.date}'. Use YYYY-MM-DD.", file=sys.stderr)
        return 1

    # Load metrics
    try:
        metrics = load_vendor_metrics(args.vendor, args.date)
    except Exception as e:
        print(f"Error loading metrics: {e}", file=sys.stderr)
        return 1

    # Render dashboard
    dashboard = render_dashboard(metrics)

    # Handle output
    if args.output:
        try:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(dashboard + "\n")
            if not args.quiet:
                print(f"Dashboard written to: {args.output}")
        except OSError as e:
            print(f"Error writing output: {e}", file=sys.stderr)
            return 1
    else:
        print(dashboard)

    return 0


if __name__ == "__main__":
    sys.exit(main())

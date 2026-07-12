#!/usr/bin/env python3
"""
G014 · bin/cost_estimator.py
Daily S3 cost report (storage + egress + lifecycle stage projection).
"""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class StorageClass(Enum):
    STANDARD = "STANDARD"
    STANDARD_IA = "STANDARD_IA"
    ONEZONE_IA = "ONEZONE_IA"
    GLACIER = "GLACIER"
    DEEP_ARCHIVE = "DEEP_ARCHIVE"
    INTELLIGENT_TIERING = "INTELLIGENT_TIERING"


# AWS S3 pricing (USD per GB-month)
STORAGE_PRICING: Dict[StorageClass, float] = {
    StorageClass.STANDARD: 0.023,
    StorageClass.STANDARD_IA: 0.0125,
    StorageClass.ONEZONE_IA: 0.01,
    StorageClass.GLACIER: 0.004,
    StorageClass.DEEP_ARCHIVE: 0.00099,
    StorageClass.INTELLIGENT_TIERING: 0.023,
}

# Egress pricing tiers (USD per GB): (limit_gb, price)
EGRESS_PRICING = [(1, 0.00), (10, 0.09), (50, 0.085), (150, 0.07), (float('inf'), 0.05)]

# Lifecycle thresholds (days -> storage class)
LIFECYCLE_THRESHOLDS = {
    30: StorageClass.STANDARD_IA,
    90: StorageClass.GLACIER,
    180: StorageClass.DEEP_ARCHIVE,
}


@dataclass
class StorageMetrics:
    bucket_name: str
    size_gb: float
    object_count: int
    storage_class: StorageClass = StorageClass.STANDARD
    egress_gb: float = 0.0
    avg_object_age_days: int = 0


def calculate_storage_cost(size_gb: float, storage_class: StorageClass) -> float:
    """Calculate monthly storage cost."""
    return size_gb * STORAGE_PRICING.get(storage_class, STORAGE_PRICING[StorageClass.STANDARD])


def calculate_egress_cost(egress_gb: float) -> float:
    """Calculate egress cost based on tiered pricing."""
    if egress_gb <= 0:
        return 0.0
    total_cost, remaining, prev_limit = 0.0, egress_gb, 0.0
    for limit, price in EGRESS_PRICING:
        if remaining <= 0:
            break
        tier_size = min(limit - prev_limit, remaining)
        total_cost += tier_size * price
        remaining -= tier_size
        prev_limit = limit
    return total_cost


def project_lifecycle_stage(metrics: StorageMetrics) -> Dict[str, Any]:
    """Recommend lifecycle transition based on object age."""
    current_cost = calculate_storage_cost(metrics.size_gb, metrics.storage_class)
    recommended = metrics.storage_class
    for threshold, storage_cls in sorted(LIFECYCLE_THRESHOLDS.items()):
        if metrics.avg_object_age_days > threshold:
            recommended = storage_cls
    projected_cost = calculate_storage_cost(metrics.size_gb, recommended)
    return {
        "current_class": metrics.storage_class.value,
        "recommended_class": recommended.value,
        "current_monthly_cost_usd": round(current_cost, 4),
        "projected_monthly_cost_usd": round(projected_cost, 4),
        "monthly_savings_usd": round(current_cost - projected_cost, 4),
    }


def generate_cost_report(metrics: StorageMetrics) -> Dict[str, Any]:
    """Generate comprehensive cost report."""
    storage_cost = calculate_storage_cost(metrics.size_gb, metrics.storage_class)
    egress_cost = calculate_egress_cost(metrics.egress_gb)
    return {
        "report_date": datetime.utcnow().isoformat() + "Z",
        "bucket": metrics.bucket_name,
        "metrics": {
            "size_gb": metrics.size_gb,
            "object_count": metrics.object_count,
            "storage_class": metrics.storage_class.value,
            "egress_gb": metrics.egress_gb,
            "avg_object_age_days": metrics.avg_object_age_days,
        },
        "cost_breakdown": {
            "storage_monthly_usd": round(storage_cost, 4),
            "egress_monthly_usd": round(egress_cost, 4),
            "total_monthly_usd": round(storage_cost + egress_cost, 4),
        },
        "lifecycle_projection": project_lifecycle_stage(metrics),
    }


def print_report(report: Dict[str, Any], output_format: str = "text") -> None:
    """Print report in specified format."""
    if output_format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(f"\n{'='*50}")
        print(f"S3 Cost Report - {report['bucket']}")
        print(f"{'='*50}")
        print(f"Report Date: {report['report_date']}")
        m = report['metrics']
        print("\n--- Storage Metrics ---")
        print(f"  Size:           {m['size_gb']:.2f} GB")
        print(f"  Objects:        {m['object_count']:,}")
        print(f"  Storage Class:  {m['storage_class']}")
        print(f"  Egress:         {m['egress_gb']:.2f} GB")
        print(f"  Avg Age:        {m['avg_object_age_days']} days")
        c = report['cost_breakdown']
        print("\n--- Cost Breakdown (Monthly) ---")
        print(f"  Storage:        ${c['storage_monthly_usd']:.4f}")
        print(f"  Egress:         ${c['egress_monthly_usd']:.4f}")
        print(f"  TOTAL:          ${c['total_monthly_usd']:.4f}")
        lp = report['lifecycle_projection']
        print("\n--- Lifecycle Projection ---")
        print(f"  Current Class:      {lp['current_class']}")
        print(f"  Recommended Class:  {lp['recommended_class']}")
        print(f"  Potential Savings:  ${lp['monthly_savings_usd']:.4f}/month")
        print(f"{'='*50}\n")


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily S3 cost report")
    parser.add_argument("--bucket", "-b", required=True, help="S3 bucket name")
    parser.add_argument("--size-gb", "-s", type=float, required=True, help="Storage size in GB")
    parser.add_argument("--objects", "-o", type=int, required=True, help="Number of objects")
    parser.add_argument(
        "--storage-class",
        "-c",
        default="STANDARD",
        choices=[sc.value for sc in StorageClass],
        help="S3 storage class",
    )
    parser.add_argument(
        "--egress-gb",
        "-e",
        type=float,
        default=0.0,
        help="Egress data transfer in GB",
    )
    parser.add_argument(
        "--avg-age-days",
        "-a",
        type=int,
        default=0,
        help="Average object age in days",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["text", "json"],
        default="text",
        help="Output format",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point."""
    if argv is None:
        argv = sys.argv[1:]
    args = parse_args(argv)
    metrics = StorageMetrics(
        bucket_name=args.bucket,
        size_gb=args.size_gb,
        object_count=args.objects,
        storage_class=StorageClass(args.storage_class),
        egress_gb=args.egress_gb,
        avg_object_age_days=args.avg_age_days,
    )
    print_report(generate_cost_report(metrics), args.format)
    return 0


if __name__ == "__main__":
    sys.exit(main())

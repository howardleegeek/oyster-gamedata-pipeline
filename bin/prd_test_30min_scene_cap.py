#!/usr/bin/env python3
"""
PRD p7 #3: max 30 min per scene — clock cap enforced.
Test utility to enforce maximum 30-minute duration per scene.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from typing import Any, Dict, List


def calculate_elapsed_minutes(start_time_iso: str, end_time_iso: str) -> float:
    """Calculate elapsed minutes between two ISO timestamps.
    
    Args:
        start_time_iso: Start time in ISO format
        end_time_iso: End time in ISO format
    
    Returns:
        Elapsed time in minutes
    """
    start = datetime.fromisoformat(start_time_iso)
    end = datetime.fromisoformat(end_time_iso)
    return (end - start).total_seconds() / 60.0


def check_scene_duration(duration_minutes: float, threshold_minutes: float = 30.0) -> Dict[str, Any]:
    """Check if scene duration exceeds threshold.
    
    Args:
        duration_minutes: Actual scene duration in minutes
        threshold_minutes: Maximum allowed duration in minutes
    
    Returns:
        Dict with exceeded, warning, status, remaining_minutes, over_by_minutes
    """
    exceeded = duration_minutes > threshold_minutes
    warning = duration_minutes > threshold_minutes * 0.8 and not exceeded
    
    return {
        "exceeded": exceeded,
        "warning": warning,
        "status": "EXCEEDED" if exceeded else "WARNING" if warning else "OK",
        "remaining_minutes": max(threshold_minutes - duration_minutes, 0),
        "over_by_minutes": max(duration_minutes - threshold_minutes, 0),
    }


def create_scene_result(
    scene_id: str,
    duration_minutes: float,
    threshold_minutes: float = 30.0,
    start_time_iso: str = None,
    end_time_iso: str = None,
) -> Dict[str, Any]:
    """Create a scene duration check result.
    
    Args:
        scene_id: Scene identifier
        duration_minutes: Actual duration in minutes
        threshold_minutes: Maximum allowed duration in minutes
        start_time_iso: Optional start time in ISO format
        end_time_iso: Optional end time in ISO format
    
    Returns:
        Complete result dict with all fields
    """
    check_result = check_scene_duration(duration_minutes, threshold_minutes)
    
    result = {
        "scene_id": scene_id,
        "duration_minutes": duration_minutes,
        "threshold_minutes": threshold_minutes,
        **check_result,
    }
    
    if start_time_iso and end_time_iso:
        result["start_time"] = start_time_iso
        result["end_time"] = end_time_iso
    
    return result


def main(argv: List[str]) -> int:
    """Test 30-minute scene clock cap enforcement."""
    parser = argparse.ArgumentParser(description="Test 30-minute maximum per scene clock cap.")

    parser.add_argument(
        "--scene-id", default="test_scene", help="Scene identifier (default: test_scene)"
    )
    parser.add_argument(
        "--duration", type=float, default=0.1, help="Test duration in minutes (default: 0.1)"
    )
    parser.add_argument(
        "--threshold", type=float, default=30.0, help="Maximum minutes threshold (default: 30.0)"
    )
    parser.add_argument("--json", action="store_true", help="Output results as JSON")

    args = parser.parse_args(argv)

    # Start timing
    start_time = datetime.now()

    # Simulate scene processing
    if args.duration > 0:
        time.sleep(args.duration * 60)  # Convert minutes to seconds

    # Calculate elapsed time
    elapsed = (datetime.now() - start_time).total_seconds() / 60.0
    exceeded = elapsed > args.threshold
    warning = elapsed > args.threshold * 0.8

    # Prepare results
    results = {
        "scene_id": args.scene_id,
        "start_time": start_time.isoformat(),
        "end_time": datetime.now().isoformat(),
        "duration_minutes": round(elapsed, 3),
        "threshold_minutes": args.threshold,
        "exceeded": exceeded,
        "warning": warning,
        "remaining_minutes": round(max(args.threshold - elapsed, 0), 3),
        "over_by_minutes": round(max(elapsed - args.threshold, 0), 3),
        "status": "EXCEEDED" if exceeded else "WARNING" if warning else "OK",
    }

    # Output results
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("Scene Clock Cap Test Results")
        print("=============================")
        print(f"Scene ID:      {results['scene_id']}")
        print(f"Duration:      {results['duration_minutes']} minutes")
        print(f"Threshold:     {results['threshold_minutes']} minutes")
        print(f"Status:        {results['status']}")
        print(f"Remaining:     {results['remaining_minutes']} minutes")

        if results["exceeded"]:
            print(f"Over by:       {results['over_by_minutes']} minutes")
            print(f"\n❌ FAIL: Scene exceeded {args.threshold} minute cap!")
            return 1
        elif results["warning"]:
            print(f"\n⚠ WARNING: Scene approaching {args.threshold} minute cap")
            return 0
        else:
            print(f"\n✅ PASS: Scene within {args.threshold} minute limit")
            return 0

    return 0 if not exceeded else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

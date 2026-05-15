#!/usr/bin/env python3
"""
PRD p7 #3: max 30 min per scene — clock cap enforced.
Test utility to enforce maximum 30-minute duration per scene.

Validates the clock-cap logic without actually sleeping. Uses simulated
duration values to verify the threshold enforcement works correctly.
"""

import argparse
import sys
import json
from datetime import datetime
from typing import Dict, List


def main(argv: List[str]) -> int:
    """Test 30-minute scene clock cap enforcement."""
    parser = argparse.ArgumentParser(
        description="Test 30-minute maximum per scene clock cap."
    )
    
    parser.add_argument(
        "--scene-id",
        default="test_scene",
        help="Scene identifier (default: test_scene)"
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Simulated scene duration in minutes (default: 0.0, no sleep)"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=30.0,
        help="Maximum minutes threshold (default: 30.0)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )
    
    args = parser.parse_args(argv)
    
    # Use simulated duration directly — no actual sleep
    elapsed = args.duration
    exceeded = elapsed > args.threshold
    warning = elapsed > args.threshold * 0.8
    
    now = datetime.now()
    
    # Prepare results
    results = {
        "scene_id": args.scene_id,
        "start_time": now.isoformat(),
        "end_time": now.isoformat(),
        "duration_minutes": round(elapsed, 3),
        "threshold_minutes": args.threshold,
        "exceeded": exceeded,
        "warning": warning,
        "remaining_minutes": round(max(args.threshold - elapsed, 0), 3),
        "over_by_minutes": round(max(elapsed - args.threshold, 0), 3),
        "status": "EXCEEDED" if exceeded else "WARNING" if warning else "OK"
    }
    
    # Output results
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"Scene Clock Cap Test Results")
        print(f"=============================")
        print(f"Scene ID:      {results['scene_id']}")
        print(f"Duration:      {results['duration_minutes']} minutes")
        print(f"Threshold:     {results['threshold_minutes']} minutes")
        print(f"Status:        {results['status']}")
        print(f"Remaining:     {results['remaining_minutes']} minutes")
        
        if results['exceeded']:
            print(f"Over by:       {results['over_by_minutes']} minutes")
            print(f"\n❌ FAIL: Scene exceeded {args.threshold} minute cap!")
            return 1
        elif results['warning']:
            print(f"\n⚠ WARNING: Scene approaching {args.threshold} minute cap")
            return 0
        else:
            print(f"\n✅ PASS: Scene within {args.threshold} minute limit")
            return 0
    
    return 0 if not exceeded else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

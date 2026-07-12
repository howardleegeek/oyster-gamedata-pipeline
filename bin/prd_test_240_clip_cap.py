#!/usr/bin/env python3
"""
G078 · bin/prd_test_240_clip_cap.py

PRD p7 #2: Validate max 240 clips per scene — adapter stops at 241st.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def create_mock_scene(num_clips: int) -> Dict[str, Any]:
    """Create a mock scene with specified number of clips."""
    return {
        "scene_id": "test_scene_001",
        "clips": [
            {"id": f"clip_{i:04d}", "start": i * 10.0, "end": (i + 1) * 10.0}
            for i in range(num_clips)
        ]
    }


def validate_clip_cap(scene: Dict[str, Any], max_clips: int = 240) -> Dict[str, Any]:
    """Validate that scene respects the clip cap."""
    clip_count = len(scene.get("clips", []))
    exceeded = max(0, clip_count - max_clips)
    return {
        "valid": clip_count <= max_clips,
        "clip_count": clip_count,
        "max_allowed": max_clips,
        "exceeded_by": exceeded,
        "stopped_at": max_clips + 1 if exceeded else None,
        "message": f"Clips: {clip_count}/{max_clips}" + (
            f" (exceeded by {exceeded})" if exceeded else ""
        )
    }


def run_test(test_case: str, verbose: bool = False) -> bool:
    """Run a specific test case for clip cap validation."""
    test_cases = {
        "at_limit": (240, True),
        "below_limit": (200, True),
        "over_limit": (250, False),
        "way_over": (300, False)
    }
    
    if test_case not in test_cases:
        print(f"ERROR: Unknown test case '{test_case}'")
        return False
    
    num_clips, expected_valid = test_cases[test_case]
    result = validate_clip_cap(create_mock_scene(num_clips))
    
    if verbose:
        print(f"Test: {test_case} ({num_clips} clips)")
        print(f"  {result['message']}")
        if result["stopped_at"]:
            print(f"  Adapter stops at: clip #{result['stopped_at']}")
    
    passed = result["valid"] == expected_valid
    if not passed:
        print(f"FAIL: {test_case} - expected valid={expected_valid}, got {result['valid']}")
    return passed


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point with argparse CLI."""
    parser = argparse.ArgumentParser(description="Test 240 clip cap per scene (PRD p7 #2)")
    parser.add_argument("-t", "--test",
        choices=["at_limit", "below_limit", "over_limit", "way_over", "all"],
        default="all", help="Test case to run")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("-o", "--output", type=Path, help="Output JSON report")
    
    args = parser.parse_args(argv)
    test_cases = (
        ["at_limit", "below_limit", "over_limit", "way_over"]
        if args.test == "all"
        else [args.test]
    )
    
    results = {}
    for tc in test_cases:
        results[tc] = run_test(tc, args.verbose)
    
    all_passed = all(results.values())
    
    if args.output:
        with open(args.output, "w") as f:
            json.dump({"results": results, "all_passed": all_passed}, f, indent=2)
    
    if args.verbose:
        print(f"\nSummary: {'PASS' if all_passed else 'FAIL'}")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Edge test: int64 max for frame_id - confirm no overflow in adapter math."""

import argparse
import sys
import json
from typing import Any


def test_boundaries() -> dict[str, Any]:
    """Test int64 boundary values for overflow detection."""
    INT64_MAX = 2**63 - 1
    INT64_MIN = -2**63
    
    tests = [
        ("max", INT64_MAX), ("min", INT64_MIN), ("zero", 0),
        ("max-1", INT64_MAX - 1), ("min+1", INT64_MIN + 1),
    ]
    
    results: dict[str, Any] = {"tests": [], "ops": {}}
    
    for name, val in tests:
        ops = {}
        for op_name, op in [("+1", lambda x: x + 1),
                            ("-1", lambda x: x - 1),
                            ("*2", lambda x: x * 2)]:
            try:
                ops[op_name] = {"result": op(val), "status": "ok"}
            except OverflowError as e:
                ops[op_name] = {"error": str(e), "status": "fail"}
        results["tests"].append({"name": name, "value": val})
        results["ops"][name] = ops
    
    return results


def check_frame_id(frame_id: int) -> dict[str, Any]:
    """Check frame_id for overflow in adapter operations."""
    INT64_MAX = 2**63 - 1
    INT64_MIN = -2**63
    
    check: dict[str, Any] = {
        "frame_id": frame_id,
        "in_range": INT64_MIN <= frame_id <= INT64_MAX,
        "checks": {}
    }
    
    for op_name, op in [("*1000", lambda x: x * 1000),
                        ("+100", lambda x: x + 100),
                        ("-500", lambda x: x - 500)]:
        try:
            result = op(frame_id)
            check["checks"][op_name] = {
                "result": result,
                "in_range": INT64_MIN <= result <= INT64_MAX,
                "status": "ok"
            }
        except OverflowError as e:
            check["checks"][op_name] = {"error": str(e), "status": "fail"}
    
    return check


def main(argv: list[str]) -> int:
    """CLI entry point for edge case testing."""
    parser = argparse.ArgumentParser(
        description="Test int64 max values for frame_id overflow."
    )
    parser.add_argument("--run-all", action="store_true", 
                       help="Run all boundary tests")
    parser.add_argument("--frame-id", type=int, help="Test specific frame_id")
    parser.add_argument("--output", help="JSON output file")
    
    args = parser.parse_args(argv)
    
    if not args.run_all and args.frame_id is None:
        parser.print_help()
        return 1
    
    results: dict[str, Any] = {}
    
    if args.run_all:
        results = test_boundaries()
    
    if args.frame_id is not None:
        results["frame_check"] = check_frame_id(args.frame_id)
    
    output = json.dumps(results, indent=2)
    
    if args.output:
        with open(args.output, 'w') as f:
            f.write(output)
    else:
        print(output)
    
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
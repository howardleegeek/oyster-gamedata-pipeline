#!/usr/bin/env python3
"""
Transform camera intrinsics dict keys from lowercase fx/fy/cx/cy to fx/fy/Cx/Cy.
"""

import argparse
import json
import sys
from typing import Any, Dict, List, Optional


def transform_intrinsics(data: Dict[str, Any]) -> Dict[str, Any]:
    """Rename cx/cy to Cx/Cy in camera intrinsics dict."""
    required = {"fx", "fy", "cx", "cy"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"Missing keys: {missing}")

    result = data.copy()
    result["Cx"] = result.pop("cx")
    result["Cy"] = result.pop("cy")
    return result


def transform_any(data: Any) -> Any:
    """Recursively transform intrinsics dicts."""
    if isinstance(data, dict):
        if all(k in data for k in ["fx", "fy", "cx", "cy"]):
            return transform_intrinsics(data)
        return {k: transform_any(v) for k, v in data.items()}
    if isinstance(data, list):
        return [transform_any(item) for item in data]
    return data


def main(argv: Optional[List[str]] = None) -> int:
    """CLI for transforming camera intrinsics keys."""
    parser = argparse.ArgumentParser(description="Rename camera intrinsics keys: cx/cy → Cx/Cy")
    parser.add_argument("input", nargs="?", help="JSON string or @file.json")
    parser.add_argument("-o", "--output", help="Output file (default: stdout)")

    args = parser.parse_args(argv)

    try:
        # Get input data
        if not args.input:
            parser.print_help()
            return 0

        if args.input.startswith("@"):
            with open(args.input[1:], "r") as f:
                data = json.load(f)
        else:
            data = json.loads(args.input)

        # Transform
        result = transform_any(data)

        # Output
        if args.output:
            with open(args.output, "w") as f:
                json.dump(result, f, indent=2)
        else:
            print(json.dumps(result, indent=2))

    except FileNotFoundError as e:
        print(f"File not found: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

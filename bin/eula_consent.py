#!/usr/bin/env python3
"""Interactive EULA presenter logging vendor accept timestamp."""

import argparse
import json
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def read_eula(path: str) -> str:
    """Read EULA content from a text file."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def present_eula(text: str, width: int = 80) -> bool:
    """Display EULA and prompt for acceptance."""
    border = "=" * width
    print(f"\n{border}\nEND USER LICENSE AGREEMENT (EULA)\n{border}\n")
    print(textwrap.fill(text, width=width))
    print(f"\n{border}")
    
    while True:
        try:
            resp = input("\nDo you accept this EULA? (yes/no): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nInput interrupted.", file=sys.stderr)
            return False
            
        if resp in ("yes", "y"):
            return True
        if resp in ("no", "n"):
            return False
        print("Please answer 'yes' or 'no'.")


def log_acceptance(output: str, eula: str, accepted: bool, meta: Optional[dict] = None) -> None:
    """Write acceptance record as JSON."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eula_file": str(Path(eula).resolve()),
        "accepted": accepted,
        "metadata": meta or {},
    }
    
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    
    print(f"\nConsent record written to: {output}")


def main(argv: Optional[list] = None) -> int:
    """CLI entry point. Returns 0 on accept, 2 on reject, 1 on error."""
    parser = argparse.ArgumentParser(
        description="Interactive EULA presenter logging vendor accept timestamp."
    )
    parser.add_argument("--eula", required=True, help="Path to EULA text file")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--metadata", default="{}", help="Extra JSON metadata")
    parser.add_argument("--width", type=int, default=80, help="Display width")
    parser.add_argument("--non-interactive", action="store_true", help="Auto-accept")
    args = parser.parse_args(argv)
    
    try:
        eula_text = read_eula(args.eula)
    except Exception as e:
        print(f"Error reading EULA: {e}", file=sys.stderr)
        return 1
    
    try:
        metadata = json.loads(args.metadata)
        if not isinstance(metadata, dict):
            print("Error: --metadata must be a JSON object", file=sys.stderr)
            return 1
    except json.JSONDecodeError as e:
        print(f"Error: invalid --metadata JSON: {e}", file=sys.stderr)
        return 1
    
    accepted = args.non_interactive or present_eula(eula_text, args.width)
    
    try:
        log_acceptance(args.output, args.eula, accepted, metadata)
    except Exception as e:
        print(f"Error writing consent record: {e}", file=sys.stderr)
        return 1
    
    return 0 if accepted else 2


if __name__ == "__main__":
    sys.exit(main())
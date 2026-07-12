#!/usr/bin/env python3
"""
Autoresearch: Shannon entropy of action stream analyzer.

Analyzes player action streams to detect low entropy patterns that may indicate
AFK (Away From Keyboard) or scripted vendor behavior.
"""

import argparse
import json
import math
import sys
from collections import Counter
from typing import Dict, List, Optional, Tuple


def calculate_entropy(actions: List[str]) -> float:
    """Calculate Shannon entropy: H = -Σ p(x) * log₂(p(x))"""
    if not actions:
        return 0.0
    counter = Counter(actions)
    total = len(actions)
    entropy = -sum((c / total) * math.log2(c / total) for c in counter.values() if c > 0)
    return max(0.0, entropy)  # Ensure non-negative


def read_actions(input_path: str) -> List[str]:
    """Read actions from file or stdin, skipping empty lines and comments."""
    if input_path == "-":
        lines = sys.stdin
        return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    with open(input_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]


def analyze_actions(actions: List[str], threshold: Optional[float] = None) -> Dict:
    """Perform entropy analysis on action stream."""
    entropy = calculate_entropy(actions)
    unique_count = len(set(actions))
    max_entropy = math.log2(unique_count) if unique_count > 1 else 0.0

    result = {
        "action_count": len(actions),
        "unique_actions": unique_count,
        "entropy_bits": round(entropy, 4),
        "max_entropy_bits": round(max_entropy, 4),
        "entropy_ratio": round(entropy / max_entropy, 4) if max_entropy > 0 else 0.0,
    }

    if threshold is not None:
        result.update({
            "threshold": threshold,
            "is_low_entropy": entropy < threshold,
            "classification": "LIKELY_SCRIPTED" if entropy < threshold else "LIKELY_HUMAN",
        })

    return result


def get_top_actions(actions: List[str], n: int = 5) -> List[Tuple[str, int, float]]:
    """Get top N most frequent actions with counts and percentages."""
    counter = Counter(actions)
    total = len(actions)
    return [(a, c, c / total * 100) for a, c in counter.most_common(n)]


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for action entropy analyzer."""
    parser = argparse.ArgumentParser(
        description="Calculate Shannon entropy of action stream to detect AFK/scripted behavior."
    )
    parser.add_argument("input_file", nargs="?", default="-",
                        help="Input file path (use '-' for stdin)")
    parser.add_argument("-t", "--threshold", type=float, default=None,
                        help="Entropy threshold for low-entropy detection")
    parser.add_argument("-j", "--json", action="store_true",
                        help="Output results as JSON")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show detailed frequency breakdown")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Suppress all output (exit code only)")

    args = parser.parse_args(argv)

    try:
        actions = read_actions(args.input_file)
    except FileNotFoundError:
        if not args.quiet:
            print(f"Error: File not found: {args.input_file}", file=sys.stderr)
        return 1

    if not actions:
        if not args.quiet:
            print("Error: No valid actions found in input", file=sys.stderr)
        return 1

    result = analyze_actions(actions, args.threshold)

    if args.quiet:
        return 0 if not args.threshold or not result.get("is_low_entropy") else 2

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    # Human-readable output
    print("Action Stream Entropy Analysis")
    print("================================")
    print(f"Total actions:    {result['action_count']}")
    print(f"Unique actions:   {result['unique_actions']}")
    print(f"Entropy:          {result['entropy_bits']} bits")
    print(f"Max entropy:      {result['max_entropy_bits']} bits")
    print(f"Entropy ratio:    {result['entropy_ratio']:.2%}")

    if args.threshold is not None:
        print(f"\nThreshold:        {result['threshold']} bits")
        print(f"Classification:   {result['classification']}")

    if args.verbose:
        print("\nTop actions:")
        for action, count, pct in get_top_actions(actions):
            print(f"  {action}: {count} ({pct:.1f}%)")

    return 2 if args.threshold is not None and result.get("is_low_entropy") else 0


if __name__ == "__main__":
    sys.exit(main())

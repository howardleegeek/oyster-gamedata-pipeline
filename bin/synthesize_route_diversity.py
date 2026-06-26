#!/usr/bin/env python3
"""
Route Diversity Synthesizer for Cluster A

Generates route_type distribution: 50% normal + 50% special/loop
and WASD distribution: W=40% / A=S=D=20% each
Replaces 100% route_type=1 mono-distribution.

Author: Production Engineer
Date: 2024
"""

import argparse
import csv
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List


def generate_route_distribution(num_routes: int) -> List[Dict[str, Any]]:
    """
    Generate route distribution according to spec:
    - route_type: 50% normal (1), 50% special/loop (2)
    - WASD: W=40%, A=20%, S=20%, D=20%

    Args:
        num_routes: Number of routes to generate

    Returns:
        List of route dictionaries with route_type and wasd_key fields
    """
    routes = []

    # Generate route_type distribution
    route_types = []
    half = num_routes // 2
    # 50% normal (type 1)
    route_types.extend([1] * half)
    # 50% special/loop (type 2)
    route_types.extend([2] * half)

    # If odd number, add one more type 2 to maintain 50/50 split
    if num_routes % 2 == 1:
        route_types.append(2)

    # Shuffle the route types
    random.shuffle(route_types)

    # Generate WASD distribution
    wasd_keys = []
    # W = 40%
    w_count = int(num_routes * 0.4)
    # A, S, D = 20% each
    a_count = int(num_routes * 0.2)
    s_count = int(num_routes * 0.2)
    d_count = num_routes - w_count - a_count - s_count  # Remainder goes to D

    wasd_keys.extend(["W"] * w_count)
    wasd_keys.extend(["A"] * a_count)
    wasd_keys.extend(["S"] * s_count)
    wasd_keys.extend(["D"] * d_count)

    # Shuffle WASD keys
    random.shuffle(wasd_keys)

    # Create route entries
    for i in range(num_routes):
        route = {
            "route_id": i + 1,
            "route_type": route_types[i],
            "wasd_key": wasd_keys[i],
            "description": f"Route {i + 1}: type={route_types[i]}, key={wasd_keys[i]}",
        }
        routes.append(route)

    return routes


def calculate_statistics(routes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate distribution statistics for verification.

    Args:
        routes: List of route dictionaries

    Returns:
        Dictionary with statistics
    """
    total = len(routes)

    # Count route types
    type_counts = {}
    for route in routes:
        route_type = route["route_type"]
        type_counts[route_type] = type_counts.get(route_type, 0) + 1

    # Count WASD keys
    wasd_counts = {}
    for route in routes:
        wasd_key = route["wasd_key"]
        wasd_counts[wasd_key] = wasd_counts.get(wasd_key, 0) + 1

    # Calculate percentages
    type_percentages = {f"type_{k}": f"{(v / total) * 100:.1f}%" for k, v in type_counts.items()}

    wasd_percentages = {f"key_{k}": f"{(v / total) * 100:.1f}%" for k, v in wasd_counts.items()}

    return {
        "total_routes": total,
        "route_type_distribution": type_counts,
        "route_type_percentages": type_percentages,
        "wasd_distribution": wasd_counts,
        "wasd_percentages": wasd_percentages,
    }


def write_output(routes: List[Dict[str, Any]], output_format: str, output_path: str) -> None:
    """
    Write routes to output file in specified format.

    Args:
        routes: List of route dictionaries
        output_format: Format to write ('json', 'csv', or 'txt')
        output_path: Path to output file
    """
    output_path = Path(output_path)

    if output_format == "json":
        with open(output_path, "w") as f:
            json.dump({"routes": routes, "statistics": calculate_statistics(routes)}, f, indent=2)

    elif output_format == "csv":
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["route_id", "route_type", "wasd_key", "description"]
            )
            writer.writeheader()
            for route in routes:
                writer.writerow(route)

    elif output_format == "txt":
        with open(output_path, "w") as f:
            f.write("Route Diversity Synthesis Results\n")
            f.write("=" * 40 + "\n\n")

            stats = calculate_statistics(routes)
            f.write(f"Total routes: {stats['total_routes']}\n\n")

            f.write("Route Type Distribution:\n")
            for route_type, count in stats["route_type_distribution"].items():
                percentage = stats["route_type_percentages"][f"type_{route_type}"]
                f.write(f"  Type {route_type}: {count} routes ({percentage})\n")

            f.write("\nWASD Key Distribution:\n")
            for key, count in stats["wasd_distribution"].items():
                percentage = stats["wasd_percentages"][f"key_{key}"]
                f.write(f"  Key '{key}': {count} routes ({percentage})\n")

            f.write("\n" + "=" * 40 + "\n\n")
            f.write("Individual Routes:\n")
            for route in routes:
                f.write(f"{route['description']}\n")


def main(argv: List[str]) -> int:
    """
    Main entry point for the route diversity synthesizer.

    Args:
        argv: Command line arguments

    Returns:
        Exit code (0 for success, non-zero for error)
    """
    parser = argparse.ArgumentParser(
        description="Synthesize route diversity for Cluster A",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
        Generates route distribution:
        - route_type: 50%% normal (1), 50%% special/loop (2)
        - WASD: W=40%%, A=20%%, S=20%%, D=20%%
        
        Replaces 100%% route_type=1 mono-distribution.
        """,
    )

    parser.add_argument(
        "-n",
        "--num-routes",
        type=int,
        default=100,
        help="Number of routes to generate (default: 100)",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default="route_diversity.json",
        help="Output file path (default: route_diversity.json)",
    )

    parser.add_argument(
        "-f",
        "--format",
        choices=["json", "csv", "txt"],
        default="json",
        help="Output format (json, csv, or txt) (default: json)",
    )

    parser.add_argument("-s", "--seed", type=int, help="Random seed for reproducible results")

    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify distribution matches spec and print statistics",
    )

    args = parser.parse_args(argv)

    # Set random seed if provided
    if args.seed is not None:
        random.seed(args.seed)

    # Validate input
    if args.num_routes <= 0:
        print("Error: Number of routes must be positive", file=sys.stderr)
        return 1

    try:
        # Generate routes
        print(f"Generating {args.num_routes} routes...")
        routes = generate_route_distribution(args.num_routes)

        # Write output
        print(f"Writing output to {args.output} in {args.format} format...")
        write_output(routes, args.format, args.output)

        # Calculate and display statistics
        stats = calculate_statistics(routes)

        print("\n" + "=" * 50)
        print("ROUTE DIVERSITY SYNTHESIS COMPLETE")
        print("=" * 50)

        print(f"\nTotal routes generated: {stats['total_routes']}")

        print("\nRoute Type Distribution:")
        for route_type, count in stats["route_type_distribution"].items():
            percentage = stats["route_type_percentages"][f"type_{route_type}"]
            print(f"  Type {route_type}: {count} routes ({percentage})")

        print("\nWASD Key Distribution:")
        for key, count in stats["wasd_distribution"].items():
            percentage = stats["wasd_percentages"][f"key_{key}"]
            print(f"  Key '{key}': {count} routes ({percentage})")

        # Verify against spec if requested
        if args.verify:
            print("\n" + "=" * 50)
            print("SPEC VERIFICATION:")
            print("=" * 50)

            # Check route type distribution (should be close to 50/50)
            type1_percent = (
                stats["route_type_distribution"].get(1, 0) / stats["total_routes"]
            ) * 100
            type2_percent = (
                stats["route_type_distribution"].get(2, 0) / stats["total_routes"]
            ) * 100

            print(f"Route Type 1 (normal): {type1_percent:.1f}% (target: 50%)")
            print(f"Route Type 2 (special/loop): {type2_percent:.1f}% (target: 50%)")

            # Check WASD distribution
            w_percent = (stats["wasd_distribution"].get("W", 0) / stats["total_routes"]) * 100
            a_percent = (stats["wasd_distribution"].get("A", 0) / stats["total_routes"]) * 100
            s_percent = (stats["wasd_distribution"].get("S", 0) / stats["total_routes"]) * 100
            d_percent = (stats["wasd_distribution"].get("D", 0) / stats["total_routes"]) * 100

            print("\nWASD Distribution:")
            print(f"  W: {w_percent:.1f}% (target: 40%)")
            print(f"  A: {a_percent:.1f}% (target: 20%)")
            print(f"  S: {s_percent:.1f}% (target: 20%)")
            print(f"  D: {d_percent:.1f}% (target: 20%)")

            # Calculate deviation from targets
            type_deviation = abs(type1_percent - 50) + abs(type2_percent - 50)
            wasd_deviation = (
                abs(w_percent - 40)
                + abs(a_percent - 20)
                + abs(s_percent - 20)
                + abs(d_percent - 20)
            )

            print("\nTotal deviation from spec:")
            print(f"  Route types: {type_deviation:.1f}% (lower is better)")
            print(f"  WASD keys: {wasd_deviation:.1f}% (lower is better)")

            if type_deviation < 5 and wasd_deviation < 10:
                print("\n✓ Distribution matches spec within acceptable tolerance")
            else:
                print(
                    "\n⚠ Distribution deviates from spec (increase --num-routes for better accuracy)"
                )

        print(f"\nOutput written to: {args.output}")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

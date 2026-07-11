#!/usr/bin/env python3
"""
automatic_diversity_metric.py

Cluster E+: per-scene diversity score (biome / time / weather entropy)
so buyer can sort cohorts.

This module calculates Shannon entropy-based diversity metrics for scene
cohorts, enabling buyers to evaluate and sort collections based on
environmental diversity.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# Lazy imports for optional dependencies
yaml: Any = None
numpy: Any = None


def _lazy_import_yaml() -> Any:
    """Lazily import PyYAML if available."""
    global yaml
    if yaml is None:
        try:
            import yaml as _yaml
            yaml = _yaml
        except ImportError:
            yaml = None
    return yaml


def _lazy_import_numpy() -> Any:
    """Lazily import numpy if available."""
    global numpy
    if numpy is None:
        try:
            import numpy as _numpy
            numpy = _numpy
        except ImportError:
            numpy = None
    return numpy


def calculate_shannon_entropy(values: list[str]) -> float:
    """
    Calculate Shannon entropy for a list of categorical values.

    Args:
        values: List of categorical values (e.g., biome names)

    Returns:
        Shannon entropy in bits (normalized by log(2))
    """
    if not values:
        return 0.0

    counter = Counter(values)
    total = len(values)
    entropy = 0.0

    for count in counter.values():
        if count > 0:
            probability = count / total
            entropy -= probability * math.log2(probability)

    return entropy


def normalize_entropy(entropy: float, num_categories: int) -> float:
    """
    Normalize entropy to [0, 1] range.

    Args:
        entropy: Raw Shannon entropy
        num_categories: Number of unique categories

    Returns:
        Normalized entropy value between 0 and 1
    """
    if num_categories <= 1:
        return 0.0

    max_entropy = math.log2(num_categories)
    if max_entropy == 0:
        return 0.0

    return entropy / max_entropy


def calculate_diversity_score(
    biome: list[str],
    time_of_day: list[str],
    weather: list[str]
) -> dict[str, float]:
    """
    Calculate comprehensive diversity score from scene attributes.

    Args:
        biome: List of biome identifiers
        time_of_day: List of time-of-day values
        weather: List of weather conditions

    Returns:
        Dictionary containing individual and aggregate diversity scores
    """
    # Calculate raw entropies
    biome_entropy = calculate_shannon_entropy(biome)
    time_entropy = calculate_shannon_entropy(time_of_day)
    weather_entropy = calculate_shannon_entropy(weather)

    # Calculate normalized entropies
    biome_unique = len(set(biome))
    time_unique = len(set(time_of_day))
    weather_unique = len(set(weather))

    biome_normalized = normalize_entropy(biome_entropy, biome_unique)
    time_normalized = normalize_entropy(time_entropy, time_unique)
    weather_normalized = normalize_entropy(weather_entropy, weather_unique)

    # Calculate aggregate diversity score (weighted average)
    # Equal weights for biome, time, and weather
    aggregate = (biome_normalized + time_normalized + weather_normalized) / 3.0

    return {
        "biome_entropy_raw": round(biome_entropy, 4),
        "biome_entropy_normalized": round(biome_normalized, 4),
        "biome_unique_count": biome_unique,
        "time_entropy_raw": round(time_entropy, 4),
        "time_entropy_normalized": round(time_normalized, 4),
        "time_unique_count": time_unique,
        "weather_entropy_raw": round(weather_entropy, 4),
        "weather_entropy_normalized": round(weather_normalized, 4),
        "weather_unique_count": weather_unique,
        "aggregate_diversity_score": round(aggregate, 4),
    }


def load_scene_data(file_path: Path) -> list[dict[str, Any]]:
    """
    Load scene data from JSON or YAML file.

    Args:
        file_path: Path to input file

    Returns:
        List of scene dictionaries

    Raises:
        ValueError: If file format is unsupported or parsing fails
    """
    suffix = file_path.suffix.lower()

    if suffix == ".json":
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    elif suffix in (".yaml", ".yml"):
        yaml_module = _lazy_import_yaml()
        if yaml_module is None:
            raise ValueError("PyYAML not available. Install pyyaml or use JSON input.")
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml_module.safe_load(f)
    else:
        raise ValueError(f"Unsupported file format: {suffix}")

    # Normalize to list format
    if isinstance(data, dict):
        if "scenes" in data:
            return data["scenes"]
        elif "cohort" in data:
            return data["cohort"]
        else:
            return [data]
    elif isinstance(data, list):
        return data
    else:
        raise ValueError(f"Unexpected data structure in {file_path}")


def extract_attributes(scenes: list[dict[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    """
    Extract biome, time, and weather attributes from scene data.

    Args:
        scenes: List of scene dictionaries

    Returns:
        Tuple of (biome_list, time_list, weather_list)
    """
    biome = []
    time_of_day = []
    weather = []

    for scene in scenes:
        # Handle various field name conventions
        biome_val = (
            scene.get("biome") or scene.get("environment") or scene.get("biome_type", "unknown")
        )
        time_val = (
            scene.get("time_of_day") or scene.get("time") or scene.get("tod", "unknown")
        )
        weather_key = "weather"
        weather_val = (
            scene.get(weather_key)
            or scene.get("weather_condition")
            or scene.get("conditions", "unknown")
        )

        biome.append(str(biome_val))
        time_of_day.append(str(time_val))
        weather.append(str(weather_val))

    return biome, time_of_day, weather


def format_output(
    scores: dict[str, float],
    output_format: str,
    include_details: bool = True
) -> str:
    """
    Format diversity scores for output.

    Args:
        scores: Dictionary of diversity scores
        output_format: Output format ('json', 'text', 'csv')
        include_details: Whether to include detailed breakdown

    Returns:
        Formatted output string
    """
    if output_format == "json":
        return json.dumps(scores, indent=2)

    elif output_format == "csv":
        if not include_details:
            return f"aggregate_diversity_score\n{scores['aggregate_diversity_score']}"

        headers = [
            "biome_entropy_raw", "biome_entropy_normalized", "biome_unique_count",
            "time_entropy_raw", "time_entropy_normalized", "time_unique_count",
            "weather_entropy_raw", "weather_entropy_normalized", "weather_unique_count",
            "aggregate_diversity_score"
        ]
        values = [str(scores.get(h, "")) for h in headers]
        return ",".join(headers) + "\n" + ",".join(values)

    else:  # text format
        lines = [
            "=" * 50,
            "DIVERSITY METRIC REPORT",
            "=" * 50,
            "",
            "BIOME DIVERSITY:",
            f"  Raw Entropy:     {scores['biome_entropy_raw']:.4f} bits",
            f"  Normalized:      {scores['biome_entropy_normalized']:.4f}",
            f"  Unique Biomes:   {scores['biome_unique_count']}",
            "",
            "TIME OF DAY DIVERSITY:",
            f"  Raw Entropy:     {scores['time_entropy_raw']:.4f} bits",
            f"  Normalized:      {scores['time_entropy_normalized']:.4f}",
            f"  Unique Times:    {scores['time_unique_count']}",
            "",
            "WEATHER DIVERSITY:",
            f"  Raw Entropy:     {scores['weather_entropy_raw']:.4f} bits",
            f"  Normalized:      {scores['weather_entropy_normalized']:.4f}",
            f"  Unique Weather:  {scores['weather_unique_count']}",
            "",
            "-" * 50,
            f"AGGREGATE DIVERSITY SCORE: {scores['aggregate_diversity_score']:.4f}",
            "-" * 50,
        ]
        return "\n".join(lines)


def validate_input_file(file_path: Path) -> None:
    """
    Validate that input file exists and is readable.

    Args:
        file_path: Path to input file

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If file is empty
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Input file not found: {file_path}")

    if file_path.stat().st_size == 0:
        raise ValueError(f"Input file is empty: {file_path}")


def main(argv: list[str] | None = None) -> int:
    """
    Main entry point for the diversity metric calculator.

    Args:
        argv: Command-line arguments (defaults to sys.argv)

    Returns:
        Exit code (0 for success, non-zero for error)
    """
    parser = argparse.ArgumentParser(
        description="Calculate per-scene diversity scores (biome/time/weather entropy) "
                    "for cohort sorting.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s scenes.json
  %(prog)s cohort.yaml -f csv
  %(prog)s data.json -o results.txt
  %(prog)s scenes.json --quiet
        """
    )

    parser.add_argument(
        "input",
        type=Path,
        help="Input file containing scene data (JSON or YAML)"
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        help="Output file path (default: stdout)"
    )
    parser.add_argument(
        "-f", "--format",
        choices=["json", "text", "csv"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress non-result output (errors still printed)"
    )
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Output only aggregate score (for CSV)"
    )

    args = parser.parse_args(argv)

    try:
        # Validate input
        validate_input_file(args.input)

        # Load scene data
        scenes = load_scene_data(args.input)

        if not scenes:
            print("Error: No scenes found in input data", file=sys.stderr)
            return 1

        # Extract attributes
        biome, time_of_day, weather = extract_attributes(scenes)

        # Calculate diversity scores
        scores = calculate_diversity_score(biome, time_of_day, weather)

        # Format output
        output = format_output(scores, args.format, not args.no_details)

        # Write output
        if args.output:
            args.output.write_text(output, encoding="utf-8")
            if not args.quiet:
                print(f"Results written to: {args.output}")
        else:
            print(output)

        return 0

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON - {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: Unexpected error - {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

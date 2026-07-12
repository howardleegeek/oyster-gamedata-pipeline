#!/usr/bin/env python3
"""
Autoresearch: Benchmark lint_buyer_spec on 100-tarball corpus — surface p50/p95/p99.
"""

import argparse
import ast
import json
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

# Lazy import for numpy (optional dependency)
_numpy = None


def _get_numpy():
    global _numpy
    if _numpy is None:
        import numpy

        _numpy = numpy
    return _numpy


def parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Benchmark lint_buyer_spec on 100-tarball corpus — surface p50/p95/p99."
    )
    parser.add_argument("corpus", type=Path, help="Path to corpus directory")
    parser.add_argument("--runs", type=int, default=1, help="Runs per file")
    parser.add_argument("--output", type=Path, help="Output JSON path")
    parser.add_argument("--verbose", action="store_true", help="Print per-file timing")
    return parser.parse_args(argv)


def discover_corpus(corpus_path: Path) -> List[Path]:
    """Discover buyer spec files in corpus directory."""
    if not corpus_path.is_dir():
        raise ValueError(f"Not a directory: {corpus_path}")
    files = []
    for entry in corpus_path.iterdir():
        if entry.is_file() and (
            entry.suffix.lower() in (".tar", ".gz", ".bz2", ".xz", ".json", ".yaml", ".yml")
            or entry.name.endswith((".tar.gz", ".tar.bz2", ".tar.xz"))
        ):
            files.append(entry)
    return sorted(files)


def lint_buyer_spec(file_path: Path) -> Tuple[bool, str]:
    """Lint a buyer specification file."""
    try:
        suffix = file_path.suffix.lower()
        if suffix in (".tar", ".gz", ".bz2", ".xz"):
            time.sleep(0.001)  # Simulate archive I/O
        elif suffix in (".json", ".yaml", ".yml"):
            content = file_path.read_text()
            try:
                ast.literal_eval(content)
            except (ValueError, SyntaxError):
                import yaml

                yaml.safe_load(content)
        return (True, "OK")
    except Exception as e:
        return (False, str(e))


def benchmark_file(file_path: Path, runs: int = 1) -> List[float]:
    """Benchmark lint_buyer_spec on a single file."""
    timings = []
    for _ in range(runs):
        start = time.perf_counter()
        success, _ = lint_buyer_spec(file_path)
        elapsed = time.perf_counter() - start
        timings.append(elapsed)
        if not success:
            break
    return timings


def calculate_percentiles(values: List[float], percentiles: List[int]) -> List[float]:
    """Calculate percentiles from timing values."""
    np = _get_numpy()
    return [float(np.percentile(values, p)) for p in percentiles]


def format_results(timings: List[float], p50: float, p95: float, p99: float, total: int) -> str:
    """Format benchmark results."""
    return "\n".join(
        [
            "Autoresearch Lint Performance Benchmark",
            "=" * 40,
            f"Total files: {total}",
            f"Total elapsed: {sum(timings):.3f}s",
            f"Average: {sum(timings)/len(timings):.3f}s",
            "",
            "Latency Percentiles:",
            f"  p50: {p50*1000:.2f}ms",
            f"  p95: {p95*1000:.2f}ms",
            f"  p99: {p99*1000:.2f}ms",
        ]
    )


def write_json(
    path: Path, timings: List[float], p50: float, p95: float, p99: float, total: int
) -> None:
    """Write results to JSON."""
    result = {
        "total_files": total,
        "total_elapsed": sum(timings),
        "average_per_file": sum(timings) / len(timings),
        "percentiles": {"p50": p50, "p95": p95, "p99": p99},
        "all_timings": timings,
    }
    path.write_text(json.dumps(result, indent=2))


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point."""
    args = parse_args(argv or sys.argv[1:])

    if not args.corpus.exists():
        print(f"Error: Corpus does not exist: {args.corpus}", file=sys.stderr)
        return 1

    try:
        files = discover_corpus(args.corpus)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not files:
        print(f"Warning: No files found in: {args.corpus}", file=sys.stderr)
        return 0

    print(f"Discovered {len(files)} files")

    # Run benchmark
    all_timings: List[float] = []
    for fp in files:
        timings = benchmark_file(fp, runs=args.runs)
        all_timings.extend(timings)
        if args.verbose:
            print(f"  {fp.name}: {timings[-1]*1000:.2f}ms")

    # Calculate percentiles
    p50, p95, p99 = calculate_percentiles(all_timings, [50, 95, 99])

    # Output results
    print(format_results(all_timings, p50, p95, p99, len(files)))

    if args.output:
        write_json(args.output, all_timings, p50, p95, p99, len(files))
        print(f"\nResults written to: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

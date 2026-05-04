#!/usr/bin/env python3
"""
Edge test for NaN and Inf float values in Vector3.

Tests boundary conditions where Vector3 contains NaN or infinite float values.
According to the buyer spec, all floats must be finite, so lint should reject
vectors with non-finite values.

Usage:
    python bin/edge_test_nan_inf_floats.py [--verbose] [--report]
"""

from __future__ import annotations

import argparse
import ast
import math
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple


class Vector3:
    """A simple 3D vector class for testing boundary conditions."""

    def __init__(self, x: float, y: float, z: float) -> None:
        self.x, self.y, self.z = x, y, z

    def __repr__(self) -> str:
        return f"Vector3({self.x}, {self.y}, {self.z})"

    def is_finite(self) -> bool:
        """Check if all components are finite (not NaN or Inf)."""
        return math.isfinite(self.x) and math.isfinite(self.y) and math.isfinite(self.z)


class Vector3LintValidator(ast.NodeVisitor):
    """AST-based linter to detect non-finite floats in Vector3 assignments."""

    def __init__(self) -> None:
        self.violations: List[Tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        """Visit function calls to detect Vector3 instantiations."""
        if isinstance(node.func, ast.Name) and node.func.id == "Vector3":
            for i, arg in enumerate(node.args):
                if isinstance(arg, ast.Constant) and isinstance(arg.value, float):
                    if math.isnan(arg.value):
                        self.violations.append((arg.lineno, f"NaN at pos {i}"))
                    elif math.isinf(arg.value):
                        self.violations.append((arg.lineno, f"Inf at pos {i}"))
        self.generic_visit(node)


def create_test_vectors() -> List[Vector3]:
    """Create test vectors including boundary cases."""
    return [
        Vector3(1.0, 2.0, 3.0),
        Vector3(-1.5, 0.0, 4.2),
        Vector3(float('nan'), 1.0, 2.0),
        Vector3(1.0, float('inf'), 2.0),
        Vector3(1.0, 2.0, float('-inf')),
    ]


def validate_vectors(vectors: List[Vector3]) -> Tuple[int, List[str]]:
    """Validate that all vectors have finite float components."""
    errors = []
    for i, vec in enumerate(vectors):
        if not vec.is_finite():
            errors.append(f"Vector[{i}] {vec} has non-finite values")
    return len(errors), errors


def lint_source_file(filepath: Path) -> Tuple[int, List[Tuple[int, str]]]:
    """Lint a Python source file for non-finite Vector3 values."""
    try:
        source = filepath.read_text()
        tree = ast.parse(source)
    except (OSError, SyntaxError) as e:
        return 1, [(0, str(e))]

    validator = Vector3LintValidator()
    validator.visit(tree)
    return len(validator.violations), validator.violations


def run_tests(verbose: bool = False) -> int:
    """Run all edge case tests for NaN and Inf in Vector3."""
    print("=" * 50)
    print("Edge Test: NaN and Inf in Vector3")
    print("=" * 50)

    # Test 1: Vector3 with boundary values
    print("\n[Test 1] Creating vectors with NaN/Inf...")
    vectors = create_test_vectors()
    if verbose:
        for i, v in enumerate(vectors):
            print(f"  [{i}] {v} (finite: {v.is_finite()})")

    # Test 2: Validate finiteness
    print("\n[Test 2] Validating vector finiteness...")
    err_cnt, errors = validate_vectors(vectors)
    if verbose:
        for e in errors:
            print(f"  ERROR: {e}")
    print(f"  Found {err_cnt} vectors with non-finite values")

    # Test 3: Lint temp source file
    print("\n[Test 3] Linting source code...")
    test_code = '''
class Vector3:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.x, self.y, self.z = x, y, z

v1 = Vector3(1.0, 2.0, 3.0)
v_bad = Vector3(float("nan"), 1.0, 2.0)
v_inf = Vector3(1.0, float("inf"), 2.0)
'''
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(test_code)
        temp_path = Path(f.name)

    try:
        vcnt, vlist = lint_source_file(temp_path)
        print(f"  Found {vcnt} lint violations")
        if verbose:
            for ln, msg in vlist:
                print(f"    Line {ln}: {msg}")
    finally:
        temp_path.unlink()

    # Summary
    print("\n" + "=" * 50)
    total = err_cnt + vcnt
    print(f"RESULT: {total} issues detected (expected)")
    return 0 if total > 0 else 1


def main(argv: List[str]) -> int:
    """Main entry point for the edge test CLI."""
    parser = argparse.ArgumentParser(description="Edge test for NaN/Inf in Vector3")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--report", action="store_true", help="Generate detailed report")
    args = parser.parse_args(argv)
    return run_tests(verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

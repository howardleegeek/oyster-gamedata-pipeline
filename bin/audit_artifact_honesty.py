#!/usr/bin/env python3
"""IL10 (Producer-Artifact Honesty) lint.

Walks every residual file under ``bin/v*_residuals/`` and ``bin/v3_physics_oracle/``
and asserts: any function that takes a producer-side artifact path
(``*_path``, ``*_dir``, ``manifest_path``, ``video_path``, ``inputs_path``,
``depth_dir``) MUST contain a visible ABSTAIN gate (string literal "ABSTAIN"
in the body) OR a return whose ``residual=`` is NaN/inf.

Without this lint a verifier could silently PASS when the artifact is absent —
the exact false-confidence failure mode IL10 was written to prevent
(see docs/SPEC_R13_MULTIMODAL.md § 1).

Stdlib only (``ast``, ``pathlib``).
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCAN_DIRS = (
    ROOT / "v1_claude_residuals",
    ROOT / "v2_minimax_residuals",
    ROOT / "v2prime_glm_residuals",
    ROOT / "v3_physics_oracle",
)

# Suspicious parameter names that bind to a producer-written artifact.
ARTIFACT_PARAM_NAMES = frozenset({
    "manifest_path",
    "video_path",
    "inputs_path",
    "depth_dir",
})
ARTIFACT_PARAM_SUFFIXES = ("_path", "_dir")


@dataclass(frozen=True)
class Violation:
    file: Path
    function: str
    lineno: int
    artifact_param: str

    def format(self) -> str:
        """Format violation as a human-readable error message.

        Returns:
            Formatted string with file path, line number, function name,
            artifact parameter, and IL10 violation description.
        """
        rel = self.file.relative_to(ROOT.parent)
        return (
            f"{rel}:{self.lineno}: function '{self.function}' has artifact "
            f"parameter '{self.artifact_param}' but no ABSTAIN gate "
            f"(IL10 violation — could silent-PASS when artifact absent)"
        )


def _is_artifact_param(name: str) -> bool:
    """Return True if a parameter name looks like a producer-side artifact path."""
    if name in ARTIFACT_PARAM_NAMES:
        return True
    return any(name.endswith(suf) for suf in ARTIFACT_PARAM_SUFFIXES)


def _function_param_names(node: ast.FunctionDef) -> list[str]:
    """All positional / keyword-only parameter names of a function."""
    args = node.args
    return [a.arg for a in (*args.args, *args.kwonlyargs, *args.posonlyargs)]


def _body_has_abstain_string(node: ast.FunctionDef) -> bool:
    """True if any string literal in the function body contains 'ABSTAIN'."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if "ABSTAIN" in sub.value:
                return True
    return False


def _body_has_nan_or_inf_residual(node: ast.FunctionDef) -> bool:
    """True if a Return statement passes math.nan / math.inf / float('nan').

    Recognized forms (any one suffices):
      * ``ResidualResult(..., math.nan, ...)`` — positional NaN/inf
      * ``... residual=math.nan ...``           — keyword NaN/inf
      * ``return {"residual": float("nan"), ...}`` — dict literal
    """
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Return):
            continue
        for inner in ast.walk(sub):
            if isinstance(inner, ast.Attribute):
                if (
                    isinstance(inner.value, ast.Name)
                    and inner.value.id == "math"
                    and inner.attr in {"nan", "inf"}
                ):
                    return True
            if isinstance(inner, ast.Call):
                fn = inner.func
                if isinstance(fn, ast.Name) and fn.id == "float":
                    if inner.args and isinstance(inner.args[0], ast.Constant):
                        v = inner.args[0].value
                        if isinstance(v, str) and v.lower() in {"nan", "inf", "+inf", "-inf"}:
                            return True
    return False


def _audit_function(path: Path, node: ast.FunctionDef) -> list[Violation]:
    """Return per-artifact-param violations for one function definition."""
    artifact_params = [p for p in _function_param_names(node) if _is_artifact_param(p)]
    if not artifact_params:
        return []
    if _body_has_abstain_string(node) or _body_has_nan_or_inf_residual(node):
        return []
    return [Violation(path, node.name, node.lineno, p) for p in artifact_params]


def _iter_residual_files() -> list[Path]:
    """Iterate over all residual Python files under SCAN_DIRS.

    Yields:
        Path objects for each .py file (excluding __init__.py) found in
        the configured scan directories.
    """
    files: list[Path] = []
    for d in SCAN_DIRS:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.py")):
            if f.name == "__init__.py":
                continue
            files.append(f)
    return files


def audit(files: list[Path] | None = None) -> list[Violation]:
    """Audit every residual file; return list of violations (empty = clean)."""
    targets = files if files is not None else _iter_residual_files()
    violations: list[Violation] = []
    for path in targets:
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                violations.extend(_audit_function(path, node))
    return violations


def main() -> int:
    """Run the IL10 audit and print results to stdout.

    Returns:
        0 if no violations found, 1 if violations exist.
    """
    violations = audit()
    if not violations:
        print(f"IL10 audit: 0 violations across {len(_iter_residual_files())} residual files")
        return 0
    print(f"IL10 audit: {len(violations)} violation(s) found:")
    for v in violations:
        print(f"  {v.format()}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

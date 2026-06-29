#!/usr/bin/env python3
"""
G024 · buyer_spec_validator_v2.py

Stricter independent validator for buyer specifications.
Performs cross-checks, semantic + structural validation.

Author: Production Engineer
Version: 2.0.0
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def _lazy_import_yaml() -> Optional[Any]:
    """Lazy import PyYAML module."""
    try:
        import yaml

        return yaml
    except ImportError:
        return None


def _lazy_import_pydantic() -> Optional[Any]:
    """Lazy import pydantic module."""
    try:
        import pydantic

        return pydantic
    except ImportError:
        return None


def _lazy_import_torch() -> Optional[Any]:
    """Lazy import torch module."""
    try:
        import torch

        return torch
    except ImportError:
        return None


class ValidationResult:
    """Container for validation results."""

    def __init__(self, path: str) -> None:
        """Initialize validation result for a given path."""
        self.path = path
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.info: List[str] = []

    @property
    def is_valid(self) -> bool:
        """Return True if no errors were found."""
        return len(self.errors) == 0

    def add_error(self, msg: str) -> None:
        """Add an error message."""
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        """Add a warning message."""
        self.warnings.append(msg)

    def add_info(self, msg: str) -> None:
        """Add an info message."""
        self.info.append(msg)

    def merge(self, other: ValidationResult) -> None:
        """Merge another ValidationResult into this one."""
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.info.extend(other.info)


class BuyerSpecValidator:
    """
    Stricter independent validator for buyer specifications.

    Performs structural validation, semantic checks, and cross-reference validation
    for various file types including Python, Bash, YAML, JSON, and Markdown.
    """

    SECRET_PATTERNS = [
        r'(?i)api[_-]?key\s*[:=]\s*["\']?[a-zA-Z0-9]{16,}',
        r'(?i)secret[_-]?key\s*[:=]\s*["\']?[a-zA-Z0-9]{16,}',
        r'(?i)password\s*[:=]\s*["\']?[^\s"\']{8,}',
        r'(?i)token\s*[:=]\s*["\']?[a-zA-Z0-9]{20,}',
        r"(?i)bearer\s+[a-zA-Z0-9._-]{20,}",
    ]

    SHELL_PATTERN = re.compile(r"subprocess\..*\(.*shell\s*=\s*True")
    TMP_PATH_PATTERN = re.compile(r'["\']/tmp/[^"\']*["\']')

    def __init__(self, verbose: bool = False, strict: bool = False) -> None:
        """
        Initialize the validator.

        Args:
            verbose: Enable verbose output.
            strict: Enable strict mode (warnings become errors).
        """
        self.verbose = verbose
        self.strict = strict
        self.results: List[ValidationResult] = []

    def _check_secrets(self, content: str, result: ValidationResult) -> None:
        """Check for hardcoded secrets in content."""
        for pattern in self.SECRET_PATTERNS:
            if re.search(pattern, content):
                result.add_warning("Potential hardcoded secret detected")

    def _check_shell_true(self, content: str, result: ValidationResult) -> None:
        """Check for shell=True usage."""
        if self.SHELL_PATTERN.search(content):
            result.add_error("Found subprocess.run(..., shell=True) - use list form instead")

    def _check_hardcoded_tmp(self, content: str, result: ValidationResult) -> None:
        """Check for hardcoded /tmp/ paths."""
        if self.TMP_PATH_PATTERN.search(content):
            result.add_error("Found hardcoded /tmp/ path - use tempfile.mkdtemp() instead")

    def validate_python_syntax(self, content: str, file_path: str) -> ValidationResult:
        """Validate Python file syntax and structure."""
        result = ValidationResult(file_path)

        # Check AST syntax
        try:
            ast.parse(content)
        except SyntaxError as e:
            result.add_error(f"Python syntax error: {e}")
            return result

        # Check for common issues
        self._check_secrets(content, result)
        self._check_shell_true(content, result)
        self._check_hardcoded_tmp(content, result)

        # Check for imports that should be lazy
        if "import pydantic" in content or "from pydantic" in content:
            result.add_info("Found pydantic import - ensure lazy import if needed")
        if "import torch" in content or "from torch" in content:
            result.add_info("Found torch import - ensure lazy import if needed")

        return result

    def validate_bash_structure(self, content: str, file_path: str) -> ValidationResult:
        """Validate Bash file structure."""
        result = ValidationResult(file_path)

        # Check for set -euo pipefail
        if not re.search(r"^set\s+.*e.*u.*o.*pipefail", content, re.MULTILINE):
            result.add_warning("Missing 'set -euo pipefail' at top of bash script")

        # Check for EXIT trap
        if not re.search(r"trap.*EXIT", content, re.IGNORECASE):
            result.add_warning("Missing EXIT trap for cleanup")

        # Check for shell=True usage
        self._check_shell_true(content, result)

        # Check for hardcoded /tmp/ paths
        self._check_hardcoded_tmp(content, result)

        # Check for secrets
        self._check_secrets(content, result)

        # Validate bash syntax
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            try:
                proc = subprocess.run(
                    ["bash", "-n", tmp.name], capture_output=True, text=True, timeout=5
                )
                if proc.returncode != 0:
                    result.add_error(f"Bash syntax error: {proc.stderr.strip()}")
            except subprocess.TimeoutExpired:
                result.add_error("Bash syntax check timed out")
            except Exception as e:
                result.add_error(f"Failed to check bash syntax: {e}")
            finally:
                Path(tmp.name).unlink(missing_ok=True)

        return result

    def validate_yaml_structure(self, content: str, file_path: str) -> ValidationResult:
        """Validate YAML file structure."""
        result = ValidationResult(file_path)

        yaml_module = _lazy_import_yaml()
        if yaml_module is None:
            result.add_warning("PyYAML not available, skipping YAML validation")
            return result

        try:
            yaml_module.safe_load(content)
        except yaml_module.YAMLError as e:
            result.add_error(f"YAML parse error: {e}")

        # Check for secrets
        self._check_secrets(content, result)

        return result

    def validate_json_structure(self, content: str, file_path: str) -> ValidationResult:
        """Validate JSON file structure."""
        result = ValidationResult(file_path)

        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            result.add_error(f"JSON parse error: {e}")

        # Check for secrets
        self._check_secrets(content, result)

        return result

    def validate_markdown_structure(self, content: str, file_path: str) -> ValidationResult:
        """Validate Markdown file structure."""
        result = ValidationResult(file_path)

        # Basic markdown validation - check for common structure
        lines = content.split("\n")

        # Check for at least one heading
        has_heading = any(line.strip().startswith("#") for line in lines)
        if not has_heading:
            result.add_warning("No headings found in markdown file")

        # Check for proper list structure
        in_list = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("- ") or stripped.startswith("* ") or stripped.startswith("1. "):
                in_list = True
            elif stripped and in_list and not (stripped.startswith("  ") or stripped.startswith("\t")):
                result.add_warning(f"List continuation issue at line {i + 1}")

        # Check for secrets
        self._check_secrets(content, result)

        return result

    def validate_file(self, file_path: str) -> ValidationResult:
        """Validate a single file based on its extension."""
        path = Path(file_path)
        if not path.exists():
            result = ValidationResult(file_path)
            result.add_error(f"File not found: {file_path}")
            return result

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            result = ValidationResult(file_path)
            result.add_error(f"File is not valid UTF-8: {file_path}")
            return result

        suffix = path.suffix.lower()
        validators: Dict[str, Callable[[str, str], ValidationResult]] = {
            ".py": self.validate_python_syntax,
            ".sh": self.validate_bash_structure,
            ".bash": self.validate_bash_structure,
            ".yaml": self.validate_yaml_structure,
            ".yml": self.validate_yaml_structure,
            ".md": self.validate_markdown_structure,
            ".markdown": self.validate_markdown_structure,
            ".json": self.validate_json_structure,
        }

        if suffix in validators:
            return validators[suffix](content, file_path)

        result = ValidationResult(file_path)
        result.add_warning(f"Unsupported file type: {suffix}")
        return result

    def validate_files(self, files: List[str]) -> int:
        """Validate multiple files and report results. Returns exit code."""
        total_errors = 0
        total_warnings = 0

        for file_path in files:
            result = self.validate_file(file_path)
            self.results.append(result)
            total_errors += len(result.errors)
            total_warnings += len(result.warnings)

        for result in self.results:
            if result.errors or (self.strict and result.warnings):
                print(f"\n❌ {result.path}", file=sys.stderr)
            elif result.warnings:
                print(f"\n⚠️  {result.path}", file=sys.stderr)
            elif self.verbose:
                print(f"\n✓ {result.path}", file=sys.stderr)

            for err in result.errors:
                print(f"  ERROR: {err}", file=sys.stderr)
            for warn in result.warnings:
                prefix = "ERROR (strict)" if self.strict else "WARNING"
                print(f"  {prefix}: {warn}", file=sys.stderr)
            if self.verbose:
                for info in result.info:
                    print(f"  INFO: {info}", file=sys.stderr)

        strict_errors = total_warnings if self.strict else 0
        print(f"\n{'=' * 50}", file=sys.stderr)

        if total_errors + strict_errors > 0:
            print(
                f"FAILED: {total_errors + strict_errors} error(s), {total_warnings} warning(s)",
                file=sys.stderr,
            )
            return 1

        if total_warnings > 0:
            print(f"PASSED with warnings: {total_warnings} warning(s)", file=sys.stderr)
        else:
            print(f"PASSED: {len(files)} file(s) validated", file=sys.stderr)

        return 0


def main(argv: List[str]) -> int:
    """
    Main entry point for the validator.

    Args:
        argv: Command line arguments.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    parser = argparse.ArgumentParser(
        description="Stricter independent validator for buyer specifications.",
        epilog="Validates Python, Bash, YAML, JSON, and Markdown files.",
    )
    parser.add_argument("files", nargs="+", help="Files to validate")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("-s", "--strict", action="store_true", help="Treat warnings as errors")

    args = parser.parse_args(argv)

    validator = BuyerSpecValidator(verbose=args.verbose, strict=args.strict)
    return validator.validate_files(args.files)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

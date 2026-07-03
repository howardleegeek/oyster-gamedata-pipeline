#!/usr/bin/env python3
"""
Error Severity Classifier

Auto-classifier: maps incoming error_class + module + traceback signature to severity
(critical = data loss / payment / auth; high = crash; medium = degraded; low = warning);
rules table with override path.

Author: Production Engineering Team
Version: 1.0.0
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy imports for optional dependencies
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


# -----------------------------------------------------------------------------
# Severity Levels
# -----------------------------------------------------------------------------

class Severity:
    """Severity level constants and definitions."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"

    # Human-readable descriptions
    DESCRIPTIONS = {
        CRITICAL: "Data loss / payment / auth failure",
        HIGH: "Application crash or service outage",
        MEDIUM: "Degraded performance or partial functionality",
        LOW: "Warning or informational message",
        UNKNOWN: "Unclassified error",
    }

    # Priority order (lower number = more severe)
    PRIORITY = {
        CRITICAL: 0,
        HIGH: 1,
        MEDIUM: 2,
        LOW: 3,
        UNKNOWN: 4,
    }

    @classmethod
    def is_valid(cls, level: str) -> bool:
        """Check if severity level is valid."""
        return level in cls.PRIORITY


# -----------------------------------------------------------------------------
# Classification Rules
# -----------------------------------------------------------------------------

# Default classification rules: (error_class_pattern, module_pattern, keywords) -> severity
DEFAULT_RULES = [
    # Critical: Data loss / payment / auth
    (r".*", r".*auth.*|.*login.*|.*oauth.*|.*session.*", r"unauthorized|forbidden|auth.*fail", Severity.CRITICAL),
    (r".*", r".*payment.*|.*billing.*|.*invoice.*|.*transaction.*", r"declined|failed|error", Severity.CRITICAL),
    (r".*", r".*database.*|.*db.*|.*storage.*", r"data.*loss|corrupt|delete.*fail", Severity.CRITICAL),
    (r".*", r".*", r"OutOfMemoryError|StackOverflowError", Severity.CRITICAL),

    # High: Crash scenarios
    (r".*", r".*", r"NullPointerException|NullReferenceException|NoneType.*", Severity.HIGH),
    (r".*", r".*", r"ConnectionRefused|ConnectionReset|TimeoutError", Severity.HIGH),
    (r".*", r".*", r"ImportError|ModuleNotFoundError|AttributeError", Severity.HIGH),
    (r"RuntimeError|SystemExit|KeyboardInterrupt", r".*", r".*", Severity.HIGH),

    # Medium: Degraded performance
    (r".*", r".*", r"timeout|slow|latency|degraded", Severity.MEDIUM),
    (r".*", r".*", r"retry|fallback|circuit.*breaker", Severity.MEDIUM),
    (r".*", r".*", r"cache.*miss|rate.*limit", Severity.MEDIUM),

    # Low: Warnings
    (r".*", r".*", r"warning|deprecated|debug", Severity.LOW),
    (r".*", r".*", r"info|log|audit", Severity.LOW),
]


class RuleEngine:
    """Rule-based classification engine with override support."""

    def __init__(self, rules: Optional[list] = None, override_path: Optional[Path] = None):
        """
        Initialize the rule engine.

        Args:
            rules: List of classification rules. Each rule is a tuple of
                   (error_class_pattern, module_pattern, keywords, severity).
            override_path: Path to override rules file (JSON or YAML).
        """
        self.rules = rules if rules is not None else DEFAULT_RULES.copy()
        self._load_overrides(override_path)

    def _load_overrides(self, override_path: Optional[Path]) -> None:
        """Load override rules from file if provided."""
        if override_path is None or not override_path.exists():
            return

        try:
            if override_path.suffix in (".yaml", ".yml"):
                if not YAML_AVAILABLE:
                    return
                with open(override_path, "r", encoding="utf-8") as f:
                    overrides = yaml.safe_load(f)
            elif override_path.suffix == ".json":
                with open(override_path, "r", encoding="utf-8") as f:
                    overrides = json.load(f)
            else:
                return

            if overrides and "rules" in overrides:
                # Prepend override rules (higher priority)
                self.rules = overrides["rules"] + self.rules
        except FileNotFoundError:
            # Override file was removed between exists() and open() — fall back to defaults.
            logger.debug("Override file disappeared before load: %s", override_path)
        except (OSError, ValueError, TypeError) as exc:
            # I/O, value, or YAML-decode errors are operator-actionable: log WARNING
            # with the underlying error so the operator can fix the file, then fall
            # back to default rules. (json.JSONDecodeError is a subclass of ValueError.)
            # Pass exc_info=True so the full traceback is attached for diagnostics.
            logger.warning(
                "Failed to load override file %s (%s); using default rules",
                override_path, exc,
                exc_info=True,
            )
        except Exception as exc:
            # Last-resort safety net: yaml.YAMLError (a yaml.parser.ParserError for
            # malformed YAML) does NOT inherit from ValueError, so it slipped past
            # the narrow except above. Catch any remaining error type here, log it,
            # and fall back to defaults so the classifier stays available. This is
            # strictly safer than the original bare `except Exception: pass` because
            # the underlying error is now visible to operators.
            if YAML_AVAILABLE and isinstance(exc, yaml.YAMLError):
                logger.warning(
                    "Failed to parse YAML override file %s (%s); using default rules",
                    override_path, exc,
                    exc_info=True,
                )
            else:
                logger.warning(
                    "Unexpected error loading override file %s (%s); "
                    "using default rules",
                    override_path, exc,
                    exc_info=True,
                )

    def classify(
        self,
        error_class: str,
        module: str,
        traceback: str = "",
    ) -> str:
        """
        Classify error severity based on error characteristics.

        Args:
            error_class: The error class name (e.g., "ValueError", "RuntimeError").
            module: The module where the error occurred (e.g., "payment.service").
            traceback: Optional traceback text for keyword matching.

        Returns:
            Severity level string.
        """
        combined_text = f"{error_class} {module} {traceback}".lower()

        for rule in self.rules:
            if len(rule) != 4:
                continue

            error_pattern, module_pattern, keywords, severity = rule

            # Check error class pattern
            if not re.match(error_pattern, error_class, re.IGNORECASE):
                continue

            # Check module pattern
            if not re.match(module_pattern, module, re.IGNORECASE):
                continue

            # Check keywords (any keyword match)
            if keywords == ".*" or re.search(keywords, combined_text, re.IGNORECASE):
                return severity

        return Severity.UNKNOWN


# -----------------------------------------------------------------------------
# CLI Interface
# -----------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Classify error severity based on error characteristics.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --error-class ValueError --module payment.service
  %(prog)s --error-class RuntimeError --module api.handler --traceback "timeout error"
  %(prog)s --error-class Exception --module auth.login --override ./rules_override.json
        """,
    )

    parser.add_argument(
        "--error-class", "-e",
        required=True,
        help="Error class name (e.g., ValueError, RuntimeError)",
    )

    parser.add_argument(
        "--module", "-m",
        required=True,
        help="Module where error occurred (e.g., payment.service)",
    )

    parser.add_argument(
        "--traceback", "-t",
        default="",
        help="Traceback text for keyword matching",
    )

    parser.add_argument(
        "--override", "-o",
        type=Path,
        default=None,
        help="Path to override rules file (JSON or YAML)",
    )

    parser.add_argument(
        "--format", "-f",
        choices=["text", "json"],
        default="text",
        help="Output format",
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show severity description",
    )

    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """
    Main entry point for the error severity classifier.

    Args:
        argv: Command-line arguments (defaults to sys.argv).

    Returns:
        Exit code (0 for success, non-zero for errors).
    """
    if argv is None:
        argv = sys.argv[1:]

    try:
        args = parse_args(argv)
    except SystemExit:
        return 1

    # Initialize rule engine
    engine = RuleEngine(override_path=args.override)

    # Classify error
    severity = engine.classify(
        error_class=args.error_class,
        module=args.module,
        traceback=args.traceback,
    )

    # Output result
    if args.format == "json":
        result = {
            "error_class": args.error_class,
            "module": args.module,
            "severity": severity,
        }
        if args.verbose:
            result["description"] = Severity.DESCRIPTIONS.get(severity, "")
        print(json.dumps(result, indent=2))
    else:
        print(severity)
        if args.verbose:
            desc = Severity.DESCRIPTIONS.get(severity, "")
            if desc:
                print(f"  {desc}")

    return 0


# -----------------------------------------------------------------------------
# Module Entry Point
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main())

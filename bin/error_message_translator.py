#!/usr/bin/env python3
"""error_message_translator.py — Convert internal exception traces into
vendor-friendly remediation messages.

Usage:
    python3 bin/error_message_translator.py [--input FILE] [--format text|json] [--verbose]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RemediationRule:
    """Mapping from an internal exception pattern to a vendor message."""
    pattern: str
    friendly_title: str
    remediation: str
    severity: str = "warning"


_RULES: List[RemediationRule] = [
    RemediationRule("ConnectionRefusedError", "Service Unreachable",
        "Verify the target service is running and the network endpoint is correct. "
        "Check firewall rules and DNS resolution.", "critical"),
    RemediationRule(r"TimeoutError|socket\.timeout", "Operation Timed Out",
        "The request exceeded the allowed time window. Retry with a larger timeout "
        "or investigate network latency and service load.", "warning"),
    RemediationRule(r"PermissionError|AccessDenied", "Access Denied",
        "Insufficient permissions for the requested operation. Confirm that the "
        "service account or user has the required IAM / filesystem roles.", "critical"),
    RemediationRule(r"FileNotFoundError|No such file or directory", "Resource Not Found",
        "The specified file or directory does not exist. Verify the path and ensure "
        "all prerequisites (uploads, mounts) are in place.", "warning"),
    RemediationRule(r"MemoryError|out of memory", "Insufficient Memory",
        "The process ran out of available memory. Reduce input size, increase the "
        "instance memory allocation, or enable swap / paging.", "critical"),
    RemediationRule("ValueError", "Invalid Input Value",
        "One or more input parameters failed validation. Review the request payload "
        "against the API schema and correct any malformed fields.", "info"),
    RemediationRule("KeyError", "Missing Configuration Key",
        "A required configuration key is absent. Check environment variables, "
        "config files, and secret manager entries for completeness.", "warning"),
    RemediationRule(r"ImportError|ModuleNotFoundError", "Missing Dependency",
        "A required Python package is not installed. Run the dependency installer "
        "(e.g. pip install -r requirements.txt) and verify the virtual environment.", "critical"),
    RemediationRule(r"JSONDecodeError|json\.decoder", "Malformed JSON Payload",
        "The supplied JSON could not be parsed. Validate the payload with a JSON "
        "linter and ensure proper escaping of special characters.", "warning"),
    RemediationRule(r"OSError|IOError", "I/O Operation Failed",
        "A low-level input/output error occurred. Check disk space, file permissions, "
        "and storage device health.", "warning"),
]
_FALLBACK = RemediationRule(".*", "Unexpected Error",
    "An unrecognised error occurred. Please collect the full traceback and contact "
    "support with the request ID for further investigation.", "info")


@dataclass
class ParsedTrace:
    """Structured information extracted from a Python traceback string."""
    exception_type: str
    exception_message: str
    frames: List[str] = field(default_factory=list)
    raw: str = ""


_TRACE_RE = re.compile(r"Traceback \(most recent call last\):", re.MULTILINE)
_FRAME_RE = re.compile(r'^\s+File "([^"]+)", line (\d+), in (.+)$', re.MULTILINE)
_EXCEPTION_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Warning)?):(.*)$", re.MULTILINE)


def parse_traceback(raw: str) -> Optional[ParsedTrace]:
    """Extract structured information from a Python traceback string.
    Returns None if no recognisable traceback pattern is found."""
    if not _TRACE_RE.search(raw):
        m = _EXCEPTION_RE.search(raw)
        if m:
            return ParsedTrace(m.group(1).strip(), m.group(2).strip(), raw=raw)
        return None
    frames = [f'File "{f[0]}", line {f[1]}, in {f[2]}' for f in _FRAME_RE.findall(raw)]
    exc_matches = list(_EXCEPTION_RE.finditer(raw))
    if exc_matches:
        last = exc_matches[-1]
        return ParsedTrace(last.group(1).strip(), last.group(2).strip(), frames, raw)
    return ParsedTrace("UnknownError", "", frames, raw)


def translate(trace: ParsedTrace) -> RemediationRule:
    """Match a parsed traceback against the rule set and return the best rule."""
    haystack = f"{trace.exception_type} {trace.exception_message} {trace.raw}"
    for rule in _RULES:
        if re.search(rule.pattern, haystack, re.IGNORECASE):
            return rule
    return _FALLBACK


def format_text(rule: RemediationRule, trace: ParsedTrace, verbose: bool = False) -> str:
    """Render a human-readable vendor-friendly report."""
    lines = [
        "=" * 60,
        f"  [{rule.severity.upper()}] {rule.friendly_title}",
        "=" * 60, "",
        f"Internal exception : {trace.exception_type}",
    ]
    if trace.exception_message:
        lines.append(f"Message            : {trace.exception_message}")
    lines += ["", "Remediation:"]
    lines += [f"  {p}" for p in textwrap.wrap(rule.remediation, width=58)]
    if verbose and trace.frames:
        lines += ["", "Call stack (abbreviated):"]
        lines += [f"  → {fr}" for fr in trace.frames[-5:]]
    lines.append("")
    return "\n".join(lines)


def format_json(rule: RemediationRule, trace: ParsedTrace, verbose: bool = False) -> str:
    """Render a JSON report suitable for programmatic consumption."""
    payload = {
        "severity": rule.severity, "friendly_title": rule.friendly_title,
        "remediation": rule.remediation, "internal_exception": trace.exception_type,
        "message": trace.exception_message or None,
    }
    if verbose and trace.frames:
        payload["call_stack"] = trace.frames[-5:]
    return json.dumps(payload, indent=2, ensure_ascii=False)


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point — parse CLI args, read input, translate, and print output."""
    parser = argparse.ArgumentParser(
        description="Translate internal Python exception traces into vendor-friendly messages.")
    parser.add_argument("--input", "-i", type=argparse.FileType("r", encoding="utf-8"),
        default=sys.stdin, help="Path to a file containing a traceback (default: stdin).")
    parser.add_argument("--format", "-f", choices=["text", "json"], default="text",
        help="Output format (default: text).")
    parser.add_argument("--verbose", "-v", action="store_true",
        help="Include abbreviated call stack in the output.")
    args = parser.parse_args(argv)
    raw = args.input.read()
    args.input.close()
    trace = parse_traceback(raw)
    if trace is None:
        print("error: no recognisable exception traceback found in input.", file=sys.stderr)
        return 1
    rule = translate(trace)
    if args.format == "json":
        print(format_json(rule, trace, verbose=args.verbose))
    else:
        print(format_text(rule, trace, verbose=args.verbose))
    return 0


if __name__ == "__main__":
    sys.exit(main())

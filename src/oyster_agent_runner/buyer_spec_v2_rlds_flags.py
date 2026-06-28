#!/usr/bin/env python3
"""buyer_spec_v2_rlds_flags.py — Cluster A: RLDS episode-boundary flags.

Adds / validates is_first, is_last, is_terminal per record for
Open X-Embodiment (OXE) / RT-X datasets.  Required for OXE pooling.

Usage:
    python buyer_spec_v2_rlds_flags.py -i episodes.json -o flagged.json
    python buyer_spec_v2_rlds_flags.py --validate -i flagged.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

__all__ = ["RLDSFlagProcessor", "main"]


class RLDSFlagProcessor:
    """Add and validate RLDS episode-boundary flags."""

    def __init__(self, strict: bool = True) -> None:
        self.strict = strict

    def add_flags(
        self,
        record: dict[str, Any],
        *,
        is_first: bool = False,
        is_last: bool = False,
        is_terminal: bool | None = None,
    ) -> dict[str, Any]:
        """Return *record* copy with RLDS boundary flags attached."""
        if is_terminal is None:
            is_terminal = is_last
        if self.strict and is_last and not is_terminal:
            raise ValueError("is_last=True but is_terminal=False")
        result = dict(record)
        result.update(is_first=is_first, is_last=is_last, is_terminal=is_terminal)
        return result

    def process_episode(self, steps: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Flag every step: first→is_first, last→is_last+is_terminal."""
        if not steps:
            return []
        n = len(steps)
        return [
            self.add_flags(
                dict(s), is_first=(i == 0), is_last=(i == n - 1), is_terminal=(i == n - 1)
            )
            for i, s in enumerate(steps)
        ]

    def validate(self, record: dict[str, Any]) -> list[str]:
        """Return list of error strings (empty = OK)."""
        errors: list[str] = []
        for key in ("is_first", "is_last", "is_terminal"):
            if key not in record:
                errors.append(f"Missing required flag: {key}")
        if errors:
            return errors
        if record["is_last"] and not record["is_terminal"]:
            errors.append("is_last=True but is_terminal=False")
        return errors

    def extract_boundaries(self, records: Sequence[dict[str, Any]]) -> list[dict[str, int]]:
        """Return [{start, end}, …] for each episode boundary."""
        boundaries: list[dict[str, int]] = []
        start: int | None = None
        for i, rec in enumerate(records):
            if rec.get("is_first"):
                if start is not None:
                    boundaries.append({"start": start, "end": i - 1})
                start = i
            if rec.get("is_last") and start is not None:
                boundaries.append({"start": start, "end": i})
                start = None
        if start is not None:
            boundaries.append({"start": start, "end": len(records) - 1})
        return boundaries


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Add / validate RLDS episode-boundary flags.")
    p.add_argument("--input", "-i", type=Path, required=True, help="Input JSON path.")
    p.add_argument("--output", "-o", type=Path, default=None, help="Output JSON path.")
    p.add_argument("--validate", action="store_true", help="Validate existing flags.")
    p.add_argument("--strict", action="store_true", default=True, help="Strict mode (default).")
    p.add_argument(
        "--no-strict", dest="strict", action="store_false", help="Relax errors to warnings."
    )
    p.add_argument(
        "--format",
        choices=["episodes", "flat"],
        default="episodes",
        help="Input format: episodes (list-of-lists) or flat.",
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry-point. Returns 0 on success, 1 on error."""
    args = _build_parser().parse_args(argv)
    if not args.input.exists():
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        return 1
    with open(args.input, encoding="utf-8") as fh:
        data = json.load(fh)
    proc = RLDSFlagProcessor(strict=args.strict)

    if args.validate:
        records: list[dict[str, Any]] = []
        for ep in data if args.format == "episodes" else [data]:
            records.extend(ep)
        all_errs: list[str] = []
        for idx, rec in enumerate(records):
            for e in proc.validate(rec):
                all_errs.append(f"record[{idx}]: {e}")
        if all_errs:
            for e in all_errs:
                print(e, file=sys.stderr)
            return 1
        print(f"OK — {len(records)} records validated.")
        return 0

    flagged = (
        [proc.process_episode(ep) for ep in data]
        if args.format == "episodes"
        else proc.process_episode(data)
    )
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(flagged, fh, indent=2)
        print(f"Wrote {args.output}")
    else:
        json.dump(flagged, sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""keycode_int_normalizer.py — G163 Cluster A.

Normalize keyCode fields per PRD page 11 (W=87 ASCII):
  • *collapse*  — convert a list-form keyCode array to a single int per record.
  • *expand*    — explode a list-form keyCode array into multiple frame records,
                  one keyCode per row.

Usage (CLI):
    python -m oyster_agent_runner.keycode_int_normalizer \
        --input records.json --output normalized.json --mode collapse

Stdlib only.  Type-hinted, docstring'd, argparse-driven.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Union

# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

KeyCode = Union[int, List[int]]
Record = Dict[str, Any]


def _collapse_keycode(kc: KeyCode) -> int:
    """Return a single int for a keyCode value.

    If *kc* is already an int it is returned unchanged.
    If it is a list the values are summed (PRD page 11 W=87 ASCII
    convention — each element is an ASCII code point; the aggregate
    represents the combined key-press).
    """
    if isinstance(kc, int):
        return kc
    if isinstance(kc, (list, tuple)):
        return sum(kc)
    raise TypeError(f"keyCode must be int or list, got {type(kc).__name__}")


def _expand_keycode(record: Record, key: str = "keyCode") -> List[Record]:
    """Expand one record whose *key* holds a list into N records.

    Each output record is a shallow copy of the input with *key*
    replaced by a single int element.  If the value is already a
    scalar the original record is returned as a single-element list.
    """
    val = record.get(key)
    if val is None:
        return [dict(record)]
    if isinstance(val, int):
        return [dict(record)]
    if isinstance(val, (list, tuple)):
        frames: List[Record] = []
        for idx, code in enumerate(val):
            frame = dict(record)
            frame[key] = int(code)
            frame.setdefault("_frame_idx", idx)
            frames.append(frame)
        return frames
    raise TypeError(f"{key} must be int or list, got {type(val).__name__}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize(records: List[Record], mode: str = "collapse", key: str = "keyCode") -> List[Record]:
    """Normalize keyCode fields across *records*.

    Parameters
    ----------
    records:
        List of dicts, each potentially containing a *key* field.
    mode:
        ``"collapse"`` → single int per record.
        ``"expand"``   → one record per keyCode element.
    key:
        Field name holding the keyCode value (default ``"keyCode"``).

    Returns
    -------
    A new list of normalized records.
    """
    if mode == "collapse":
        out: List[Record] = []
        for rec in records:
            new_rec = dict(rec)
            if key in new_rec:
                new_rec[key] = _collapse_keycode(new_rec[key])
            out.append(new_rec)
        return out
    if mode == "expand":
        out = []
        for rec in records:
            out.extend(_expand_keycode(rec, key=key))
        return out
    raise ValueError(f"Unknown mode {mode!r}; use 'collapse' or 'expand'")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: List[str] | None = None) -> int:
    """Entry-point for command-line usage."""
    p = argparse.ArgumentParser(description="Normalize keyCode arrays (collapse or expand).")
    p.add_argument(
        "--input", "-i", required=True, help="Path to input JSON file (list of records)."
    )
    p.add_argument("--output", "-o", required=True, help="Path to output JSON file.")
    p.add_argument(
        "--mode",
        "-m",
        choices=("collapse", "expand"),
        default="collapse",
        help="Normalization mode (default: collapse).",
    )
    p.add_argument(
        "--key", "-k", default="keyCode", help="Field name holding keyCode (default: keyCode)."
    )
    args = p.parse_args(argv)

    inp = Path(args.input)
    if not inp.is_file():
        print(f"Error: input file not found: {inp}", file=sys.stderr)
        return 1

    records: List[Record] = json.loads(inp.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        print("Error: input JSON must be a list of records.", file=sys.stderr)
        return 1

    normalized = normalize(records, mode=args.mode, key=args.key)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(normalized)} record(s) → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

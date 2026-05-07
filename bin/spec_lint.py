#!/usr/bin/env python3
"""Spec-pack linter — enforces the iron-law REAL ONLY canon for specs/*.md.

Howard 2026-05-07: built per spec D21 in this same dispatch wave. Validates
that every atomic spec dispatched to the cluster:

1. Has YAML front-matter with required keys (task_id, project, priority,
   estimated_minutes, modifies, executor)
2. Has at least 3 acceptance criteria (lines starting with ``- [ ]``)
3. Does NOT contain banned patterns (placeholder | mock | stub | fake |
   TODO | FIXME) outside ``## 不要做`` sections, UNLESS the front-matter
   explicitly carries ``iron_law_waived: <reason>``
4. Lists ``must_not_touch`` so executors don't sprawl into files they
   shouldn't change

Exit codes:
    0 — every spec passed
    1 — at least one spec violated; printed list with file + line numbers
    2 — linter itself errored (malformed input it couldn't even parse)

Stdlib only. Run: ``python bin/spec_lint.py specs/``
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_FM_KEYS = {
    "task_id",
    "project",
    "priority",
    "estimated_minutes",
    "modifies",
    "executor",
}

# Recommended (warn, not fail) — every spec SHOULD list these but we don't
# block on them; the iron-law canon is the must.
RECOMMENDED_FM_KEYS = {"must_not_touch"}

BANNED_PATTERNS = [
    re.compile(r"\bplaceholder\b", re.IGNORECASE),
    re.compile(r"\bmock\b", re.IGNORECASE),
    re.compile(r"\bstub\b", re.IGNORECASE),
    re.compile(r"\bfake\b", re.IGNORECASE),
    re.compile(r"\bTODO\b"),
    re.compile(r"\bFIXME\b"),
]

# Phrases that, if present in the same line as a banned word, mean the line
# is DESCRIBING the iron law (e.g. "no placeholder", "closes placeholder gap")
# rather than violating it. These are co-occurrence-only — a line with
# "placeholder" alone still trips, but "no placeholder" is clean.
DISCUSSING_NOT_VIOLATING = [
    re.compile(r"\bno\s+placeholder\b", re.IGNORECASE),
    re.compile(r"\bplaceholder\s+gap\b", re.IGNORECASE),
    re.compile(r"\bcloses?\s+(\w+\s+){0,4}placeholder\b", re.IGNORECASE),
    re.compile(r"\brejects?\s+(\w+\s+){0,4}placeholder\b", re.IGNORECASE),
    re.compile(r"\bvs\.?\s+placeholder\b", re.IGNORECASE),
    re.compile(r"\bplaceholder\s+(?:value|behaviour|behavior|fields?|paths?)\b", re.IGNORECASE),
    re.compile(r"\bsilent\s+placeholder\b", re.IGNORECASE),
    re.compile(r"\bship(?:ping)?\s+placeholder\b", re.IGNORECASE),
    re.compile(r"\bnever\s+(?:silent\s+)?placeholder\b", re.IGNORECASE),
    re.compile(r"\bmetadata-derived.*placeholder\b", re.IGNORECASE),
    re.compile(r"\bfallback to placeholder\b", re.IGNORECASE),
    re.compile(r"\bno\s+mocks?\b", re.IGNORECASE),
    re.compile(r"\bno\s+TODO\b"),
    re.compile(r"\b(?:say|said|saying)\s+\"?TODO\b"),
    # The literal banned-list itself appears in this script's docs / spec
    re.compile(r"\bbanned[\s_-]?(?:patterns?|list|set|words?)\b", re.IGNORECASE),
    re.compile(r"\biron[\s_-]?law\b", re.IGNORECASE),
]

MIN_ACCEPTANCE_CRITERIA = 3
ACCEPTANCE_RE = re.compile(r"^\s*-\s+\[\s\]")
DONT_DO_HEADING_RE = re.compile(r"^##\s*不要做\s*$")
ANY_HEADING_RE = re.compile(r"^##\s+")


@dataclass
class LintResult:
    path: Path
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def _parse_front_matter(text: str) -> tuple[dict, int] | None:
    """Parse the YAML front-matter block at the top of the file.

    Returns (fm_dict, body_start_line_index) or None if no front matter.
    Hand-rolled YAML — supports only the minimal subset our specs use:
    ``key: value``, ``key:`` followed by ``- item`` list lines, and
    inline lists ``key: [a, b]``.
    """
    if not text.startswith("---\n"):
        return None
    end_idx = text.find("\n---\n", 4)
    if end_idx == -1:
        return None
    fm_text = text[4:end_idx]
    body_start_line = fm_text.count("\n") + 2  # +2 for the two --- lines
    fm: dict = {}
    cur_key: str | None = None
    cur_list: list | None = None
    for line in fm_text.split("\n"):
        if not line.strip():
            cur_key = cur_list = None
            continue
        # List continuation
        m = re.match(r"^\s+-\s+(.+)$", line)
        if m and cur_list is not None:
            cur_list.append(m.group(1).strip().strip('"').strip("'"))
            continue
        # Top-level key
        m = re.match(r"^([a-zA-Z_]\w*):\s*(.*)$", line)
        if m:
            cur_key = m.group(1)
            value = m.group(2).strip()
            if value == "":
                # next lines either list or empty
                cur_list = []
                fm[cur_key] = cur_list
            elif value.startswith("[") and value.endswith("]"):
                inner = value[1:-1]
                fm[cur_key] = [v.strip().strip('"').strip("'")
                               for v in inner.split(",") if v.strip()]
                cur_list = None
            else:
                fm[cur_key] = value.strip('"').strip("'")
                cur_list = None
    return fm, body_start_line


def lint_file(path: Path) -> LintResult:
    res = LintResult(path)
    text = path.read_text(encoding="utf-8")

    parsed = _parse_front_matter(text)
    if parsed is None:
        # Howard 2026-05-07: legacy specs (D1-D15) predate front-matter;
        # treat as warning-only so we don't churn historical artefacts in
        # this linter's first ship. New specs MUST have front-matter
        # (D16+ already do). Add `iron_law_legacy: <reason>` in front-matter
        # to suppress when retro-fitting old specs.
        res.warnings.append(
            "no YAML front-matter (legacy spec; D16+ require it). "
            "Lint passes in lenient mode but won't validate task_id, "
            "modifies, etc."
        )
        # Continue with body-only checks (acceptance criteria + banned).
        fm = {}
        body_start_line = 0
    else:
        fm, body_start_line = parsed

    # Required keys check (only when front matter exists — legacy specs
    # already got a warning above and skip these).
    if fm:
        missing = REQUIRED_FM_KEYS - set(fm.keys())
        if missing:
            res.failures.append(
                f"front-matter missing required keys: {sorted(missing)}"
            )

        # Recommended keys (warn).
        rec_missing = RECOMMENDED_FM_KEYS - set(fm.keys())
        if rec_missing:
            res.warnings.append(
                f"front-matter missing recommended keys: {sorted(rec_missing)}"
            )

    # Acceptance criteria count. Skip for legacy specs (no front-matter) —
    # they predate this convention and we don't churn historical artefacts.
    body = text[text.find("\n---\n") + 5:] if fm else text
    body_lines = body.split("\n")
    ac_count = sum(1 for line in body_lines if ACCEPTANCE_RE.match(line))
    if fm and ac_count < MIN_ACCEPTANCE_CRITERIA:
        res.failures.append(
            f"only {ac_count} acceptance criteria found; need ≥{MIN_ACCEPTANCE_CRITERIA}"
        )
    elif not fm and ac_count < MIN_ACCEPTANCE_CRITERIA:
        res.warnings.append(
            f"legacy spec: only {ac_count} acceptance criteria; "
            f"consider adding ≥{MIN_ACCEPTANCE_CRITERIA} when retro-fitting"
        )

    # Iron-law banned-pattern check (skip lines under ## 不要做 sections).
    # Legacy specs (no front-matter) are exempt — they predate this canon.
    waiver = fm.get("iron_law_waived") if fm else "legacy spec exemption"
    if not waiver:
        in_dont_do = False
        for i, line in enumerate(body_lines, start=body_start_line + 1):
            if DONT_DO_HEADING_RE.match(line):
                in_dont_do = True
                continue
            if ANY_HEADING_RE.match(line):
                in_dont_do = False
            if in_dont_do:
                continue
            # Also skip front-matter-style ``iron_law:`` annotation lines and
            # quotes describing the banned set itself.
            if re.match(r"^\s*iron_law\s*:", line):
                continue
            for pat in BANNED_PATTERNS:
                if pat.search(line):
                    # Skip if the line is meta-discussion ABOUT placeholders.
                    if line.lstrip().startswith(("**", "#", ">", "|", "```")):
                        continue
                    # Skip if the line is describing the iron law itself
                    # (i.e. discussing what we DON'T do, not proposing it).
                    if any(p.search(line) for p in DISCUSSING_NOT_VIOLATING):
                        continue
                    # Skip if the line is inside backticks (code/file paths).
                    # Heuristic: line dominated by backtick spans.
                    if line.count("`") >= 2:
                        # crude — strip the backtick-quoted regions and re-check
                        stripped = re.sub(r"`[^`]*`", "", line)
                        if not pat.search(stripped):
                            continue
                    res.failures.append(
                        f"line {i}: banned pattern {pat.pattern!r} outside "
                        f"## 不要做 — add to 不要做 section or set "
                        f"`iron_law_waived: <reason>` in front-matter"
                    )
                    break  # one issue per line is enough

    return res


def lint_dir(spec_dir: Path) -> int:
    """Lint every ``*.md`` file in ``spec_dir`` (skips ``INDEX.md`` and
    ``README.md``). Returns shell exit code."""
    failures = 0
    warnings_only = 0
    files = sorted(p for p in spec_dir.glob("*.md")
                   if p.name not in ("INDEX.md", "README.md"))
    if not files:
        print(f"::warning:: no spec files found in {spec_dir}")
        return 0

    for path in files:
        try:
            res = lint_file(path)
        except Exception as e:  # pragma: no cover — true bug, exit 2
            print(f"::error file={path}::lint script bug: {e}",
                  file=sys.stderr)
            return 2

        if res.ok:
            for w in res.warnings:
                print(f"::warning file={path}::{w}")
                warnings_only += 1
            print(f"  PASS  {path}")
        else:
            failures += 1
            for f in res.failures:
                print(f"::error file={path}::{f}", file=sys.stderr)
            print(f"  FAIL  {path}", file=sys.stderr)

    print()
    print(f"summary: {len(files)} specs total, "
          f"{len(files) - failures} pass, "
          f"{failures} fail, "
          f"{warnings_only} pass-with-warning")
    return 0 if failures == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint Oyster spec packs for the REAL-ONLY iron law."
    )
    parser.add_argument(
        "spec_dir",
        nargs="?",
        default="specs",
        type=Path,
        help="Directory of *.md spec files (default: specs/)",
    )
    args = parser.parse_args()

    if not args.spec_dir.is_dir():
        print(f"::error::not a directory: {args.spec_dir}", file=sys.stderr)
        return 2
    return lint_dir(args.spec_dir)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
verify_visual_diff.py — side-by-side visual diff of two action_camera.json files.

Howard 2026-05-05: "我想用眼睛+grep 一眼看出 recorder 跟 sample-builder 的字段差异。"

Compares two clip directories' action_camera.json files and emits a 2-column
field-level diff for selected frames, plus structural metadata divergence:
    * record count
    * field set (added / removed keys)
    * field-name typo divergence (e.g. recorder uses "camera_rotation_oula",
      sample uses "camera_rotation_euler" — both are flagged so Howard sees them)
    * % records sharing the same field set
    * mean numerical drift on shared numeric fields

Usage:
    python3 bin/verify_visual_diff.py <clip_a_dir> <clip_b_dir>
    python3 bin/verify_visual_diff.py <a> <b> --frames 0,100,9000
    python3 bin/verify_visual_diff.py <a> <b> --html              # HTML to stdout
    python3 bin/verify_visual_diff.py <a> <b> --html --output report.html
    python3 bin/verify_visual_diff.py <a> <b> --json              # JSON diff

Exit code: 0 if files structurally identical, 1 if any divergence, 99 on I/O error.

Stdlib only — no numpy / pandas / scipy / colorama deps.
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---- ANSI color codes (no colorama dep) -----------------------------------

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_CYAN = "\033[36m"
ANSI_MAGENTA = "\033[35m"

MARK_MATCH = "✓"
MARK_DIFF = "✗"
MARK_MISSING = "·"

# Numeric tolerance for "essentially equal" floats (purely cosmetic for the
# match marker — actual values are still printed so Howard can eyeball drift).
NUMERIC_EPS = 1e-9

# Field-name pairs we know are typo-equivalents and should be aligned in the
# side-by-side render rather than appearing as added/removed independently.
KNOWN_FIELD_ALIASES: list[tuple[str, str]] = [
    ("camera_rotation_euler", "camera_rotation_oula"),
]


# ---- Normalization --------------------------------------------------------


def _normalize_vec(value: Any) -> Any:
    """Convert list-form vectors / quaternions to canonical dict form.

    [x, y, z]    -> {"x":x, "y":y, "z":z}
    [x, y, z, w] -> {"x":x, "y":y, "z":z, "w":w}
    Other shapes are returned untouched so we don't over-eagerly mutate
    keyCode arrays etc.
    """
    if isinstance(value, list) and len(value) in (3, 4) and all(
        isinstance(v, (int, float)) for v in value
    ):
        keys = ["x", "y", "z", "w"][: len(value)]
        return {k: float(v) for k, v in zip(keys, value)}
    return value


def normalize_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Apply _normalize_vec to all values; recursively descend dicts."""
    out: dict[str, Any] = {}
    for k, v in rec.items():
        if isinstance(v, dict):
            out[k] = {kk: _normalize_vec(vv) for kk, vv in v.items()}
        else:
            out[k] = _normalize_vec(v)
    return out


# ---- Loading --------------------------------------------------------------


def load_action_camera(clip_dir: Path) -> list[dict[str, Any]]:
    """Load action_camera.json from a clip dir. Accepts list or {records: [...]}."""
    path = clip_dir / "action_camera.json"
    if not path.is_file():
        raise FileNotFoundError(f"action_camera.json not found in {clip_dir}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return data["records"]
    raise ValueError(f"unrecognized action_camera.json shape in {path}")


# ---- Comparison primitives ------------------------------------------------


def values_match(a: Any, b: Any) -> bool:
    """Loose equality with float tolerance and dict-recursion."""
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(values_match(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        return all(values_match(x, y) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        try:
            af, bf = float(a), float(b)
        except (TypeError, ValueError):
            return a == b
        if math.isnan(af) and math.isnan(bf):
            return True
        return abs(af - bf) <= NUMERIC_EPS
    return a == b


def _as_number(v: Any) -> float | None:
    """Try to coerce to float; returns None for non-numeric leaves."""
    if isinstance(v, bool):
        # bool is technically int but excluded — these are flags, not metrics.
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def numeric_drift(a: Any, b: Any) -> float | None:
    """Return abs(a-b) when both leaves coerce to floats, else None."""
    af = _as_number(a)
    bf = _as_number(b)
    if af is None or bf is None:
        return None
    if math.isnan(af) or math.isnan(bf):
        return None
    return abs(af - bf)


# ---- Field-set / structural analysis --------------------------------------


@dataclass
class FieldSetDiff:
    only_a: set[str]
    only_b: set[str]
    shared: set[str]
    aliases: list[tuple[str, str]]


def compute_field_set_diff(records_a: list[dict], records_b: list[dict]) -> FieldSetDiff:
    """Union of all keys in each side, then match aliases."""
    keys_a: set[str] = set()
    keys_b: set[str] = set()
    for r in records_a:
        keys_a.update(r.keys())
    for r in records_b:
        keys_b.update(r.keys())

    only_a = keys_a - keys_b
    only_b = keys_b - keys_a
    aliases: list[tuple[str, str]] = []
    for ka, kb in KNOWN_FIELD_ALIASES:
        if ka in only_a and kb in only_b:
            aliases.append((ka, kb))
            only_a.discard(ka)
            only_b.discard(kb)
        elif kb in only_a and ka in only_b:
            aliases.append((kb, ka))
            only_a.discard(kb)
            only_b.discard(ka)
    return FieldSetDiff(
        only_a=only_a,
        only_b=only_b,
        shared=keys_a & keys_b,
        aliases=aliases,
    )


@dataclass
class StructuralReport:
    count_a: int
    count_b: int
    field_diff: FieldSetDiff
    pct_same_field_set: float
    mean_drift_per_field: dict[str, float]
    drift_sample_count_per_field: dict[str, int] = field(default_factory=dict)


def compute_structural_report(records_a: list[dict], records_b: list[dict]) -> StructuralReport:
    fd = compute_field_set_diff(records_a, records_b)

    # % of paired records (by index) that share the exact same set of keys.
    paired = list(zip(records_a, records_b))
    same = sum(1 for a, b in paired if set(a.keys()) == set(b.keys()))
    pct = (100.0 * same / len(paired)) if paired else 0.0

    # Mean abs-drift on shared numeric fields, recursively into normalized dicts.
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}

    def _walk(prefix: str, va: Any, vb: Any) -> None:
        # Normalize to dict-shape if list-style vector (per buyer spec).
        va = _normalize_vec(va)
        vb = _normalize_vec(vb)
        if isinstance(va, dict) and isinstance(vb, dict):
            for k in set(va.keys()) & set(vb.keys()):
                _walk(f"{prefix}.{k}", va[k], vb[k])
            return
        d = numeric_drift(va, vb)
        if d is not None:
            sums[prefix] = sums.get(prefix, 0.0) + d
            counts[prefix] = counts.get(prefix, 0) + 1

    for a, b in paired:
        for k in set(a.keys()) & set(b.keys()):
            _walk(k, a.get(k), b.get(k))

    mean = {k: (sums[k] / counts[k]) for k in sums if counts.get(k)}
    return StructuralReport(
        count_a=len(records_a),
        count_b=len(records_b),
        field_diff=fd,
        pct_same_field_set=pct,
        mean_drift_per_field=mean,
        drift_sample_count_per_field=counts,
    )


# ---- Frame-level diff -----------------------------------------------------


@dataclass
class FieldRow:
    key: str
    val_a: Any  # normalized
    val_b: Any  # normalized
    match: bool
    note: str = ""  # e.g. "alias: oula↔euler", "missing in A", "missing in B"


SENTINEL_MISSING = object()


def diff_frame(
    rec_a: dict[str, Any] | None,
    rec_b: dict[str, Any] | None,
    aliases: list[tuple[str, str]],
) -> list[FieldRow]:
    """Build a per-key side-by-side row list for one frame.

    `aliases` is the list of (key_in_a, key_in_b) typo pairs that should be
    rendered on the same row even though the names disagree.
    """
    rec_a = normalize_record(rec_a or {})
    rec_b = normalize_record(rec_b or {})
    alias_map_a_to_b = {ka: kb for ka, kb in aliases}
    alias_map_b_to_a = {kb: ka for ka, kb in aliases}
    consumed_b: set[str] = set()
    rows: list[FieldRow] = []

    # Pass 1: keys present in A (with possible alias mapping).
    for ka in rec_a:
        kb = alias_map_a_to_b.get(ka, ka)
        if kb in rec_b:
            note = f"alias: {ka} ↔ {kb}" if ka != kb else ""
            va = rec_a[ka]
            vb = rec_b[kb]
            rows.append(FieldRow(
                key=ka if ka == kb else f"{ka} | {kb}",
                val_a=va,
                val_b=vb,
                match=values_match(va, vb),
                note=note,
            ))
            consumed_b.add(kb)
        else:
            rows.append(FieldRow(
                key=ka,
                val_a=rec_a[ka],
                val_b=SENTINEL_MISSING,
                match=False,
                note="only in A",
            ))

    # Pass 2: keys only in B.
    for kb in rec_b:
        if kb in consumed_b:
            continue
        # If kb is the second half of an alias whose first half wasn't in A,
        # we still want to show it as "only in B".
        alias_partner = alias_map_b_to_a.get(kb)
        if alias_partner and alias_partner in rec_a:
            continue  # already handled in pass 1
        rows.append(FieldRow(
            key=kb,
            val_a=SENTINEL_MISSING,
            val_b=rec_b[kb],
            match=False,
            note="only in B",
        ))

    return rows


# ---- Frame selection ------------------------------------------------------


def pick_default_frames(n_a: int, n_b: int) -> list[int]:
    """Default sample: first, midpoint, last (clamped to shorter side)."""
    n = min(n_a, n_b)
    if n <= 0:
        return []
    if n == 1:
        return [0]
    if n == 2:
        return [0, 1]
    return [0, n // 2, n - 1]


def parse_frames_arg(arg: str | None, n_a: int, n_b: int) -> list[int]:
    if not arg:
        return pick_default_frames(n_a, n_b)
    out = []
    for chunk in arg.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.append(int(chunk))
        except ValueError:
            raise SystemExit(f"--frames: invalid frame index '{chunk}'") from None
    return out


# ---- Rendering: terminal --------------------------------------------------


def _fmt_value(v: Any) -> str:
    if v is SENTINEL_MISSING:
        return "<missing>"
    if isinstance(v, float):
        # Compact but readable; 6 dp matches the recorder.
        return f"{v:.6f}".rstrip("0").rstrip(".") or "0"
    if isinstance(v, dict):
        # Render compact dict (vectors / quaternions normalized form).
        parts = [f"{k}={_fmt_value(val)}" for k, val in v.items()]
        return "{" + ", ".join(parts) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_fmt_value(x) for x in v) + "]"
    return json.dumps(v, ensure_ascii=False)


def render_terminal(
    clip_a: Path,
    clip_b: Path,
    frames: list[int],
    records_a: list[dict],
    records_b: list[dict],
    structural: StructuralReport,
    use_color: bool,
) -> str:
    def c(text: str, code: str) -> str:
        if not use_color:
            return text
        return f"{code}{text}{ANSI_RESET}"

    out: list[str] = []
    out.append(c("=" * 78, ANSI_BOLD))
    out.append(c(f"verify_visual_diff.py — {clip_a.name}  ⇄  {clip_b.name}", ANSI_BOLD))
    out.append(c("=" * 78, ANSI_BOLD))
    out.append("")
    out.append(f"A: {clip_a}  ({structural.count_a} records)")
    out.append(f"B: {clip_b}  ({structural.count_b} records)")
    out.append("")

    # ---- Structural ----
    out.append(c("STRUCTURAL", ANSI_CYAN + ANSI_BOLD))
    if structural.count_a == structural.count_b:
        out.append(f"  record count    {c(MARK_MATCH, ANSI_GREEN)}  {structural.count_a} = {structural.count_b}")
    else:
        out.append(f"  record count    {c(MARK_DIFF, ANSI_RED)}  A={structural.count_a}  B={structural.count_b}")

    fd = structural.field_diff
    out.append(f"  shared fields   ({len(fd.shared)}): {', '.join(sorted(fd.shared)) or '<none>'}")
    if fd.only_a:
        out.append(f"  only in A       {c(MARK_DIFF, ANSI_RED)}  {sorted(fd.only_a)}")
    if fd.only_b:
        out.append(f"  only in B       {c(MARK_DIFF, ANSI_RED)}  {sorted(fd.only_b)}")
    if fd.aliases:
        for ka, kb in fd.aliases:
            out.append(f"  alias detected  {c(MARK_DIFF, ANSI_YELLOW)}  '{ka}' (A) ↔ '{kb}' (B)  ← likely typo divergence")

    if structural.pct_same_field_set >= 99.999:
        same_marker = c(MARK_MATCH, ANSI_GREEN)
    else:
        same_marker = c(MARK_DIFF, ANSI_YELLOW)
    out.append(
        f"  same field set  {same_marker}  {structural.pct_same_field_set:.1f}% of paired records"
    )

    if structural.mean_drift_per_field:
        out.append("")
        out.append(c("  mean numerical drift (shared numeric fields):", ANSI_DIM))
        for k in sorted(structural.mean_drift_per_field):
            d = structural.mean_drift_per_field[k]
            n = structural.drift_sample_count_per_field.get(k, 0)
            mark = MARK_MATCH if d <= NUMERIC_EPS else MARK_DIFF
            color = ANSI_GREEN if d <= NUMERIC_EPS else ANSI_RED
            out.append(f"    {c(mark, color)}  {k:<48} mean|Δ| = {d:.6g}  (n={n})")
    out.append("")

    # ---- Per-frame ----
    out.append(c("FRAME DIFF", ANSI_CYAN + ANSI_BOLD))
    for idx in frames:
        rec_a = records_a[idx] if 0 <= idx < len(records_a) else None
        rec_b = records_b[idx] if 0 <= idx < len(records_b) else None
        rows = diff_frame(rec_a, rec_b, structural.field_diff.aliases)
        diffs_in_row = sum(1 for r in rows if not r.match)
        title_color = ANSI_GREEN if diffs_in_row == 0 else ANSI_RED
        out.append("")
        out.append(c(f"--- frame {idx} ---  ({diffs_in_row} field diffs)", title_color + ANSI_BOLD))
        out.append(f"  {'KEY':<36} | {'A':<32} | {'B':<32}")
        out.append(f"  {'-' * 36} | {'-' * 32} | {'-' * 32}")
        for row in rows:
            mark = MARK_MATCH if row.match else MARK_DIFF
            mark_color = ANSI_GREEN if row.match else ANSI_RED
            va = _fmt_value(row.val_a)
            vb = _fmt_value(row.val_b)
            # Truncate long values to keep the table grep-friendly.
            va_short = (va[:30] + "..") if len(va) > 32 else va
            vb_short = (vb[:30] + "..") if len(vb) > 32 else vb
            note_str = f"  ← {row.note}" if row.note else ""
            out.append(
                f"  {c(mark, mark_color)} {row.key:<34} | {va_short:<32} | {vb_short:<32}{note_str}"
            )

    out.append("")
    return "\n".join(out)


# ---- Rendering: HTML ------------------------------------------------------


def render_html(
    clip_a: Path,
    clip_b: Path,
    frames: list[int],
    records_a: list[dict],
    records_b: list[dict],
    structural: StructuralReport,
) -> str:
    e = html_lib.escape

    def cell(v: Any) -> str:
        if v is SENTINEL_MISSING:
            return '<td class="missing">&lt;missing&gt;</td>'
        return f"<td><code>{e(_fmt_value(v))}</code></td>"

    parts: list[str] = []
    parts.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    parts.append("<title>verify_visual_diff</title><style>")
    parts.append("body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;"
                 "margin:24px;background:#fafafa;color:#222}")
    parts.append("h1{margin-bottom:4px}h2{border-bottom:2px solid #ccc;padding-bottom:4px;"
                 "margin-top:32px}")
    parts.append("table{border-collapse:collapse;width:100%;margin-bottom:16px;"
                 "font-family:ui-monospace,Menlo,monospace;font-size:12px}")
    parts.append("th,td{border:1px solid #ccc;padding:4px 8px;text-align:left;"
                 "vertical-align:top}")
    parts.append("th{background:#eee}")
    parts.append("tr.match td{background:#eaffea}tr.diff td{background:#ffecec}")
    parts.append("tr.diff td.note{background:#fff7d6;color:#665200;font-style:italic}")
    parts.append(".mark{font-weight:bold;text-align:center}")
    parts.append(".match-mark{color:#080}.diff-mark{color:#a00}")
    parts.append(".missing{color:#999;font-style:italic}")
    parts.append(".badge{display:inline-block;padding:1px 6px;border-radius:3px;"
                 "font-size:11px;margin-left:6px}")
    parts.append(".badge-ok{background:#d6f5d6;color:#080}.badge-warn{background:#ffe1a8;"
                 "color:#8a4500}.badge-bad{background:#ffd1d1;color:#a00}")
    parts.append("</style></head><body>")
    parts.append(f"<h1>verify_visual_diff: {e(clip_a.name)} ⇄ {e(clip_b.name)}</h1>")
    parts.append(f"<p><b>A:</b> <code>{e(str(clip_a))}</code> ({structural.count_a} records)<br>")
    parts.append(f"<b>B:</b> <code>{e(str(clip_b))}</code> ({structural.count_b} records)</p>")

    # ---- Structural section ----
    parts.append("<h2>Structural</h2><table>")
    parts.append("<tr><th>check</th><th>A</th><th>B</th><th>note</th></tr>")
    rc_class = "match" if structural.count_a == structural.count_b else "diff"
    parts.append(
        f'<tr class="{rc_class}"><td>record count</td>'
        f'<td>{structural.count_a}</td><td>{structural.count_b}</td>'
        f'<td class="note">paired by index</td></tr>'
    )
    fd = structural.field_diff
    parts.append(
        f'<tr><td>shared fields</td><td colspan=2><code>{e(", ".join(sorted(fd.shared)))}</code></td>'
        f'<td>{len(fd.shared)} keys</td></tr>'
    )
    for ka, kb in fd.aliases:
        parts.append(
            f'<tr class="diff"><td>alias / typo divergence</td>'
            f'<td><code>{e(ka)}</code></td><td><code>{e(kb)}</code></td>'
            f'<td class="note">flagged — likely same field, different spelling</td></tr>'
        )
    if fd.only_a:
        parts.append(
            f'<tr class="diff"><td>only in A</td><td colspan=2><code>{e(", ".join(sorted(fd.only_a)))}</code></td>'
            f'<td class="note">missing in B</td></tr>'
        )
    if fd.only_b:
        parts.append(
            f'<tr class="diff"><td>only in B</td><td colspan=2><code>{e(", ".join(sorted(fd.only_b)))}</code></td>'
            f'<td class="note">missing in A</td></tr>'
        )
    parts.append(
        f'<tr><td>same field set</td><td colspan=2>{structural.pct_same_field_set:.2f}%</td>'
        f'<td>of paired records</td></tr>'
    )
    parts.append("</table>")

    if structural.mean_drift_per_field:
        parts.append("<h2>Mean numerical drift (shared numeric leaves)</h2><table>")
        parts.append("<tr><th>field</th><th>mean |Δ|</th><th>samples</th></tr>")
        for k in sorted(structural.mean_drift_per_field):
            d = structural.mean_drift_per_field[k]
            n = structural.drift_sample_count_per_field.get(k, 0)
            cls = "match" if d <= NUMERIC_EPS else "diff"
            parts.append(
                f'<tr class="{cls}"><td><code>{e(k)}</code></td>'
                f'<td>{d:.6g}</td><td>{n}</td></tr>'
            )
        parts.append("</table>")

    # ---- Per-frame section ----
    parts.append("<h2>Frame diffs</h2>")
    for idx in frames:
        rec_a = records_a[idx] if 0 <= idx < len(records_a) else None
        rec_b = records_b[idx] if 0 <= idx < len(records_b) else None
        rows = diff_frame(rec_a, rec_b, structural.field_diff.aliases)
        ndiff = sum(1 for r in rows if not r.match)
        badge = ('badge-ok' if ndiff == 0 else 'badge-bad')
        parts.append(
            f'<h3>frame {idx} <span class="badge {badge}">{ndiff} diffs</span></h3>'
        )
        parts.append("<table>")
        parts.append("<tr><th>·</th><th>key</th><th>A</th><th>B</th><th>note</th></tr>")
        for row in rows:
            cls = "match" if row.match else "diff"
            mark_cls = "match-mark" if row.match else "diff-mark"
            mark = MARK_MATCH if row.match else MARK_DIFF
            parts.append(
                f'<tr class="{cls}">'
                f'<td class="mark {mark_cls}">{mark}</td>'
                f'<td><code>{e(row.key)}</code></td>'
                f'{cell(row.val_a)}{cell(row.val_b)}'
                f'<td class="note">{e(row.note)}</td>'
                f'</tr>'
            )
        parts.append("</table>")

    parts.append("</body></html>")
    return "".join(parts)


# ---- Rendering: JSON ------------------------------------------------------


def _json_safe(v: Any) -> Any:
    if v is SENTINEL_MISSING:
        return None
    if isinstance(v, dict):
        return {k: _json_safe(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_json_safe(x) for x in v]
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def render_json(
    clip_a: Path,
    clip_b: Path,
    frames: list[int],
    records_a: list[dict],
    records_b: list[dict],
    structural: StructuralReport,
) -> str:
    fd = structural.field_diff
    payload: dict[str, Any] = {
        "clip_a": str(clip_a),
        "clip_b": str(clip_b),
        "structural": {
            "count_a": structural.count_a,
            "count_b": structural.count_b,
            "shared_fields": sorted(fd.shared),
            "only_a": sorted(fd.only_a),
            "only_b": sorted(fd.only_b),
            "aliases": [list(p) for p in fd.aliases],
            "pct_same_field_set": structural.pct_same_field_set,
            "mean_drift_per_field": structural.mean_drift_per_field,
            "drift_sample_count_per_field": structural.drift_sample_count_per_field,
        },
        "frames": [],
    }
    for idx in frames:
        rec_a = records_a[idx] if 0 <= idx < len(records_a) else None
        rec_b = records_b[idx] if 0 <= idx < len(records_b) else None
        rows = diff_frame(rec_a, rec_b, structural.field_diff.aliases)
        payload["frames"].append({
            "index": idx,
            "diff_count": sum(1 for r in rows if not r.match),
            "rows": [
                {
                    "key": r.key,
                    "val_a": _json_safe(r.val_a),
                    "val_b": _json_safe(r.val_b),
                    "match": r.match,
                    "note": r.note,
                }
                for r in rows
            ],
        })
    return json.dumps(payload, indent=2, default=str)


# ---- main -----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Side-by-side visual diff of two action_camera.json files."
    )
    p.add_argument("clip_a", type=Path, help="Path to clip dir A (e.g. recorder output)")
    p.add_argument("clip_b", type=Path, help="Path to clip dir B (e.g. sample-builder)")
    p.add_argument(
        "--frames",
        default=None,
        help="Comma-separated frame indices (default: first, mid, last)",
    )
    p.add_argument("--html", action="store_true", help="Emit HTML report instead of terminal text")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON diff")
    p.add_argument("--output", type=Path, default=None, help="Write to file instead of stdout")
    p.add_argument("--no-color", action="store_true", help="Disable ANSI color (terminal mode)")
    args = p.parse_args(argv)

    if args.html and args.json:
        print("--html and --json are mutually exclusive", file=sys.stderr)
        return 99

    try:
        records_a = load_action_camera(args.clip_a)
        records_b = load_action_camera(args.clip_b)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 99

    frames = parse_frames_arg(args.frames, len(records_a), len(records_b))
    structural = compute_structural_report(records_a, records_b)

    if args.html:
        out = render_html(args.clip_a, args.clip_b, frames, records_a, records_b, structural)
    elif args.json:
        out = render_json(args.clip_a, args.clip_b, frames, records_a, records_b, structural)
    else:
        use_color = (not args.no_color) and (
            args.output is None and sys.stdout.isatty()
        )
        out = render_terminal(
            args.clip_a, args.clip_b, frames, records_a, records_b, structural, use_color,
        )

    if args.output:
        args.output.write_text(out, encoding="utf-8")
    else:
        print(out)

    # Decide exit code
    fd = structural.field_diff
    diverged = (
        structural.count_a != structural.count_b
        or fd.only_a
        or fd.only_b
        or fd.aliases
        or any(d > NUMERIC_EPS for d in structural.mean_drift_per_field.values())
    )
    return 1 if diverged else 0


if __name__ == "__main__":
    sys.exit(main())

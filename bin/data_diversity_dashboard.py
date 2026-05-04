#!/usr/bin/env python3
"""
G160 · bin/data_diversity_dashboard.py

Cluster E: per-cohort diversity dashboard for buyer pre-purchase verification.
Generates route_type / biome / time-of-day / action-entropy histograms.
"""

import argparse, csv, datetime, json, math, os, sys, tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np; HAS_NUMPY = True
except ImportError:
    np = None; HAS_NUMPY = False
try:
    from PIL import Image, ImageDraw, ImageFont; HAS_PIL = True
except ImportError:
    Image = ImageDraw = ImageFont = None; HAS_PIL = False  # type: ignore[misc]
try:
    import yaml; HAS_YAML = True
except ImportError:
    yaml = None; HAS_YAML = False
try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment; HAS_OPENPYXL = True
except ImportError:
    openpyxl = Font = PatternFill = Alignment = None; HAS_OPENPYXL = False  # type: ignore[misc]

_DIMENSIONS = ("route_type", "biome", "time_of_day", "action_entropy")
_PALETTE = ["#4C72B0", "#55A868", "#C44E52", "#8172B2", "#CCB974", "#64B5CD"]


def load_data(path: Path) -> List[Dict[str, Any]]:
    """Load records from JSON, YAML, or CSV."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, list): return raw
        if isinstance(raw, dict):
            for k in ("records", "data", "rows", "items"):
                if k in raw and isinstance(raw[k], list): return raw[k]
            return [raw]
        raise ValueError(f"Unexpected JSON in {path}")
    if suffix in (".yaml", ".yml"):
        if not HAS_YAML: raise ImportError("PyYAML required for YAML")
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        if isinstance(raw, list): return raw
        if isinstance(raw, dict):
            for k in ("records", "data", "rows", "items"):
                if k in raw and isinstance(raw[k], list): return raw[k]
            return [raw]
        raise ValueError(f"Unexpected YAML in {path}")
    if suffix == ".csv":
        rows: List[Dict[str, str]] = []
        with open(path, "r", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh): rows.append(dict(row))
        return rows
    raise ValueError(f"Unsupported format: {suffix}")


def _extract_cohorts(records: List[Dict], field: str) -> Dict[str, List[Dict]]:
    groups: Dict[str, List[Dict]] = defaultdict(list)
    for r in records: groups[str(r.get(field, "unknown"))].append(r)
    return dict(groups)


def _histogram(values: List[Any], bins: Optional[List[str]] = None) -> Tuple[List[str], List[int]]:
    counts: Dict[str, int] = defaultdict(int)
    for v in values: counts[str(v)] += 1
    labels = bins if bins else sorted(counts.keys())
    return labels, [counts.get(l, 0) for l in labels]


def _tod_bucket(ts: Any) -> str:
    hour: Optional[int] = None
    if isinstance(ts, (int, float)): hour = int(ts) % 24
    elif isinstance(ts, str):
        for sep in ("T", " ", ":"):
            if sep in ts:
                for p in ts.split(sep):
                    if p.isdigit() and 0 <= int(p) <= 23: hour = int(p); break
                if hour is not None: break
        if hour is None and ts.isdigit(): hour = int(ts) % 24
    if hour is None: return "unknown"
    if 5 <= hour < 12: return "morning"
    if 12 <= hour < 17: return "afternoon"
    if 17 <= hour < 21: return "evening"
    return "night"


def _compute_hists(recs: List[Dict]) -> Dict[str, Tuple[List[str], List[int]]]:
    out: Dict[str, Tuple[List[str], List[int]]] = {}
    out["route_type"] = _histogram([r.get("route_type", "unknown") for r in recs])
    out["biome"] = _histogram([r.get("biome", "unknown") for r in recs])
    out["time_of_day"] = _histogram(
        [_tod_bucket(r.get("time_of_day", r.get("timestamp", ""))) for r in recs],
        bins=["morning", "afternoon", "evening", "night", "unknown"])
    ent: List[str] = []
    for r in recs:
        raw = r.get("action_entropy")
        if raw is None: ent.append("missing")
        else:
            try:
                v = float(raw)
                ent.append("low" if v < 0.5 else ("medium" if v < 1.5 else "high"))
            except (ValueError, TypeError): ent.append("invalid")
    out["action_entropy"] = _histogram(ent, bins=["low", "medium", "high", "missing", "invalid"])
    return out


def _render_text(name: str, hists: Dict, n: int) -> str:
    lines = [f"{'=' * 60}", f"Cohort: {name}  (n={n})", f"{'=' * 60}"]
    for dim in _DIMENSIONS:
        labels, counts = hists[dim]
        lines.append(f"\n  --- {dim} ---")
        mx = max(counts) if counts else 1
        for lab, cnt in zip(labels, counts):
            bar = "█" * int(round(cnt / mx * 40)) if mx else ""
            lines.append(f"    {lab:<15s} | {bar} {cnt}")
    lines.append("")
    return "\n".join(lines)


def generate_text_report(cohorts: Dict[str, List[Dict]]) -> str:
    return "\n".join(_render_text(n, _compute_hists(r), len(r)) for n, r in sorted(cohorts.items()))


def _draw_bar(labels: List[str], counts: List[int], title: str, w: int = 400, h: int = 260) -> "Image.Image":
    if not HAS_PIL or Image is None: raise RuntimeError("PIL required")
    img = Image.new("RGB", (w, h), "#FAFAFA"); draw = ImageDraw.Draw(img)
    ml, mr, mt, mb = 100, 20, 30, 10; cw, ch = w - ml - mr, h - mt - mb
    mx = max(counts) if counts else 1; n = len(labels)
    bh = max(4, ch // max(n, 1) - 4); draw.text((ml, 4), title, fill="#333")
    y = mt + 10
    for i, (lab, cnt) in enumerate(zip(labels, counts)):
        bl = int(cnt / mx * cw) if mx else 0
        draw.rectangle([ml, y, ml + bl, y + bh], fill=_PALETTE[i % len(_PALETTE)])
        draw.text((4, y), str(lab), fill="#222"); draw.text((ml + bl + 4, y), str(cnt), fill="#555")
        y += bh + 4
    return img


def generate_image_dashboard(cohorts: Dict[str, List[Dict]], out_dir: Path) -> List[Path]:
    if not HAS_PIL or Image is None: raise RuntimeError("PIL required")
    out_dir.mkdir(parents=True, exist_ok=True); paths: List[Path] = []
    for name in sorted(cohorts):
        recs = cohorts[name]; hists = _compute_hists(recs)
        cw, ch = 420, 280; comp = Image.new("RGB", (cw * 2, ch * 2 + 30), "#FFF")
        ImageDraw.Draw(comp).text((10, 5), f"Cohort: {name}  (n={len(recs)})", fill="#111")
        for idx, dim in enumerate(_DIMENSIONS):
            comp.paste(_draw_bar(*hists[dim], dim, cw, ch), (idx % 2 * cw, idx // 2 * ch + 30))
        p = out_dir / f"cohort_{name}.png"; comp.save(str(p), "PNG"); paths.append(p)
    return paths


def generate_excel_report(cohorts: Dict[str, List[Dict]], out_path: Path) -> Path:
    if not HAS_OPENPYXL or openpyxl is None: raise RuntimeError("openpyxl required")
    wb = openpyxl.Workbook()
    hf = Font(bold=True, color="FFFFFF"); hfill = PatternFill(start_color="4C72B0", end_color="4C72B0", fill_type="solid")
    tf = Font(bold=True, size=14)
    for name in sorted(cohorts):
        recs = cohorts[name]; hists = _compute_hists(recs)
        ws = wb.create_sheet(title=f"cohort_{name}")
        ws.merge_cells("A1:D1"); ws["A1"] = f"Cohort: {name}  (n={len(recs)})"; ws["A1"].font = tf
        row = 3
        for dim in _DIMENSIONS:
            labels, counts = hists[dim]
            ws.cell(row=row, column=1, value=dim).font = Font(bold=True, size=12); row += 1
            for ci, hdr in enumerate(("Category", "Count", "Share"), 1):
                c = ws.cell(row=row, column=ci, value=hdr); c.font = hf; c.fill = hfill; c.alignment = Alignment(horizontal="center")
            row += 1; total = sum(counts) or 1
            for lab, cnt in zip(labels, counts):
                ws.cell(row=row, column=1, value=lab)
                ws.cell(row=row, column=2, value=cnt).alignment = Alignment(horizontal="center")
                ws.cell(row=row, column=3, value=f"{round(cnt / total * 100, 1)}%").alignment = Alignment(horizontal="center")
                row += 1
            row += 1
    if "Sheet" in wb.sheetnames: del wb["Sheet"]
    wb.save(str(out_path)); return out_path


def generate_summary_json(cohorts: Dict[str, List[Dict]], out_path: Path) -> Path:
    summary: Dict[str, Any] = {"generated_at": datetime.datetime.utcnow().isoformat() + "Z",
                               "num_cohorts": len(cohorts), "cohorts": {}}
    for name in sorted(cohorts):
        recs = cohorts[name]; hists = _compute_hists(recs)
        cd: Dict[str, Any] = {"n": len(recs), "dimensions": {}}
        for dim in _DIMENSIONS:
            labels, counts = hists[dim]
            cd["dimensions"][dim] = {"labels": labels, "counts": counts, "unique": len([c for c in counts if c > 0])}
        summary["cohorts"][name] = cd
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh: json.dump(summary, fh, indent=2, ensure_ascii=False)
    return out_path


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for the data diversity dashboard."""
    parser = argparse.ArgumentParser(description="Cluster E: per-cohort diversity dashboard.")
    parser.add_argument("--input", "-i", required=True, help="Input data file (JSON/YAML/CSV).")
    parser.add_argument("--output", "-o", required=True, help="Output directory or file path.")
    parser.add_argument("--format", "-f", choices=("text", "png", "xlsx", "json", "all"), default="text")
    parser.add_argument("--cohort-field", default="cohort", help="Cohort grouping field name.")
    args = parser.parse_args(argv)
    inp = Path(args.input)
    if not inp.exists():
        print(f"Error: input not found: {inp}", file=sys.stderr); return 1
    try:
        records = load_data(inp)
    except Exception as exc:
        print(f"Error loading data: {exc}", file=sys.stderr); return 2
    if not records:
        print("Warning: no records found.", file=sys.stderr); return 0
    cohorts = _extract_cohorts(records, args.cohort_field)
    print(f"Loaded {len(records)} records across {len(cohorts)} cohort(s).")
    out = Path(args.output); fmt = args.format
    if fmt in ("text", "all"):
        report = generate_text_report(cohorts)
        of = out / "dashboard.txt" if (out.suffix == "" or out.is_dir()) else out
        of.parent.mkdir(parents=True, exist_ok=True); of.write_text(report, encoding="utf-8")
        print(f"Text report → {of}")
        if fmt == "text":
            pass  # file-only mode
        else:
            print(report)
    if fmt in ("png", "all"):
        if not HAS_PIL: print("Warning: PIL unavailable, skipping PNG.", file=sys.stderr)
        else:
            d = out if out.is_dir() else out.parent; d.mkdir(parents=True, exist_ok=True)
            ps = generate_image_dashboard(cohorts, d); print(f"PNG → {len(ps)} image(s) in {d}")
    if fmt in ("xlsx", "all"):
        if not HAS_OPENPYXL: print("Warning: openpyxl unavailable, skipping Excel.", file=sys.stderr)
        else:
            xp = out if out.suffix.lower() == ".xlsx" else out / "dashboard.xlsx"
            xp.parent.mkdir(parents=True, exist_ok=True); generate_excel_report(cohorts, xp); print(f"Excel → {xp}")
    if fmt in ("json", "all"):
        jp = out if out.suffix.lower() == ".json" else out / "summary.json"
        jp.parent.mkdir(parents=True, exist_ok=True); generate_summary_json(cohorts, jp); print(f"JSON → {jp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

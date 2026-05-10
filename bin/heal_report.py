"""bin/heal_report.py — Generate heal_report.html from heal_events.jsonl.

Howard 2026-05-09 (Heal Framework v1, Layer 3 viewer):
Tester opens this HTML in browser to see what's healthy + what's broken
without parsing JSON by hand. Single self-contained HTML, no JS frameworks.

Usage (standalone):
    python3 bin/heal_report.py
        → generates ~/Documents/OysterRecorder/runtime/heal_report.html
        → opens in default browser

Called from recorder: clicking "📊 健康报告" in helpbar (rc15+).
"""
from __future__ import annotations

import html
import json
import sys
import webbrowser
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from heal_registry import (  # noqa: E402
    VALID_FEATURES,
    _heal_log_path,
    aggregate_by_feature,
    read_recent_events,
)


SEV_COLORS = {
    "info": "#0277bd",
    "warn": "#f57f17",
    "error": "#c62828",
    "fatal": "#6a1b9a",
}


def _row_for_feature(feature_id: str, counts: dict[str, int]) -> str:
    sev_cells = "".join(
        f'<td style="color:{SEV_COLORS.get(sev, "#666")};text-align:right;">{counts.get(sev, 0)}</td>'
        for sev in ("info", "warn", "error", "fatal")
    )
    total = counts.get("total", 0)
    health_class = "ok"
    if counts.get("fatal", 0) > 0:
        health_class = "fatal"
    elif counts.get("error", 0) > 0:
        health_class = "error"
    elif counts.get("warn", 0) > 0:
        health_class = "warn"
    return (
        f'<tr class="row-{health_class}">'
        f'<td>{html.escape(feature_id)}</td>'
        f'{sev_cells}'
        f'<td style="text-align:right;font-weight:600;">{total}</td>'
        f'</tr>'
    )


def _row_for_event(ev: dict) -> str:
    # rc15.24 C1 (round 16): escape ts_iso. Every other field already
    # ran through html.escape(); ts_iso was the lone gap. Threat is
    # low (file is local + trusted), but a tampered/corrupt JSONL line
    # with `<` in ts_iso would inject. 1-line hardening, can't regress.
    ts = html.escape(ev.get("ts_iso", "?"))
    fid = html.escape(ev.get("feature_id", "?"))
    et = html.escape(ev.get("event_type", "?"))
    sev = ev.get("severity", "info")
    sev_color = SEV_COLORS.get(sev, "#666")
    summary = html.escape(ev.get("summary", ""))
    # rc15-fix BUG#5: default=str so non-JSON-native values (Path,
    # datetime) in details don't crash the dashboard generator. Was
    # safe when details came from JSONL re-read (already strings) but
    # crashed when called with in-memory dict containing Path objects.
    details = html.escape(json.dumps(ev.get("details", {}), ensure_ascii=False, default=str)[:200])
    rem = ev.get("remediation") or {}
    next_step = html.escape(rem.get("next_step", ""))
    return (
        f'<tr>'
        f'<td style="font-family:monospace;font-size:11px;white-space:nowrap;">{ts}</td>'
        f'<td>{fid}</td>'
        f'<td>{et}</td>'
        f'<td style="color:{sev_color};font-weight:600;">{sev}</td>'
        f'<td>{summary}</td>'
        f'<td style="font-family:monospace;font-size:11px;color:#666;">{details}</td>'
        f'<td style="font-style:italic;color:#444;">{next_step}</td>'
        f'</tr>'
    )


def generate_html(limit: int = 200) -> Path:
    events = read_recent_events(limit=limit)
    by_feat = aggregate_by_feature(events)
    log_path = _heal_log_path()
    total_events = len(events)
    feat_count = len(by_feat)
    sev_total = Counter(ev.get("severity", "info") for ev in events)

    # Detect contract violations: any feature_id starting with "unregistered:"
    unregistered = [f for f in by_feat if f.startswith("unregistered:")]
    health_banner = (
        '<div style="background:#fef3c7;color:#92400e;padding:12px;border-radius:6px;margin:12px 0;">'
        f'⚠️ {len(unregistered)} unregistered feature(s) emitting events: '
        + ", ".join(html.escape(f) for f in unregistered)
        + '. Add to heal_registry.VALID_FEATURES.</div>'
    ) if unregistered else (
        '<div style="background:#d1fae5;color:#065f46;padding:12px;border-radius:6px;margin:12px 0;">'
        f'✅ All {feat_count} active features are registered.</div>'
    )

    rows_features = "\n".join(
        _row_for_feature(fid, by_feat[fid])
        for fid in sorted(by_feat.keys())
    )
    rows_events = "\n".join(
        _row_for_event(ev)
        for ev in reversed(events[-100:])  # most recent first
    )

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Oyster Recorder — Health Report</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif; max-width: 1280px; margin: 24px auto; padding: 0 20px; color: #222; }}
h1 {{ font-size: 22px; margin: 8px 0; }}
h2 {{ font-size: 16px; margin: 24px 0 8px; color: #444; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
.meta {{ color: #666; font-size: 13px; }}
table {{ border-collapse: collapse; width: 100%; margin: 8px 0 16px; font-size: 13px; }}
th {{ background: #f5f5f5; text-align: left; padding: 8px; border-bottom: 2px solid #ddd; }}
td {{ padding: 6px 8px; border-bottom: 1px solid #eee; vertical-align: top; }}
tr.row-ok td {{ background: rgba(16,185,129,0.04); }}
tr.row-warn td {{ background: rgba(245,158,11,0.06); }}
tr.row-error td {{ background: rgba(220,38,38,0.06); }}
tr.row-fatal td {{ background: rgba(106,27,154,0.08); font-weight: 600; }}
.summary-card {{ display: inline-block; padding: 12px 18px; margin: 4px 8px 4px 0; border-radius: 8px; background: #fafafa; border: 1px solid #e0e0e0; }}
.summary-card .num {{ font-size: 28px; font-weight: 700; color: #222; }}
.summary-card .label {{ font-size: 11px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }}
</style>
</head>
<body>
<h1>🐚 Oyster Recorder — Health Report</h1>
<div class="meta">
  Generated: {datetime.now(timezone.utc).isoformat()} (UTC)<br>
  Source: <code>{html.escape(str(log_path))}</code><br>
  Total events: {total_events} (showing last {min(limit, total_events)})
</div>

{health_banner}

<h2>Severity totals</h2>
<div>
  <span class="summary-card"><div class="num" style="color:{SEV_COLORS['info']}">{sev_total.get('info', 0)}</div><div class="label">info</div></span>
  <span class="summary-card"><div class="num" style="color:{SEV_COLORS['warn']}">{sev_total.get('warn', 0)}</div><div class="label">warn</div></span>
  <span class="summary-card"><div class="num" style="color:{SEV_COLORS['error']}">{sev_total.get('error', 0)}</div><div class="label">error</div></span>
  <span class="summary-card"><div class="num" style="color:{SEV_COLORS['fatal']}">{sev_total.get('fatal', 0)}</div><div class="label">fatal</div></span>
</div>

<h2>By feature</h2>
<table>
<thead><tr><th>feature_id</th><th>info</th><th>warn</th><th>error</th><th>fatal</th><th>total</th></tr></thead>
<tbody>
{rows_features or '<tr><td colspan="6" style="text-align:center;color:#999;padding:24px;">No events yet</td></tr>'}
</tbody>
</table>

<h2>Recent events (latest 100)</h2>
<table>
<thead><tr><th>timestamp</th><th>feature</th><th>type</th><th>sev</th><th>summary</th><th>details</th><th>next step</th></tr></thead>
<tbody>
{rows_events or '<tr><td colspan="7" style="text-align:center;color:#999;padding:24px;">No events yet</td></tr>'}
</tbody>
</table>

<h2>Registered features ({len(VALID_FEATURES)})</h2>
<div style="font-family:monospace;font-size:12px;color:#555;">
{', '.join(sorted(VALID_FEATURES))}
</div>

<div class="meta" style="margin-top:24px;border-top:1px solid #ddd;padding-top:8px;">
  Heal Framework v1 • see <code>HEAL_CONTRACT.md</code> for contract details.
</div>
</body>
</html>
"""

    out_path = log_path.parent / "heal_report.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path


def main() -> int:
    # rc15.4-fix BUG R6 H4: wrap entire body in try/except so generate_html
    # failure doesn't cause stale HTML to open or NameError to propagate.
    try:
        out = generate_html()
        print(f"heal_report.html written: {out}")
        if not (out and out.exists()):
            print("ERROR: heal_report.html not produced")
            return 2
    except Exception as e:
        print(f"ERROR: generate_html failed: {e}")
        return 3
    try:
        # rc15.2-fix BUG#8: use Path.as_uri() instead of f"file://{out}".
        # On Windows, str(Path(...)) returns backslashes which most browsers
        # reject. as_uri() produces correct file:///C:/Users/... form.
        webbrowser.open(out.as_uri())
    except Exception as e:
        print(f"WARNING: browser open failed: {e}; manual open: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

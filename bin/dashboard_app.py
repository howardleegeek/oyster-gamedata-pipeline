#!/usr/bin/env python3
"""
G012 · bin/dashboard_app.py — Flask + htmx vendor submission dashboard (read-only).

Provides a lightweight web UI for browsing vendor submission data stored as
CSV / Excel files in a configurable data directory.  All endpoints are
read-only; no mutations are exposed.

Usage:
    python bin/dashboard_app.py [--host HOST] [--port PORT] [--data-dir DIR]

Dependencies: Flask (required), openpyxl (optional, for .xlsx support).
htmx is loaded from CDN; no local JS build step required.
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_flask: Any = None
_openpyxl: Any = None
logger = logging.getLogger("dashboard_app")


def _import_flask() -> Any:
    """Lazy-import Flask; raise a friendly error if unavailable."""
    global _flask
    if _flask is None:
        try:
            import flask as _flask  # noqa: F811
        except ImportError:
            print("ERROR: Flask is required. Install with: pip install flask", file=sys.stderr)
            sys.exit(1)
    return _flask


def _import_openpyxl() -> Any:
    """Lazy-import openpyxl for Excel support."""
    global _openpyxl
    if _openpyxl is None:
        try:
            import openpyxl as _openpyxl  # noqa: F811
        except ImportError:
            pass
    return _openpyxl


def _read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """Read a CSV/TSV file and return (headers, rows-as-dicts)."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        headers: List[str] = list(reader.fieldnames or [])
        rows: List[Dict[str, str]] = [dict(r) for r in reader]
    return headers, rows


def _read_excel(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """Read the first sheet of an Excel workbook."""
    oxl = _import_openpyxl()
    if oxl is None:
        raise RuntimeError("openpyxl is required to read .xlsx files")
    wb = oxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        return [], []
    rows_iter = ws.iter_rows(values_only=True)
    try:
        headers = [str(c) for c in next(rows_iter)]
    except StopIteration:
        return [], []
    rows: List[Dict[str, str]] = []
    for row in rows_iter:
        rows.append({h: str(v) if v is not None else "" for h, v in zip(headers, row)})
    wb.close()
    return headers, rows


def load_data(data_dir: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """Scan *data_dir* for the first supported data file and return its contents."""
    if not data_dir.is_dir():
        logger.warning("Data directory does not exist: %s", data_dir)
        return [], []
    for ext, reader_fn in ((".csv", _read_csv), (".tsv", _read_csv),
                           (".xlsx", _read_excel), (".xls", _read_excel)):
        candidates = sorted(data_dir.glob(f"*{ext}"))
        if candidates:
            logger.info("Loading data from %s", candidates[0])
            return reader_fn(candidates[0])
    logger.warning("No supported data files found in %s", data_dir)
    return [], []


class _DataCache:
    """Simple cache for dashboard data — sufficient for read-only use."""

    def __init__(self) -> None:
        self._headers: List[str] = []
        self._rows: List[Dict[str, str]] = []
        self._loaded: bool = False

    def ensure_loaded(self, data_dir: Path) -> None:
        if not self._loaded:
            self._headers, self._rows = load_data(data_dir)
            self._loaded = True

    @property
    def headers(self) -> List[str]:
        return self._headers

    @property
    def rows(self) -> List[Dict[str, str]]:
        return self._rows

    @property
    def total(self) -> int:
        return len(self._rows)


cache = _DataCache()


def _clamp(value: int, lo: int, hi: int) -> int:
    """Clamp *value* between *lo* and *hi* inclusive."""
    return max(lo, min(hi, value))


def _esc(s: str) -> str:
    """Minimal HTML escaping for table cells."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


_HTMX_CDN = "https://unpkg.com/htmx.org@1.9.12"
_STYLE = """
<style>
  :root{--bg:#f8f9fa;--fg:#212529;--accent:#0d6efd;--border:#dee2e6}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--fg);padding:1.5rem}
  h1{margin-bottom:.5rem}
  .toolbar{display:flex;gap:.75rem;align-items:center;margin-bottom:1rem;flex-wrap:wrap}
  .toolbar input{padding:.4rem .6rem;border:1px solid var(--border);border-radius:4px;min-width:220px}
  .toolbar button{padding:.4rem .8rem;background:var(--accent);color:#fff;border:none;border-radius:4px;cursor:pointer}
  table{width:100%;border-collapse:collapse;background:#fff;border-radius:6px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}
  th,td{padding:.5rem .75rem;text-align:left;border-bottom:1px solid var(--border);font-size:.875rem}
  th{background:#e9ecef;position:sticky;top:0}
  th a{color:inherit;text-decoration:none}
  th a:hover{color:var(--accent)}
  .pagination{display:flex;gap:.5rem;align-items:center;margin-top:1rem;font-size:.875rem}
  .pagination button{padding:.3rem .6rem;border:1px solid var(--border);background:#fff;border-radius:4px;cursor:pointer}
  .pagination button:disabled{opacity:.4;cursor:default}
  .badge{display:inline-block;background:var(--accent);color:#fff;padding:.15rem .5rem;border-radius:999px;font-size:.75rem}
  .meta{color:#6c757d;font-size:.8rem;margin-bottom:.5rem}
</style>"""


def _render_index(headers: List[str], rows: List[Dict[str, str]], total: int) -> str:
    """Render the full dashboard page."""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vendor Submission Dashboard</title><script src="{_HTMX_CDN}"></script>{_STYLE}</head>
<body><h1>📦 Vendor Submission Dashboard</h1>
<p class="meta">Read-only · <span class="badge">{total} rows</span> · {len(headers)} columns</p>
<div class="toolbar">
  <input type="search" id="search-box" name="q" placeholder="Search all columns…"
         hx-get="/htx/table" hx-target="#table-body" hx-trigger="keyup changed delay:300ms"
         hx-indicator="#spinner">
  <span id="spinner" class="htmx-indicator">⏳</span>
  <span id="result-count">{total} result(s)</span>
  <a href="/api/export" style="margin-left:auto"><button type="button">⬇ Export CSV</button></a>
</div>
<div id="table-container" hx-get="/htx/table" hx-trigger="load"><p>Loading…</p></div>
</body></html>"""


def _render_table_fragment(headers, rows, page, pages, total, sort_col, sort_dir, query):
    """Render the <table> + pagination fragment for htmx swap."""
    q_param = f"&q={query}" if query else ""

    def _sort_link(col):
        d = "desc" if (col == sort_col and sort_dir == "asc") else "asc"
        arrow = " ▲" if (col == sort_col and sort_dir == "asc") else (" ▼" if (col == sort_col and sort_dir == "desc") else "")
        return f'<a hx-get="/htx/table?sort={col}&dir={d}{q_param}&page={page}" hx-target="#table-container">{col}{arrow}</a>'

    ths = "".join(f"<th>{_sort_link(h)}</th>" for h in headers)
    tds_rows = ""
    for r in rows:
        tds_rows += "<tr>" + "".join(f"<td>{_esc(r.get(h, ''))}</td>" for h in headers) + "</tr>"
    if not rows:
        tds_rows = f'<tr><td colspan="{len(headers)}" style="text-align:center;color:#999">No results</td></tr>'

    prev = f'<button hx-get="/htx/table?page={page-1}{q_param}" hx-target="#table-container" {"disabled" if page<=1 else ""}>◀ Prev</button>'
    nxt = f'<button hx-get="/htx/table?page={page+1}{q_param}" hx-target="#table-container" {"disabled" if page>=pages else ""}>Next ▶</button>'
    return f"""<div id="table-container"><table><thead><tr>{ths}</tr></thead>
<tbody id="table-body">{tds_rows}</tbody></table>
<div class="pagination">{prev}<span>Page {page} of {pages} ({total} rows)</span>{nxt}</div></div>"""


def create_app(data_dir: Path) -> Any:
    """Create and configure the Flask application."""
    flask = _import_flask()
    app = flask.Flask(__name__)

    @app.route("/")
    def index():
        cache.ensure_loaded(data_dir)
        return _render_index(cache.headers, cache.rows, cache.total)

    @app.route("/api/summary")
    def api_summary():
        cache.ensure_loaded(data_dir)
        return flask.jsonify({"total_rows": cache.total, "columns": cache.headers, "column_count": len(cache.headers)})

    @app.route("/htx/table")
    def htx_table():
        """htmx endpoint: filtered/sorted/paginated table fragment."""
        cache.ensure_loaded(data_dir)
        page = _clamp(int(flask.request.args.get("page", "1")), 1, 10000)
        size = _clamp(int(flask.request.args.get("size", "25")), 1, 200)
        sort_col = flask.request.args.get("sort", "")
        sort_dir = flask.request.args.get("dir", "asc")
        query = flask.request.args.get("q", "").strip().lower()
        rows = list(cache.rows)
        if query:
            rows = [r for r in rows if any(query in str(v).lower() for v in r.values())]
        if sort_col and sort_col in cache.headers:
            rows.sort(key=lambda r: r.get(sort_col, ""), reverse=(sort_dir.lower() == "desc"))
        total = len(rows)
        pages = max(1, (total + size - 1) // size)
        page = _clamp(page, 1, pages)
        return _render_table_fragment(cache.headers, rows[(page-1)*size:page*size], page, pages, total, sort_col, sort_dir, query)

    @app.route("/htx/search")
    def htx_search():
        """htmx endpoint: return row count for a search query."""
        cache.ensure_loaded(data_dir)
        query = flask.request.args.get("q", "").strip().lower()
        count = cache.total if not query else sum(1 for r in cache.rows if any(query in str(v).lower() for v in r.values()))
        return f'<span id="result-count">{count} result(s)</span>'

    @app.route("/api/export")
    def api_export():
        """Export current (filtered) data as CSV download."""
        cache.ensure_loaded(data_dir)
        query = flask.request.args.get("q", "").strip().lower()
        rows = cache.rows if not query else [r for r in cache.rows if any(query in str(v).lower() for v in r.values())]
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=cache.headers)
        writer.writeheader()
        writer.writerows(rows)
        return flask.Response(output.getvalue(), mimetype="text/csv",
                              headers={"Content-Disposition": "attachment; filename=export.csv"})

    @app.errorhandler(404)
    def not_found(e):
        return "<h1>404 — Not Found</h1>", 404

    @app.errorhandler(500)
    def server_error(e):
        return "<h1>500 — Internal Server Error</h1>", 500

    return app


def main(argv: Optional[List[str]] = None) -> int:
    """Parse CLI arguments and launch the Flask development server.

    Parameters
    ----------
    argv : list[str] | None
        Command-line arguments (defaults to sys.argv[1:]).

    Returns
    -------
    int : Exit code (0 on success, 1 on error).
    """
    parser = argparse.ArgumentParser(description="Vendor Submission Dashboard — read-only Flask + htmx UI")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="Port to listen on (default: 5000)")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DASHBOARD_DATA_DIR", "data")),
                        help="Directory containing vendor submission CSV/XLSX files")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level),
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if not args.data_dir.is_dir():
        logger.warning("Data directory '%s' not found; using temporary sample data.", args.data_dir)
        tmp_dir = Path(tempfile.mkdtemp(prefix="dashboard_data_"))
        (tmp_dir / "sample_submissions.csv").write_text(
            "vendor_id,vendor_name,status,submission_date,amount\n"
            "V001,Acme Corp,approved,2024-01-15,12500.00\n"
            "V002,Globex Inc,pending,2024-02-20,8750.50\n"
            "V003,Initech,approved,2024-03-01,3200.00\n"
            "V004,Umbrella Co,rejected,2024-03-10,15000.00\n"
            "V005,Stark Industries,approved,2024-04-05,42000.00\n")
        args.data_dir = tmp_dir
        logger.info("Using temporary sample data at %s", tmp_dir)

    app = create_app(args.data_dir)
    print(f"🚀 Dashboard starting → http://{args.host}:{args.port}")
    print(f"   Data directory: {args.data_dir}")

    try:
        app.run(host=args.host, port=args.port, debug=args.debug)
    except KeyboardInterrupt:
        print("\nShutting down.")
        return 0
    except OSError as exc:
        logger.error("Failed to start server: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

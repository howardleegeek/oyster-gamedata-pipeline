#!/usr/bin/env python3
"""
G012 · bin/dashboard_app.py — Flask + htmx vendor submission dashboard (read-only).

Lightweight web UI for browsing vendor submission data from CSV/Excel files.
Read-only interface with HTMX for dynamic updates.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_flask: Any = None
_openpyxl: Any = None
logger = logging.getLogger(__name__)


def _import_flask() -> Any:
    """Lazy-import Flask."""
    global _flask
    if _flask is None:
        try:
            import flask as _flask
        except ImportError:
            print("ERROR: Install Flask: pip install flask", file=sys.stderr)
            sys.exit(1)
    return _flask


def _import_openpyxl() -> Any:
    """Lazy-import openpyxl."""
    global _openpyxl
    if _openpyxl is None:
        try:
            import openpyxl as _openpyxl
        except ImportError as e:
            logger.debug("openpyxl unavailable; Excel support disabled: %s", e)
    return _openpyxl


def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """Read CSV/TSV file."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            sample = f.read(4096)
            f.seek(0)
            delimiter = "\t" if "\t" in sample and sample.count("\t") > sample.count(",") else ","
            reader = csv.DictReader(f, delimiter=delimiter)
            headers = list(reader.fieldnames or [])
            rows = [{k: str(v) if v is not None else "" for k, v in r.items()} for r in reader]
            return headers, rows
    except Exception as e:
        logger.error(f"CSV error {path}: {e}")
        return [], []


def read_excel(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """Read Excel file."""
    oxl = _import_openpyxl()
    if not oxl:
        logger.error("Install openpyxl for Excel support")
        return [], []
    try:
        wb = oxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        if not ws:
            return [], []
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return [], []
        headers = [str(c) if c is not None else "" for c in rows[0]]
        data = []
        for row in rows[1:]:
            data.append({h: str(v) if v is not None else "" for h, v in zip(headers, row, strict=True)})
        wb.close()
        return headers, data
    except Exception as e:
        logger.error(f"Excel error {path}: {e}")
        return [], []


def load_data(data_dir: Path) -> Tuple[List[str], List[Dict[str, str]], str]:
    """Load first supported file in data_dir."""
    if not data_dir.is_dir():
        return [], [], ""

    for pattern, reader in [("*.csv", read_csv), ("*.tsv", read_csv),
                           ("*.xlsx", read_excel), ("*.xls", read_excel)]:
        for f in sorted(data_dir.glob(pattern)):
            headers, rows = reader(f)
            if headers:
                return headers, rows, f.name
    return [], [], ""


def create_app(data_dir: Path) -> Any:
    """Create Flask app."""
    flask = _import_flask()
    app = flask.Flask(__name__)
    headers, rows, filename = load_data(data_dir)

    def render_rows(rows_slice: List[Dict[str, str]], cols: int = 10) -> str:
        """Render table rows HTML."""
        if not rows_slice:
            return '<tr><td colspan="10" style="padding:2rem;text-align:center">No data</td></tr>'

        html = []
        for r in rows_slice:
            cells = []
            for h in headers[:cols]:
                val = r.get(h, "")
                if len(val) > 100:
                    val = val[:97] + "..."
                cells.append(f"<td>{val}</td>")
            html.append(f"<tr>{''.join(cells)}</tr>")
        return "\n".join(html)

    @app.route("/")
    def index() -> str:
        """Main dashboard."""
        cols = min(10, len(headers))
        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Vendor Dashboard</title>
    <script src="https://unpkg.com/htmx.org@1.9.12"></script>
    <style>
        body {{ font-family: sans-serif; margin: 0; background: #f5f7fa; }}
        .container {{ max-width: 1200px; margin: auto; padding: 1rem; }}
        header {{ background: #2563eb; color: white; padding: 1rem; border-radius: 0.5rem; margin-bottom: 1rem; }}
        .stats {{ display: flex; gap: 1rem; margin-bottom: 1rem; flex-wrap: wrap; }}
        .stat {{ background: white; padding: 1rem; border-radius: 0.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1); flex: 1; min-width: 150px; }}
        .stat-value {{ font-size: 2rem; font-weight: bold; color: #2563eb; }}
        .stat-label {{ color: #64748b; font-size: 0.875rem; }}
        .controls {{ display: flex; gap: 1rem; margin-bottom: 1rem; }}
        input[type="search"] {{ flex: 1; padding: 0.5rem; border: 1px solid #cbd5e1; border-radius: 0.375rem; }}
        table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 0.5rem; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        th {{ padding: 0.75rem; text-align: left; background: #f1f5f9; border-bottom: 2px solid #cbd5e1; cursor: pointer; }}
        td {{ padding: 0.5rem 0.75rem; border-bottom: 1px solid #e2e8f0; }}
        tr:hover {{ background: #f8fafc; }}
        footer {{ text-align: center; margin-top: 2rem; color: #64748b; font-size: 0.875rem; }}
        #loading {{ display: none; text-align: center; padding: 1rem; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Vendor Submission Dashboard</h1>
            <div>{filename or 'No data file'}</div>
        </header>

        <div class="stats" id="stats">
            <div class="stat">
                <div class="stat-value">{len(rows)}</div>
                <div class="stat-label">Submissions</div>
            </div>
            <div class="stat">
                <div class="stat-value">{len(headers)}</div>
                <div class="stat-label">Columns</div>
            </div>
            <div class="stat">
                <div class="stat-value">{cols}</div>
                <div class="stat-label">Displayed</div>
            </div>
        </div>

        <div class="controls">
            <input type="search" placeholder="Search..." hx-get="/search" hx-trigger="keyup changed delay:300ms" hx-target="#tbody" name="q">
            <select hx-get="/sort" hx-trigger="change" hx-target="#tbody" name="col">
                <option value="">Sort by</option>
                {"".join(f'<option value="{h}">{h}</option>' for h in headers[:cols])}
            </select>
        </div>

        <div id="loading">Loading...</div>

        <table>
            <thead>
                <tr>{"".join(f'<th hx-get="/sort?col={h}" hx-trigger="click" hx-target="#tbody">{h}</th>' for h in headers[:cols])}</tr>
            </thead>
            <tbody id="tbody">
                {render_rows(rows[:50], cols)}
            </tbody>
        </table>

        <div style="display: flex; justify-content: center; gap: 0.5rem; margin-top: 1rem;">
            <button hx-get="/page?p=0" hx-target="#tbody" disabled>First</button>
            <button hx-get="/page?p=0" hx-target="#tbody">Prev</button>
            <span>Page 1</span>
            <button hx-get="/page?p=1" hx-target="#tbody">Next</button>
            <button hx-get="/page?p={(len(rows)-1)//50}" hx-target="#tbody">Last</button>
        </div>

        <footer>
            <p>Vendor Dashboard • Read-only • Data from {data_dir.absolute()}</p>
        </footer>
    </div>

    <script>
        document.addEventListener('htmx:beforeRequest', () => {{
            document.getElementById('loading').style.display = 'block';
        }});
        document.addEventListener('htmx:afterRequest', () => {{
            document.getElementById('loading').style.display = 'none';
        }});
    </script>
</body>
</html>"""

    @app.route("/search")
    def search() -> str:
        """Search endpoint."""
        q = flask.request.args.get("q", "").lower()
        if not q:
            return render_rows(rows[:50])

        filtered = [r for r in rows if any(q in str(v).lower() for v in r.values())]
        return render_rows(filtered[:50])

    @app.route("/sort")
    def sort() -> str:
        """Sort endpoint."""
        col = flask.request.args.get("col", "")
        if not col or col not in headers:
            return render_rows(rows[:50])

        dir_param = flask.request.args.get("dir", "asc")
        reverse = dir_param == "desc"
        sorted_rows = sorted(rows, key=lambda x: x.get(col, "").lower(), reverse=reverse)
        return render_rows(sorted_rows[:50])

    @app.route("/page")
    def page() -> str:
        """Pagination endpoint."""
        try:
            p = int(flask.request.args.get("p", "0"))
        except ValueError:
            p = 0
        page_size = 50
        start = p * page_size
        return render_rows(rows[start:start + page_size])

    @app.route("/stats")
    def stats() -> str:
        """Stats update endpoint."""
        return f"""
        <div class="stat">
            <div class="stat-value">{len(rows)}</div>
            <div class="stat-label">Submissions</div>
        </div>
        <div class="stat">
            <div class="stat-value">{len(headers)}</div>
            <div class="stat-label">Columns</div>
        </div>
        <div class="stat">
            <div class="stat-value">{min(10, len(headers))}</div>
            <div class="stat-label">Displayed</div>
        </div>
        """

    return app


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(description="Vendor submission dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=5000, help="Port to bind")
    parser.add_argument("--data-dir", type=Path, default=Path("."), help="Data directory")
    parser.add_argument("--debug", action="store_true", help="Debug mode")

    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                       format="%(levelname)s: %(message)s")

    if not args.data_dir.exists():
        logger.error(f"Data dir not found: {args.data_dir}")
        return 1

    app = create_app(args.data_dir)

    logger.info(f"Starting at http://{args.host}:{args.port}")
    logger.info(f"Data from {args.data_dir.absolute()}")

    try:
        app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)
    except KeyboardInterrupt:
        logger.info("Stopped")
    except Exception as e:
        logger.error(f"Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
error_dashboard_web.py — Single-page admin dashboard for error monitoring.

Serves a web dashboard at /admin/errors that allows filtering errors by
severity, source, time range, and user. Groups errors by sha256_dedup_key
and displays the top-10 most-frequent recent errors with sample tracebacks.

Usage:
    python3 bin/error_dashboard_web.py [--host HOST] [--port PORT]
                                       [--data-file PATH] [--sample]

Dependencies: Python stdlib only (http.server, json, argparse, etc.).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

SEVERITY_LEVELS: Tuple[str, ...] = (
    "debug", "info", "warning", "error", "critical",
)


def _now_utc() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """Format datetime as ISO-8601 string."""
    return dt.isoformat()


def _parse_iso(s: str) -> Optional[datetime]:
    """Parse an ISO-8601 string, returning None on failure."""
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def generate_sample_errors(count: int = 200) -> List[Dict[str, Any]]:
    """Generate synthetic error records for demo / testing purposes.

    Args:
        count: Number of error records to generate.

    Returns:
        List of error dicts with keys: timestamp, severity, source,
        user, message, traceback, sha256_dedup_key.
    """
    import random
    sources = ["api_gateway", "auth_service", "payment_processor",
               "user_service", "notification_engine", "data_pipeline"]
    severities = ["debug", "info", "warning", "error", "critical"]
    users = [f"user_{i}" for i in range(1, 21)]
    messages = [
        "Connection timeout to upstream service",
        "Invalid authentication token",
        "Rate limit exceeded for client",
        "Database query failed: deadlock detected",
        "Null pointer in request handler",
        "Memory usage exceeded threshold",
        "SSL certificate verification failed",
        "Payload size exceeds maximum allowed",
        "Retry exhausted for transient failure",
        "Configuration key missing in environment",
    ]
    tracebacks = [
        "Traceback (most recent call last):\n  File \"app.py\", line 42, in handle_request\n    result = await service.call()\n  File \"service.py\", line 89, in call\n    raise ConnectionError(\"Timeout after 30s\")\nConnectionError: Timeout after 30s",
        "Traceback (most recent call last):\n  File \"auth.py\", line 123, in validate_token\n    payload = jwt.decode(token, key)\n  File \"jwt/__init__.py\", line 45, in decode\n    raise InvalidTokenError(\"Signature verification failed\")\nInvalidTokenError: Signature verification failed",
        "Traceback (most recent call last):\n  File \"rate_limit.py\", line 67, in check_limit\n    if count > limit:\n  File \"redis_client.py\", line 34, in __gt__\n    raise RateLimitExceeded(f\"Limit {limit} exceeded\")\nRateLimitExceeded: Limit 1000 exceeded",
        "Traceback (most recent call last):\n  File \"db.py\", line 156, in execute_query\n    cursor.execute(sql)\n  File \"psycopg2/extensions.py\", line 23, in execute\n    raise DeadlockDetected(\"Deadlock detected\")\nDeadlockDetected: Deadlock detected",
    ]
    errors: List[Dict[str, Any]] = []
    now = _now_utc()
    for _i in range(count):
        msg = random.choice(messages)
        sev = random.choices(severities, weights=[5, 15, 30, 40, 10])[0]
        src = random.choice(sources)
        user = random.choice(users)
        ts = now - timedelta(minutes=random.randint(0, 1440))
        dedup_input = f"{msg}|{src}|{sev}"
        sha256_key = hashlib.sha256(dedup_input.encode()).hexdigest()
        errors.append({
            "timestamp": _iso(ts),
            "severity": sev,
            "source": src,
            "user": user,
            "message": msg,
            "traceback": random.choice(tracebacks),
            "sha256_dedup_key": sha256_key,
        })
    return errors


class ErrorStore:
    """In-memory store for error records with filtering and grouping."""

    def __init__(self, data_file: Optional[str] = None) -> None:
        """Initialize store, optionally loading from JSON file."""
        self.errors: List[Dict[str, Any]] = []
        if data_file and os.path.exists(data_file):
            with open(data_file, "r", encoding="utf-8") as fh:
                self.errors = json.load(fh)
        elif data_file:
            sys.stderr.write(f"Warning: data file {data_file} not found\n")

    def add_errors(self, errors: List[Dict[str, Any]]) -> None:
        """Add error records to the store."""
        self.errors.extend(errors)

    def get_all(self) -> List[Dict[str, Any]]:
        """Return all error records."""
        return self.errors

    def distinct_values(self, field: str) -> List[str]:
        """Return distinct values for a given field."""
        values = set()
        for err in self.errors:
            if field in err:
                values.add(err[field])
        return sorted(values)

    def filter_errors(
        self,
        severity: Optional[str] = None,
        source: Optional[str] = None,
        user: Optional[str] = None,
        time_from: Optional[str] = None,
        time_to: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Filter errors based on criteria."""
        filtered = []
        dt_from = _parse_iso(time_from) if time_from else None
        dt_to = _parse_iso(time_to) if time_to else None

        for err in self.errors:
            # Severity filter
            if severity and err.get("severity") != severity:
                continue
            # Source filter
            if source and err.get("source") != source:
                continue
            # User filter
            if user and err.get("user") != user:
                continue
            # Time range filter
            ts_str = err.get("timestamp")
            if ts_str:
                ts = _parse_iso(ts_str)
                if ts:
                    if dt_from and ts < dt_from:
                        continue
                    if dt_to and ts > dt_to:
                        continue
            filtered.append(err)
        return filtered

    def top_groups(
        self,
        n: int = 10,
        severity: Optional[str] = None,
        source: Optional[str] = None,
        user: Optional[str] = None,
        time_from: Optional[str] = None,
        time_to: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return top N error groups by frequency, with optional filters."""
        filtered = self.filter_errors(severity, source, user, time_from, time_to)
        groups: Dict[str, Dict[str, Any]] = {}

        for err in filtered:
            key = err.get("sha256_dedup_key")
            if not key:
                continue

            if key not in groups:
                groups[key] = {
                    "sha256_dedup_key": key,
                    "count": 0,
                    "first_seen": err["timestamp"],
                    "last_seen": err["timestamp"],
                    "severity": err.get("severity"),
                    "source": err.get("source"),
                    "message": err.get("message"),
                    "sample_traceback": err.get("traceback"),
                    "users": set(),
                }
            group = groups[key]
            group["count"] += 1
            if err.get("user"):
                group["users"].add(err["user"])
            # Update timestamps
            ts = err.get("timestamp")
            if ts:
                if ts < group["first_seen"]:
                    group["first_seen"] = ts
                if ts > group["last_seen"]:
                    group["last_seen"] = ts

        # Convert sets to lists for JSON serialization
        result = []
        for _key, group in groups.items():
            group["users"] = sorted(group["users"])
            result.append(group)

        # Sort by count descending, then by last_seen descending
        result.sort(key=lambda x: (-x["count"], x["last_seen"]), reverse=True)
        return result[:n]


class ErrorDashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the error dashboard."""

    def __init__(self, store: ErrorStore, *args: Any, **kwargs: Any) -> None:
        """Initialize with an error store."""
        self.store = store
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        """Handle GET requests."""
        parsed = urlparse(self.path)
        if parsed.path == "/admin/errors":
            self._serve_dashboard()
        elif parsed.path == "/admin/errors/api/groups":
            self._api_groups(parsed)
        elif parsed.path == "/admin/errors/api/meta":
            self._api_meta()
        else:
            self.send_error(404, "Not Found")

    def _serve_dashboard(self) -> None:
        """Serve the main dashboard HTML page."""
        html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Error Dashboard</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               background: #f5f5f5; color: #333; line-height: 1.6; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        header { margin-bottom: 30px; }
        h1 { color: #2c3e50; margin-bottom: 10px; }
        .subtitle { color: #7f8c8d; font-size: 1.1em; }
        .filters { background: white; border-radius: 8px; padding: 20px;
                   margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .filter-row { display: flex; flex-wrap: wrap; gap: 15px; margin-bottom: 15px; }
        .filter-group { flex: 1; min-width: 200px; }
        label { display: block; margin-bottom: 5px; font-weight: 600; color: #2c3e50; }
        select, input { width: 100%; padding: 8px 12px; border: 1px solid #ddd;
                        border-radius: 4px; font-size: 14px; }
        .actions { display: flex; gap: 10px; margin-top: 20px; }
        button { padding: 10px 20px; border: none; border-radius: 4px;
                 cursor: pointer; font-weight: 600; transition: background 0.2s; }
        .btn-primary { background: #3498db; color: white; }
        .btn-primary:hover { background: #2980b9; }
        .btn-secondary { background: #95a5a6; color: white; }
        .btn-secondary:hover { background: #7f8c8d; }
        .error-count { margin: 20px 0; font-size: 1.2em; color: #2c3e50; }
        .error-table { background: white; border-radius: 8px; overflow: hidden;
                       box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        table { width: 100%; border-collapse: collapse; }
        th { background: #2c3e50; color: white; text-align: left; padding: 15px; }
        td { padding: 15px; border-bottom: 1px solid #eee; }
        tr:hover { background: #f9f9f9; }
        .severity { padding: 4px 8px; border-radius: 4px; font-size: 0.85em; font-weight: 600; }
        .severity-debug { background: #ecf0f1; color: #7f8c8d; }
        .severity-info { background: #d6eaf8; color: #2c3e50; }
        .severity-warning { background: #fef9e7; color: #b7950b; }
        .severity-error { background: #fdedec; color: #c0392b; }
        .severity-critical { background: #f2d7d5; color: #922b21; }
        .traceback { font-family: monospace; font-size: 12px; white-space: pre-wrap;
                     background: #f8f9fa; padding: 10px; border-radius: 4px;
                     border-left: 3px solid #3498db; margin-top: 5px; max-height: 200px;
                     overflow-y: auto; }
        .loading { text-align: center; padding: 40px; color: #7f8c8d; }
        .error { color: #e74c3c; padding: 10px; background: #fdedec; border-radius: 4px;
                 margin: 10px 0; }
        .timestamp { font-size: 0.9em; color: #7f8c8d; }
        .user-list { font-size: 0.9em; color: #3498db; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Error Dashboard</h1>
            <p class="subtitle">Monitor and analyze application errors</p>
        </header>

        <div class="filters">
            <div class="filter-row">
                <div class="filter-group">
                    <label for="severity">Severity</label>
                    <select id="severity">
                        <option value="">All</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label for="source">Source</label>
                    <select id="source">
                        <option value="">All</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label for="user">User</label>
                    <select id="user">
                        <option value="">All</option>
                    </select>
                </div>
            </div>
            <div class="filter-row">
                <div class="filter-group">
                    <label for="time-from">From (ISO 8601)</label>
                    <input type="text" id="time-from" placeholder="2024-01-01T00:00:00Z">
                </div>
                <div class="filter-group">
                    <label for="time-to">To (ISO 8601)</label>
                    <input type="text" id="time-to" placeholder="2024-01-02T00:00:00Z">
                </div>
            </div>
            <div class="actions">
                <button class="btn-primary" onclick="loadErrors()">Apply Filters</button>
                <button class="btn-secondary" onclick="resetFilters()">Reset</button>
            </div>
        </div>

        <div id="error-count" class="error-count"></div>
        <div id="error-table" class="error-table">
            <div class="loading">Loading error data...</div>
        </div>
    </div>

    <script>
        let currentFilters = {};

        function formatTimestamp(iso) {
            return new Date(iso).toLocaleString();
        }

        function severityClass(severity) {
            return 'severity severity-' + severity;
        }

        function loadMeta() {
            fetch('/admin/errors/api/meta')
                .then(r => r.json())
                .then(meta => {
                    // Populate severity dropdown
                    const severitySelect = document.getElementById('severity');
                    meta.severities.forEach(s => {
                        const opt = document.createElement('option');
                        opt.value = s;
                        opt.textContent = s.charAt(0).toUpperCase() + s.slice(1);
                        severitySelect.appendChild(opt);
                    });

                    // Populate source dropdown
                    const sourceSelect = document.getElementById('source');
                    meta.sources.forEach(s => {
                        const opt = document.createElement('option');
                        opt.value = s;
                        opt.textContent = s;
                        sourceSelect.appendChild(opt);
                    });

                    // Populate user dropdown
                    const userSelect = document.getElementById('user');
                    meta.users.forEach(u => {
                        const opt = document.createElement('option');
                        opt.value = u;
                        opt.textContent = u;
                        userSelect.appendChild(opt);
                    });
                })
                .catch(err => console.error('Failed to load meta:', err));
        }

        function buildQueryString(filters) {
            const params = new URLSearchParams();
            if (filters.severity) params.set('severity', filters.severity);
            if (filters.source) params.set('source', filters.source);
            if (filters.user) params.set('user', filters.user);
            if (filters.time_from) params.set('time_from', filters.time_from);
            if (filters.time_to) params.set('time_to', filters.time_to);
            return params.toString();
        }

        function loadErrors() {
            const filters = {
                severity: document.getElementById('severity').value,
                source: document.getElementById('source').value,
                user: document.getElementById('user').value,
                time_from: document.getElementById('time-from').value,
                time_to: document.getElementById('time-to').value
            };
            currentFilters = filters;

            const query = buildQueryString(filters);
            const url = '/admin/errors/api/groups' + (query ? '?' + query : '');

            document.getElementById('error-table').innerHTML =
                '<div class="loading">Loading error data...</div>';

            fetch(url)
                .then(r => {
                    if (!r.ok) throw new Error(`HTTP ${r.status}`);
                    return r.json();
                })
                .then(groups => {
                    renderErrors(groups);
                })
                .catch(err => {
                    document.getElementById('error-table').innerHTML =
                        '<div class="error">Failed to load errors: ' + err.message + '</div>';
                });
        }

        function renderErrors(groups) {
            const container = document.getElementById('error-table');

            if (groups.length === 0) {
                container.innerHTML = '<div class="loading">No errors found matching filters</div>';
                document.getElementById('error-count').textContent = 'No errors found';
                return;
            }

            document.getElementById('error-count').textContent =
                `Showing ${groups.length} error group${groups.length === 1 ? '' : 's'}`;

            let html = '<table><thead><tr>' +
                '<th>Count</th><th>Severity</th><th>Source</th><th>Message</th>' +
                '<th>First Seen</th><th>Last Seen</th><th>Affected Users</th></tr></thead><tbody>';

            groups.forEach(group => {
                html += '<tr>' +
                    '<td><strong>' + group.count + '</strong></td>' +
                    '<td><span class="' + severityClass(group.severity) + '">' +
                        group.severity + '</span></td>' +
                    '<td>' + (group.source || '—') + '</td>' +
                    '<td>' + group.message +
                    '<div class="traceback">' + (group.sample_traceback || 'No traceback') +
                    '</div></td>' +
                    '<td class="timestamp">' + formatTimestamp(group.first_seen) + '</td>' +
                    '<td class="timestamp">' + formatTimestamp(group.last_seen) + '</td>' +
                    '<td class="user-list">' + (group.users.join(', ') || '—') + '</td>' +
                    '</tr>';
            });

            html += '</tbody></table>';
            container.innerHTML = html;
        }

        function resetFilters() {
            document.getElementById('severity').value = '';
            document.getElementById('source').value = '';
            document.getElementById('user').value = '';
            document.getElementById('time-from').value = '';
            document.getElementById('time-to').value = '';
            loadErrors();
        }

        // Load meta data and initial errors on page load
        document.addEventListener('DOMContentLoaded', () => {
            loadMeta();
            loadErrors();
        });
    </script>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _api_groups(self, parsed: Any) -> None:
        """Return top-10 error groups as JSON, with optional filters."""
        params = parse_qs(parsed.query)
        groups = self.store.top_groups(
            n=10,
            severity=_first(params.get("severity")),
            source=_first(params.get("source")),
            user=_first(params.get("user")),
            time_from=_first(params.get("time_from")),
            time_to=_first(params.get("time_to")),
        )
        self._json_response(groups)

    def _api_meta(self) -> None:
        """Return distinct filter values for dropdowns."""
        meta = {
            "severities": list(SEVERITY_LEVELS),
            "sources": self.store.distinct_values("source"),
            "users": self.store.distinct_values("user"),
        }
        self._json_response(meta)

    def _json_response(self, data: Any) -> None:
        """Send a JSON response with appropriate headers."""
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        """Override to suppress default stderr logging."""
        sys.stderr.write(f"[dashboard] {self.client_address[0]} - {fmt % args}\n")


def _first(lst: Optional[List[str]]) -> Optional[str]:
    """Return the first element of a list, or None."""
    return lst[0] if lst else None


def main(argv: Optional[List[str]] = None) -> int:
    """Parse CLI arguments and start the dashboard HTTP server.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 on success, 1 on error).
    """
    parser = argparse.ArgumentParser(
        description="Error monitoring dashboard — serves /admin/errors",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=8080, help="Bind port")
    parser.add_argument("--data-file", default=None, help="JSON file with error records")
    parser.add_argument("--sample", action="store_true", help="Generate sample data")
    parser.add_argument("--save-sample", default=None, help="Save sample data to path and exit")
    args = parser.parse_args(argv)

    if args.save_sample:
        sample = generate_sample_errors()
        with open(args.save_sample, "w", encoding="utf-8") as fh:
            json.dump(sample, fh, indent=2, ensure_ascii=False)
        print(f"Saved {len(sample)} sample errors to {args.save_sample}")
        return 0

    store = ErrorStore(data_file=args.data_file)
    if args.sample and not store.get_all():
        print("Generating sample error data...")
        store.add_errors(generate_sample_errors())

    if not store.get_all():
        print("No error data available. Use --sample to generate sample data or --data-file to load from file.")
        return 1

    print(f"Loaded {len(store.get_all())} error records")
    print(f"Dashboard available at http://{args.host}:{args.port}/admin/errors")

    def handler_factory(*args: Any, **kwargs: Any) -> ErrorDashboardHandler:
        return ErrorStoreHandler(store, *args, **kwargs)

    class ErrorStoreHandler(ErrorDashboardHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(store, *args, **kwargs)

    server = HTTPServer((args.host, args.port), ErrorStoreHandler)
    try:
        print(f"Server starting on {args.host}:{args.port} (Ctrl+C to stop)")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

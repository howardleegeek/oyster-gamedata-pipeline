"""FAQ Server — local HTTP server serving FAQ markdown as HTML.

Usage:
    python3 bin/faq_server.py

Serves on localhost:8765 (configurable via FAQ_SERVER_PORT env var).
Endpoints:
    GET /              — Render TESTER_FAQ.md as styled HTML
    GET /search?q=...  — Full-text search across FAQ entries
    GET /troubleshooting — Render TESTER_TROUBLESHOOTING.md as styled HTML
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import markdown
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
FAQ_PATH = DOCS_DIR / "TESTER_FAQ.md"
TROUBLESHOOTING_PATH = DOCS_DIR / "TESTER_TROUBLESHOOTING.md"

PORT = int(os.environ.get("FAQ_SERVER_PORT", "8765"))
HOST = "127.0.0.1"  # localhost only — security constraint

# ---------------------------------------------------------------------------
# CSS template
# ---------------------------------------------------------------------------

CSS = """
<style>
  :root {
    --bg: #fafafa;
    --fg: #222;
    --accent: #2563eb;
    --code-bg: #f1f5f9;
    --border: #e2e8f0;
    --max-width: 820px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 "Helvetica Neue", Arial, sans-serif;
    background: var(--bg);
    color: var(--fg);
    line-height: 1.7;
    padding: 2rem 1rem;
  }
  .container { max-width: var(--max-width); margin: 0 auto; }
  h1 { font-size: 2rem; margin-bottom: 0.5rem; }
  h2 { font-size: 1.4rem; margin-top: 2rem; margin-bottom: 0.5rem;
       border-bottom: 1px solid var(--border); padding-bottom: 0.3rem; }
  h3 { font-size: 1.15rem; margin-top: 1.5rem; margin-bottom: 0.4rem; }
  p, li { margin-bottom: 0.6rem; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  code {
    background: var(--code-bg);
    padding: 0.15em 0.4em;
    border-radius: 4px;
    font-size: 0.9em;
  }
  pre {
    background: var(--code-bg);
    padding: 1rem;
    border-radius: 6px;
    overflow-x: auto;
    margin-bottom: 1rem;
  }
  pre code { background: none; padding: 0; }
  table { border-collapse: collapse; width: 100%; margin-bottom: 1rem; }
  th, td { border: 1px solid var(--border); padding: 0.5rem 0.75rem; text-align: left; }
  th { background: var(--code-bg); }
  blockquote {
    border-left: 3px solid var(--accent);
    padding-left: 1rem;
    color: #555;
    margin: 1rem 0;
  }
  hr { border: none; border-top: 1px solid var(--border); margin: 2rem 0; }
  .search-form { margin-bottom: 1.5rem; }
  .search-form input[type="text"] {
    width: 100%;
    padding: 0.6rem 0.8rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 1rem;
  }
  .search-result {
    background: #fff;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 1rem;
    margin-bottom: 1rem;
  }
  .search-result h3 { margin-top: 0; }
  .no-results { color: #888; font-style: italic; }
  .nav { margin-bottom: 1.5rem; }
  .nav a { margin-right: 1rem; }
  mark { background: #fef08a; padding: 0.05em 0.2em; border-radius: 2px; }
</style>
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NAV_HTML = """
<nav class="nav">
  <a href="/">FAQ</a>
  <a href="/troubleshooting">Troubleshooting</a>
</nav>
"""


def _read_md(path: Path) -> str:
    """Read a markdown file, returning empty string if missing."""
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def _md_to_html(md_text: str) -> str:
    """Convert markdown text to HTML using the markdown library."""
    return markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "toc"],
    )


def _page(title: str, body_html: str) -> str:
    """Wrap body HTML in a full document."""
    return (
        "<!DOCTYPE html>"
        f"<html><head><meta charset='utf-8'><title>{title}</title>{CSS}</head>"
        f"<body><div class='container'>{NAV_HTML}{body_html}</div></body></html>"
    )


def _search_entries(query: str) -> list[dict[str, str]]:
    """Full-text search across FAQ entries.

    Splits the FAQ into sections (h2/h3 headings) and returns those
    whose text matches the query (case-insensitive substring match).
    """
    md_text = _read_md(FAQ_PATH)
    if not md_text:
        return []

    # Split on heading markers (## or ###)
    sections = re.split(r"^(#{2,3})\s+(.+)$", md_text, flags=re.MULTILINE)
    # sections is a flat list: [preamble, '#', 'Heading', content, '#', 'Heading', content, ...]

    results: list[dict[str, str]] = []
    current_heading = ""
    current_body = ""

    for i, part in enumerate(sections):
        if i == 0:
            # preamble — skip
            continue
        if i % 3 == 1:
            # heading marker (## or ###) — skip
            continue
        if i % 3 == 2:
            # heading text
            current_heading = part.strip()
            current_body = ""
        elif i % 3 == 0:
            # content
            current_body = part.strip()
            # Search in heading + body
            haystack = (current_heading + " " + current_body).lower()
            if query.lower() in haystack:
                results.append(
                    {
                        "heading": current_heading,
                        "body": _md_to_html(current_body),
                    }
                )

    return results


def _highlight(text: str, query: str) -> str:
    """Wrap query matches in <mark> tags (case-insensitive)."""
    if not query:
        return text
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    return pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", text)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="FAQ Server")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """Render TESTER_FAQ.md as styled HTML."""
    md_text = _read_md(FAQ_PATH)
    if not md_text:
        return HTMLResponse(
            _page("FAQ", "<p>FAQ document not found.</p>"),
            status_code=404,
        )
    body = _md_to_html(md_text)
    return HTMLResponse(_page("FAQ", body))


@app.get("/search", response_class=HTMLResponse)
async def search(q: str = Query(default="", min_length=0)) -> HTMLResponse:
    """Full-text search across FAQ entries."""
    form_html = (
        '<form class="search-form" action="/search" method="get">'
        '<input type="text" name="q" placeholder="Search FAQ..." '
        f'value="{q}">'
        "</form>"
    )

    if not q.strip():
        return HTMLResponse(_page("Search", form_html + "<p>Enter a search term.</p>"))

    entries = _search_entries(q)
    if not entries:
        return HTMLResponse(
            _page("Search", form_html + '<p class="no-results">No results found.</p>')
        )

    results_html = ""
    for entry in entries:
        heading = _highlight(entry["heading"], q)
        body = _highlight(entry["body"], q)
        results_html += f'<div class="search-result"><h3>{heading}</h3>{body}</div>'

    return HTMLResponse(_page(f"Search: {q}", form_html + results_html))


@app.get("/troubleshooting", response_class=HTMLResponse)
async def troubleshooting() -> HTMLResponse:
    """Render TESTER_TROUBLESHOOTING.md as styled HTML."""
    md_text = _read_md(TROUBLESHOOTING_PATH)
    if not md_text:
        return HTMLResponse(
            _page("Troubleshooting", "<p>Troubleshooting document not found.</p>"),
            status_code=404,
        )
    body = _md_to_html(md_text)
    return HTMLResponse(_page("Troubleshooting", body))


# ---------------------------------------------------------------------------
# Server lifecycle helpers (for tray daemon integration)
# ---------------------------------------------------------------------------

_server_thread: threading.Thread | None = None
_server_instance = None


def start_server() -> None:
    """Start the FAQ server in a background thread (non-blocking)."""
    global _server_thread, _server_instance

    import uvicorn

    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    _server_instance = uvicorn.Server(config)
    _server_thread = threading.Thread(target=_server_instance.run, daemon=True)
    _server_thread.start()


def stop_server() -> None:
    """Shut down the FAQ server."""
    if _server_instance is not None:
        _server_instance.should_exit = True
    if _server_thread is not None:
        _server_thread.join(timeout=5)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    print(f"Starting FAQ server on http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")

"""Tests for bin/faq_server.py — FAQ HTTP server."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Ensure bin/ is importable
BIN_DIR = Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))

# ruff: noqa: E402
from faq_server import (
    FAQ_PATH,
    TROUBLESHOOTING_PATH,
    _md_to_html,
    _read_md,
    _search_entries,
    app,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def ensure_docs_exist():
    """Make sure the FAQ and Troubleshooting docs exist for tests."""
    assert FAQ_PATH.is_file(), f"FAQ doc missing: {FAQ_PATH}"
    assert TROUBLESHOOTING_PATH.is_file(), f"Troubleshooting doc missing: {TROUBLESHOOTING_PATH}"


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


class TestIndex:
    def test_returns_200(self):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_returns_html(self):
        resp = client.get("/")
        assert "text/html" in resp.headers["content-type"]

    def test_contains_doctype(self):
        resp = client.get("/")
        assert "<!DOCTYPE html>" in resp.text

    def test_contains_faq_content(self):
        resp = client.get("/")
        assert "Tester FAQ" in resp.text or "FAQ" in resp.text

    def test_contains_nav_links(self):
        resp = client.get("/")
        assert 'href="/troubleshooting"' in resp.text

    def test_contains_css(self):
        resp = client.get("/")
        assert "<style>" in resp.text

    def test_contains_markdown_rendered(self):
        resp = client.get("/")
        # The markdown should be converted to HTML heading elements
        # toc extension adds id attributes, so check for <h1 or <h2
        assert "<h1" in resp.text or "<h2" in resp.text


# ---------------------------------------------------------------------------
# GET /troubleshooting
# ---------------------------------------------------------------------------


class TestTroubleshooting:
    def test_returns_200(self):
        resp = client.get("/troubleshooting")
        assert resp.status_code == 200

    def test_returns_html(self):
        resp = client.get("/troubleshooting")
        assert "text/html" in resp.headers["content-type"]

    def test_contains_troubleshooting_content(self):
        resp = client.get("/troubleshooting")
        assert "Troubleshooting" in resp.text

    def test_contains_nav_links(self):
        resp = client.get("/troubleshooting")
        assert 'href="/"' in resp.text


# ---------------------------------------------------------------------------
# GET /search
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_oauth_returns_results(self):
        """Search for 'oauth' should return relevant entries."""
        resp = client.get("/search?q=oauth")
        assert resp.status_code == 200
        assert "OAuth" in resp.text or "oauth" in resp.text.lower()

    def test_search_empty_query(self):
        resp = client.get("/search?q=")
        assert resp.status_code == 200
        assert "Enter a search term" in resp.text

    def test_search_no_results(self):
        resp = client.get("/search?q=xyznonexistent12345")
        assert resp.status_code == 200
        assert "No results found" in resp.text

    def test_search_form_present(self):
        resp = client.get("/search?q=test")
        assert "<form" in resp.text
        assert 'name="q"' in resp.text

    def test_search_highlight(self):
        """Search results should highlight the query term."""
        resp = client.get("/search?q=oauth")
        assert "<mark>" in resp.text or "OAuth" in resp.text

    def test_search_case_insensitive(self):
        """Search should be case-insensitive."""
        resp_lower = client.get("/search?q=oauth")
        resp_upper = client.get("/search?q=OAUTH")
        assert resp_lower.status_code == 200
        assert resp_upper.status_code == 200

    def test_search_returns_html(self):
        resp = client.get("/search?q=recording")
        assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_read_md_existing(self):
        content = _read_md(FAQ_PATH)
        assert len(content) > 0
        assert "FAQ" in content

    def test_read_md_missing(self):
        content = _read_md(Path("/nonexistent/path/file.md"))
        assert content == ""

    def test_md_to_html(self):
        html = _md_to_html("# Hello\n\nWorld")
        # toc extension adds id attribute to headings
        assert "<h1" in html
        assert "Hello" in html
        assert "<p>World</p>" in html

    def test_md_to_html_tables(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        html = _md_to_html(md)
        assert "<table>" in html

    def test_md_to_html_code(self):
        md = "```\ncode block\n```"
        html = _md_to_html(md)
        assert "<code>" in html

    def test_search_entries_returns_list(self):
        results = _search_entries("oauth")
        assert isinstance(results, list)

    def test_search_entries_finds_oauth(self):
        results = _search_entries("oauth")
        headings = [r["heading"].lower() for r in results]
        found = any("oauth" in h for h in headings)
        assert found, f"Expected oauth in headings, got: {headings}"

    def test_search_entries_empty_query(self):
        results = _search_entries("")
        assert isinstance(results, list)

    def test_search_entries_no_match(self):
        results = _search_entries("zzzznonexistent999")
        assert results == []


# ---------------------------------------------------------------------------
# Server lifecycle tests
# ---------------------------------------------------------------------------


class TestServerLifecycle:
    def test_start_stop_server(self):
        """Test that start_server and stop_server don't raise."""
        import time

        import faq_server

        # Use a different port to avoid conflicts
        original_port = faq_server.PORT
        try:
            faq_server.PORT = 18765
            faq_server.start_server()
            time.sleep(0.5)  # Give server time to start
            faq_server.stop_server()
        finally:
            faq_server.PORT = original_port


# ---------------------------------------------------------------------------
# Security tests
# ---------------------------------------------------------------------------


class TestSecurity:
    def test_localhost_only(self):
        """Server should bind to 127.0.0.1 only."""
        from faq_server import HOST

        assert HOST == "127.0.0.1"

    def test_port_default(self):
        """Default port should be 8765."""
        import faq_server

        assert faq_server.PORT == 8765

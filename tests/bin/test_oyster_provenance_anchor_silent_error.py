"""
Regression test: oyster_provenance/anchor.py silent error surfacing.

This test verifies that the bare `except Exception:` block in
get_anchor_for_session() has been replaced with a bound exception
and debug logging.

Issue: get_anchor_for_session() had a bare `except Exception:` that
silently swallowed all date-parsing errors (malformed consent_signed_at_utc,
calendar edge cases, etc.) and returned None with no diagnostic. Fixed
by binding to `exc` and logging at DEBUG so we can distinguish "no
anchor on disk" from "consent timestamp was unparseable."
"""

import ast
import logging
import sys
from pathlib import Path

# Ensure oyster_provenance is in path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from oyster_provenance import anchor  # noqa: E402


SOURCE_FILE = (
    Path(__file__).parent.parent.parent / "oyster_provenance" / "anchor.py"
)


def _read_source() -> str:
    with open(SOURCE_FILE, "r") as f:
        return f.read()


def _function_bare_excepts(tree, func_name):
    """Return list of (lineno, label) for any bare/unbound except in func."""
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Try):
                    for handler in stmt.handlers:
                        if handler.type is None:
                            issues.append((handler.lineno, "bare except (no type)"))
                        elif (
                            isinstance(handler.type, ast.Name)
                            and handler.type.id == "Exception"
                            and handler.name is None
                        ):
                            issues.append(
                                (handler.lineno, "`except Exception:` without binding")
                            )
            return issues
    raise AssertionError(f"{func_name} not found in anchor.py")


class TestAnchorSilentErrorSurfacing:
    """Tests for silent error surfacing in oyster_provenance/anchor.py."""

    def test_logger_imported(self):
        """Module must define a module-level logger via logging.getLogger(__name__)."""
        source = _read_source()
        assert "import logging" in source, "anchor.py must import logging"
        assert "logger = logging.getLogger(__name__)" in source, (
            "anchor.py must define module-level logger"
        )

    def test_no_bare_except_in_get_anchor_for_session(self):
        """get_anchor_for_session must not have a bare except Exception:."""
        source = _read_source()
        tree = ast.parse(source)
        issues = _function_bare_excepts(tree, "get_anchor_for_session")
        assert not issues, (
            f"get_anchor_for_session has bare/unbound except(s): {issues} — "
            f"must bind to `exc` and log via logger.debug()"
        )

    def test_logger_debug_in_get_anchor_for_session(self):
        """get_anchor_for_session's except block must call logger.debug()."""
        source = _read_source()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "get_anchor_for_session"
            ):
                for stmt in ast.walk(node):
                    if isinstance(stmt, ast.Try):
                        for handler in stmt.handlers:
                            hs = ast.get_source_segment(source, handler) or ""
                            assert "logger.debug" in hs, (
                                f"get_anchor_for_session except at "
                                f"L{handler.lineno} must call logger.debug()"
                            )
                return
        raise AssertionError("get_anchor_for_session not found")

    def test_get_anchor_for_session_logs_on_bad_consent_time(self, caplog, tmp_path):
        """get_anchor_for_session must log at DEBUG when consent_signed_at_utc is malformed."""
        # Build a minimal session dir with a provenance manifest whose
        # consent_signed_at_utc is unparseable, so datetime.fromisoformat raises.
        session_dir = tmp_path / "session_bad"
        session_dir.mkdir()
        manifest = session_dir / "provenance.json"
        manifest.write_text(
            '{"consent_signed_at_utc": "this-is-not-a-timestamp"}'
        )

        with caplog.at_level(
            logging.DEBUG, logger="oyster_provenance.anchor"
        ):
            result = anchor.get_anchor_for_session(
                session_dir=str(session_dir),
                anchors_dir=str(tmp_path / "anchors"),
            )

        # Public contract preserved: bad consent_time still returns None.
        assert result is None, "Bad consent time must still return None"

        # New contract: we log the underlying reason at DEBUG.
        assert any(
            "consent_signed_at_utc" in record.message
            for record in caplog.records
        ), "Expected DEBUG log naming the consent time parse failure"

    def test_get_anchor_for_session_returns_none_for_missing_manifest(self, caplog, tmp_path):
        """get_anchor_for_session must return None silently (no log) when manifest is missing."""
        session_dir = tmp_path / "session_missing"
        session_dir.mkdir()
        # No provenance.json

        with caplog.at_level(
            logging.DEBUG, logger="oyster_provenance.anchor"
        ):
            result = anchor.get_anchor_for_session(
                session_dir=str(session_dir),
                anchors_dir=str(tmp_path / "anchors"),
            )

        assert result is None
        # Missing manifest is a normal "no anchor" path, not a swallowed error:
        # no DEBUG record expected.
        assert not any(
            "Failed to parse" in record.message
            for record in caplog.records
        ), "Missing manifest must not emit a parse-failure DEBUG log"

    def test_module_compiles(self):
        """Sanity check: anchor.py must compile without syntax errors."""
        source = _read_source()
        compile(source, str(SOURCE_FILE), "exec")

    def test_logger_is_module_logger(self):
        """Module-level `logger` must come from logging.getLogger(__name__)."""
        assert hasattr(anchor, "logger"), "anchor module must expose module-level logger"
        assert isinstance(anchor.logger, logging.Logger)
        assert anchor.logger.name == "oyster_provenance.anchor"

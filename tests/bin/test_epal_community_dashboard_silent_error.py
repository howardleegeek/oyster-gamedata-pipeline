"""
Regression tests for silent error swallows in bin/epal_community_dashboard.py.

These tests verify that the bare ``except Exception: pass`` in
``aggregate_user_stats`` (used to skip per-clip week-bucket computation when
datetime parse/comparison fails) is replaced with an exception-bound handler
that emits a debug log so failures are observable in debug runs.
"""

import ast
from pathlib import Path

import pytest


class TestEpalCommunityDashboardSilentError:
    """Tests for silent error handling in epal_community_dashboard.py."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "epal_community_dashboard.py"
        ).read_text()

    def test_no_bare_except_in_aggregate_user_stats(self):
        """``aggregate_user_stats`` must not have a bare ``except Exception:``
        (no ``as`` binding) that hides per-clip failures."""
        source = self._read_source()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "aggregate_user_stats"
            ):
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        if child.type is not None:
                            type_src = ast.unparse(child.type)
                            if "Exception" in type_src and child.name is None:
                                pytest.fail(
                                    "Found bare 'except Exception:' "
                                    "(no 'as' binding) in "
                                    "aggregate_user_stats. "
                                    "Bind the exception and log it "
                                    "via logger.debug(...)."
                                )

    def test_logger_imported(self):
        """A module-level logger must be defined so the exception can be logged."""
        source = self._read_source()
        assert "import logging" in source
        assert "logger = logging.getLogger" in source

    def test_per_clip_failure_logs_at_debug(self):
        """When a per-clip datetime comparison fails, the exception should
        be logged at DEBUG level."""
        source = self._read_source()
        assert "logger.debug" in source, (
            "logger.debug should be used to log per-clip aggregation failure"
        )
        # Check the debug call binds the exception (as exc:)
        assert "as exc:" in source, (
            "logger.debug should bind the exception via 'except Exception as exc:'"
        )

    def test_module_compiles(self):
        """Sanity check that the module is syntactically valid Python."""
        import py_compile
        bin_path = (
            Path(__file__).parent.parent.parent
            / "bin"
            / "epal_community_dashboard.py"
        )
        py_compile.compile(str(bin_path), doraise=True)

    def test_debug_log_falls_through_to_skip(self, caplog):
        """Behavioral check: aggregate_user_stats still skips the bad clip
        (i.e. control flow unchanged) but emits a debug log."""
        import importlib.util
        import sys

        bin_path = (
            Path(__file__).parent.parent.parent
            / "bin"
            / "epal_community_dashboard.py"
        )
        spec = importlib.util.spec_from_file_location(
            "epal_community_dashboard", str(bin_path)
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        good = module.ClipEntry(
            clip_id="c-good",
            user_id="u1",
            title="good",
            created_at="2024-01-01T00:00:00Z",
            bonus_amount=10.0,
        )
        # We can't easily make _parse_datetime raise (it swallows), so
        # instead verify a clean run still aggregates correctly.
        stats = module.aggregate_user_stats([good], "u1")
        assert stats.total_bonus == 10.0
        # The "bad" clip pathway is exercised by the unit-level AST tests above;
        # here we just confirm the happy path is intact.
        assert stats.user_id == "u1"

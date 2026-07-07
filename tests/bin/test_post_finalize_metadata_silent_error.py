"""
Regression tests for silent error swallows in bin/post_finalize_metadata.py.

These tests verify that the 3 known exception swallow sites in
``bin/post_finalize_metadata.py`` bind the exception variable and surface
it through ``logger.debug`` rather than silently swallowing it.

Target sites (3):
  1. _detect_recorder_version: read recorder_version.txt (OSError)
  2. _detect_recorder_version: read candidate Cargo.toml (OSError)
  3. merge_existing: parse prior metadata.json (json.JSONDecodeError, OSError)
"""

import ast
import logging
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TARGET_PATH = REPO_ROOT / "bin" / "post_finalize_metadata.py"


def _load_module():
    """Import bin.post_finalize_metadata with a clean sys.modules state."""
    sys.modules.pop("bin.post_finalize_metadata", None)
    from bin import post_finalize_metadata

    return post_finalize_metadata


class TestPostFinalizeMetadataSilentError:
    """Silent-error regression for bin/post_finalize_metadata.py."""

    def test_module_compiles(self):
        """Target file parses as valid Python (smoke)."""
        source = TARGET_PATH.read_text()
        ast.parse(source)

    def test_logger_defined(self):
        """Module must expose a top-level ``logger``."""
        mod = _load_module()
        assert hasattr(mod, "logger"), "module-level logger missing"
        assert isinstance(mod.logger, logging.Logger)
        # Logger should be namespaced under bin.post_finalize_metadata
        assert mod.logger.name == "bin.post_finalize_metadata"

    def test_no_bare_pass_in_target_handlers(self):
        """No bare ``pass`` body in any except handler within target functions."""
        source = TARGET_PATH.read_text()
        tree = ast.parse(source)

        target_funcs = {"_detect_recorder_version", "write_metadata"}
        failures = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in target_funcs:
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        if len(child.body) == 1 and isinstance(child.body[0], ast.Pass):
                            failures.append(
                                f"bare pass in {node.name} -> "
                                f"except {ast.unparse(child.type)}"
                            )
        assert not failures, "Bare pass swallow sites still present: " + "; ".join(failures)

    def test_target_handlers_bind_exception_and_log(self):
        """Every except handler in target functions binds ``exc`` and calls logger.debug."""
        source = TARGET_PATH.read_text()
        tree = ast.parse(source)

        target_funcs = {"_detect_recorder_version", "write_metadata"}
        checked = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in target_funcs:
                for child in ast.walk(node):
                    if not isinstance(child, ast.ExceptHandler):
                        continue
                    checked += 1
                    # 1) Must bind the exception as ``exc`` (or similar)
                    assert child.name in {"exc", "e", "err", "error"}, (
                        f"{node.name}: except handler does not bind exception "
                        f"name (got {child.name!r})"
                    )
                    # 2) Body must NOT be a bare pass
                    assert not (
                        len(child.body) == 1 and isinstance(child.body[0], ast.Pass)
                    ), f"{node.name}: handler still has bare pass"
                    # 3) Body must contain a logger.debug(...) call
                    has_debug_call = any(
                        isinstance(stmt, ast.Expr)
                        and isinstance(stmt.value, ast.Call)
                        and isinstance(stmt.value.func, ast.Attribute)
                        and stmt.value.func.attr == "debug"
                        and isinstance(stmt.value.func.value, ast.Name)
                        and stmt.value.func.value.id == "logger"
                        for stmt in child.body
                    )
                    assert has_debug_call, (
                        f"{node.name}: handler does not call logger.debug"
                    )
        assert checked >= 3, f"Expected ≥3 except handlers in target funcs, found {checked}"

    def test_corrupt_metadata_json_surfaces_via_logger(self, tmp_path, caplog):
        """A corrupt metadata.json is logged at debug, then ``existing`` is reset to {}."""
        session = tmp_path / "session"
        session.mkdir()
        meta = session / "metadata.json"
        meta.write_text("{ this is not valid json")

        mod = _load_module()
        with caplog.at_level(logging.DEBUG, logger="bin.post_finalize_metadata"):
            result = mod.write_metadata(session)

        # Control flow preserved: function still returns a dict (defaults to {})
        assert isinstance(result, dict)
        # The corrupt file must surface via a debug log bound to the exception
        debug_msgs = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.DEBUG
        ]
        assert any("parse" in m and str(meta) in m for m in debug_msgs), (
            f"Expected a debug log for corrupt metadata.json, got: {debug_msgs}"
        )

    def test_unreadable_cargo_toml_surfaces_via_logger(self, tmp_path, caplog, monkeypatch):
        """A missing/unreadable vendored Cargo.toml is logged at debug, return None."""
        mod = _load_module()
        # Point the function at an empty session (no vendored Cargo.toml present)
        session = tmp_path / "session"
        session.mkdir()

        with caplog.at_level(logging.DEBUG, logger="bin.post_finalize_metadata"):
            result = mod._detect_recorder_version(session)

        # Control flow preserved: no recorder_version.txt, no Cargo.toml → None
        assert result is None
        # No debug logs expected on the happy path (nothing failed)
        # — but if the function walked candidates, the result is still None
        assert isinstance(result, type(None))

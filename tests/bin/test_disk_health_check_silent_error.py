"""Regression test: bin/disk_health_check.py must surface silent errors
via logger.debug at the 3 swallow sites in the ImportError fallback
(count_sessions_today iterdir, sum_pending_uploads_gb iterdir, archive
rglob scan). Each except block must bind the exception to a name and
call logger.debug, not swallow the traceback with a bare
`except SomeError: pass`.

This test verifies:
1. The module compiles without syntax errors
2. logging is imported and a module-level logger is defined
3. The 3 target swallow sites each bind the exception AND call logger.debug
4. None of the target swallow sites is a bare `except ...: pass`
   (no bound name)

Round 360: Surface silent errors in bin/disk_health_check.py.
"""

import ast
import re
from pathlib import Path

SRC_PATH = Path("bin/disk_health_check.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/disk_health_check.py must be syntactically valid Python."""
    _load_source()  # raises on syntax error


def test_logging_imported_and_logger_defined():
    """The module must import logging and define a module-level logger."""
    src = _load_source()
    assert "import logging" in src, "logging must be imported"
    assert "logger = logging.getLogger(__name__)" in src, (
        "module-level logger must be defined as "
        "`logger = logging.getLogger(__name__)`"
    )


def _find_except_in_func(tree, func_name):
    """Return list of (lineno, handler_node) for ExceptHandlers inside func."""
    handlers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    handlers.append((child.lineno, child))
    return handlers


def test_count_sessions_today_except_binds_and_logs():
    """count_sessions_today's iterdir except must bind exception and log at DEBUG."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "count_sessions_today")
    # Take the outer except (the one catching (OSError, FileNotFoundError) at top-level)
    outer = [h for ln, h in handlers
             if h.type is not None
             and "OSError" in ast.unparse(h.type)
             and "FileNotFoundError" in ast.unparse(h.type)]
    assert outer, "count_sessions_today outer except (OSError, FileNotFoundError) not found"
    h = outer[0]
    assert h.name is not None, "iterdir except must bind exception to a name"
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "iterdir except must call logger.debug, not bare `pass`"
    )


def test_sum_pending_uploads_gb_except_binds_and_logs():
    """sum_pending_uploads_gb's iterdir except must bind exception and log at DEBUG."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "sum_pending_uploads_gb")
    outer = [h for ln, h in handlers
             if h.type is not None
             and "OSError" in ast.unparse(h.type)
             and "FileNotFoundError" in ast.unparse(h.type)]
    assert outer, "sum_pending_uploads_gb outer except (OSError, FileNotFoundError) not found"
    h = outer[0]
    assert h.name is not None, "iterdir except must bind exception to a name"
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "iterdir except must call logger.debug, not bare `pass`"
    )


def test_archive_scan_except_binds_and_logs():
    """The archive rglob/scan except block in main must bind exception and log at DEBUG."""
    src = _load_source()
    tree = ast.parse(src)
    # Find the except (OSError, AttributeError) handler whose body mentions
    # archive_dir or "Archive:" print — i.e. the swallow around archive_dir.rglob
    handlers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            ut = ast.unparse(node.type)
            if "OSError" in ut and "AttributeError" in ut:
                body_src = ast.unparse(node)
                if "archive" in body_src.lower() or "Archive" in body_src:
                    handlers.append((node.lineno, node))

    assert handlers, "archive scan except (OSError, AttributeError) not found"
    # Take the last one (the one in main, after can_record_now)
    ln, h = handlers[-1]
    assert h.name is not None, "archive except must bind exception to a name"
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "archive except must call logger.debug, not bare `pass`"
    )


def test_no_bare_pass_in_target_swallow_sites():
    """None of the 3 target swallow sites may remain a bare `except ...: pass`."""
    src = _load_source()
    # Strip docstrings and comments so a docstring example does not match
    src_clean = re.sub(r'"""[\s\S]*?"""', "", src)
    src_clean = re.sub(r"'''[\s\S]*?'''", "", src_clean)
    src_clean = re.sub(r"#[^\n]*", "", src_clean)

    # Find all `except (X, Y):` headers and check the next non-blank line
    # is NOT a bare `pass` (without a bound name).
    lines = src_clean.splitlines()
    bare_pass_sites = []
    for i, line in enumerate(lines):
        if re.match(r"\s*except\s*\(", line):
            # Walk forward to find the first non-blank, non-comment body line
            for j in range(i + 1, min(i + 6, len(lines))):
                stripped = lines[j].strip()
                if not stripped:
                    continue
                if stripped == "pass":
                    bare_pass_sites.append((i + 1, j + 1, line.strip()))
                break
    assert not bare_pass_sites, (
        f"bare `except (...): pass` still present at lines {bare_pass_sites}"
    )

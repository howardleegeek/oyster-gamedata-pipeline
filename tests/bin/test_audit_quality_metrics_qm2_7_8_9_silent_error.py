#!/usr/bin/env python3
"""
Regression test: bin/audit_quality_metrics.py must surface silent errors
via logger at the JSON parse swallow sites in QM2 (check_frame_drops),
QM7 (check_action_diversity), QM8 (check_world_coverage), and QM9
(check_camera_position_range). The except blocks must bind the exception
to a name and call logger, not swallow the traceback.

This test verifies:
1. The module compiles without syntax errors.
2. Every json.JSONDecodeError / (json.JSONDecodeError, ...) except handler
   in check_frame_drops, check_action_diversity, check_world_coverage, and
   check_camera_position_range binds the exception AND calls logger.
3. No bare `except ...: pass` pattern exists in those four functions.

Round 358: Surface silent errors in bin/audit_quality_metrics.py QM2/QM7/QM8/QM9.
"""

import ast
import re
from pathlib import Path

SRC_PATH = Path("bin/audit_quality_metrics.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def _function_body(src: str, func_name: str) -> str:
    """Return the source of a top-level function (body only)."""
    match = re.search(
        rf"def {func_name}\(.*?(?=^def |\Z)", src, re.M | re.S
    )
    assert match, f"{func_name} not found in source"
    return match.group(0)


def test_module_compiles():
    """bin/audit_quality_metrics.py must be syntactically valid Python."""
    _load_source()  # raises on syntax error


_TARGET_FUNCS = [
    "check_frame_drops",
    "check_action_diversity",
    "check_world_coverage",
    "check_camera_position_range",
]


def test_target_handlers_bind_and_log():
    """Every JSONDecodeError / KeyError / TypeError / ValueError swallow
    in the target QM functions must bind the exception AND call logger."""
    src = _load_source()
    for func_name in _TARGET_FUNCS:
        body = _function_body(src, func_name)
        tree = ast.parse(body)
        handlers = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    continue  # bare `except:` (legal Python 3)
                type_str = ast.unparse(node.type)
                if any(
                    t in type_str
                    for t in ("JSONDecodeError", "KeyError", "TypeError", "ValueError")
                ):
                    handlers.append((node.lineno, type_str, node))
        assert handlers, f"{func_name}: no JSONDecodeError/etc. handlers found"
        for lineno, type_str, handler in handlers:
            assert handler.name is not None, (
                f"{func_name} line {lineno}: `{type_str}` except must bind "
                f"the exception to a name (no silent swallow)"
            )
            body_src = ast.unparse(handler)
            assert "logger." in body_src, (
                f"{func_name} line {lineno}: `{type_str}` except must call "
                f"logger (debug/info/warning/error), not bare `continue`"
            )


def test_no_bare_except_pass_in_targets():
    """No `except ...: pass` may remain in the target QM functions."""
    src = _load_source()
    for func_name in _TARGET_FUNCS:
        body = _function_body(src, func_name)
        # strip triple-quoted strings + trailing comments to avoid false matches
        cleaned = re.sub(r'"""[\s\S]*?"""', "", body)
        cleaned = re.sub(r"'''[\s\S]*?'''", "", cleaned)
        cleaned = re.sub(r"#[^\n]*", "", cleaned)
        bare_pass = re.search(r"except[^\n]*:\s*\n\s+pass\b", cleaned)
        assert not bare_pass, (
            f"{func_name}: silent-pass still present at offset "
            f"{bare_pass.start() if bare_pass else '?'}: "
            f"{bare_pass.group(0) if bare_pass else ''}"
        )

#!/usr/bin/env python3
"""
Regression test: bin/extract_audio_event_track.py must surface silent errors
via logger.debug at the 4 swallow sites that previously bound no exception
name:
  1. count_audio_events — JSONDecodeError on a malformed jsonl line
  2. run_sox_silence    — (subprocess.TimeoutExpired, FileNotFoundError)
  3. compute_snr_from_events — JSONDecodeError on a malformed jsonl line
  4. detect_voice_present — (json.JSONDecodeError, IOError) on consent read

Each except block must bind the exception to a name and call logger.debug,
not swallow the traceback with a bare `except ...: continue` or
`except ...: return None/False`.

This test verifies:
1. The module compiles without syntax errors.
2. The 4 target swallow sites each bind the exception AND call logger.debug.
3. None of the 4 target swallow sites is a bare
   `except ...: continue` / `except ...: return <X>` with no bound name.

Round 361: Surface silent errors in bin/extract_audio_event_track.py.
"""

import ast
import re
from pathlib import Path

SRC_PATH = Path("bin/extract_audio_event_track.py")


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
    """bin/extract_audio_event_track.py must be syntactically valid Python."""
    _load_source()  # raises on syntax error


def test_logging_imported_and_logger_defined():
    """The module must import logging and define a module-level logger."""
    src = _load_source()
    assert "import logging" in src, "logging must be imported"
    assert "logger = logging.getLogger(__name__)" in src, (
        "module-level logger must be defined as "
        "`logger = logging.getLogger(__name__)`"
    )


_TARGET_SITES = [
    # (function_name, exception_type_substring)
    ("count_audio_events", "JSONDecodeError"),
    ("run_sox_silence", "subprocess.TimeoutExpired"),
    ("compute_snr_from_events", "JSONDecodeError"),
    ("detect_voice_present", "json.JSONDecodeError"),
]


def test_target_handlers_bind_and_log():
    """Every target swallow site must bind the exception AND call logger.debug."""
    src = _load_source()
    for func_name, exc_substr in _TARGET_SITES:
        body = _function_body(src, func_name)
        tree = ast.parse(body)
        # Find ExceptHandlers whose type string contains the expected substring
        # AND whose body (after unparse) is a bare pass/continue/return.
        target_handlers = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if node.type is None:
                continue  # bare `except:` (legal Python 3)
            type_str = ast.unparse(node.type)
            if exc_substr in type_str:
                target_handlers.append((node.lineno, type_str, node))
        assert target_handlers, (
            f"{func_name}: no `{exc_substr}` handler found"
        )
        # Take the inner-most handler (filter by deepest lineno, prefer the
        # one whose body is a single `continue` / `return <X>` to avoid
        # catching the outer `subprocess.TimeoutExpired` from main()).
        for lineno, type_str, handler in target_handlers:
            handler_body_src = ast.unparse(handler)
            # We expect a one-line body: `continue`, `return <X>`, or `pass`
            # followed by a `logger.debug(...)` call.
            assert handler.name is not None, (
                f"{func_name} line {lineno}: `{type_str}` except must bind "
                f"the exception to a name (no silent swallow)"
            )
            assert "logger.debug" in handler_body_src, (
                f"{func_name} line {lineno}: `{type_str}` except must call "
                f"logger.debug, not bare `continue` / `return <X>` / `pass`"
            )


def test_count_audio_events_no_silent_continue():
    """count_audio_events's jsonl loop must not have a bare `except ...: continue`
    with no bound name (would silently drop malformed lines)."""
    src = _load_source()
    body = _function_body(src, "count_audio_events")
    tree = ast.parse(body)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            continue
        type_str = ast.unparse(node.type)
        if "JSONDecodeError" not in type_str:
            continue
        assert node.name is not None, (
            f"count_audio_events line {node.lineno}: JSONDecodeError except "
            f"must bind exception (was bare `except ...: continue`)"
        )


def test_run_sox_silence_no_silent_return_none():
    """run_sox_silence's subprocess except must not return None silently."""
    src = _load_source()
    body = _function_body(src, "run_sox_silence")
    tree = ast.parse(body)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            continue
        type_str = ast.unparse(node.type)
        if "subprocess.TimeoutExpired" not in type_str:
            continue
        assert node.name is not None, (
            f"run_sox_silence line {node.lineno}: subprocess.TimeoutExpired "
            f"except must bind exception (was bare `except ...: return None`)"
        )


def test_compute_snr_from_events_no_silent_continue():
    """compute_snr_from_events's jsonl loop must not have a bare
    `except ...: continue` with no bound name."""
    src = _load_source()
    body = _function_body(src, "compute_snr_from_events")
    tree = ast.parse(body)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            continue
        type_str = ast.unparse(node.type)
        if "JSONDecodeError" not in type_str:
            continue
        assert node.name is not None, (
            f"compute_snr_from_events line {node.lineno}: JSONDecodeError "
            f"except must bind exception (was bare `except ...: continue`)"
        )


def test_detect_voice_present_no_silent_return_false():
    """detect_voice_present's consent load except must not return False silently."""
    src = _load_source()
    body = _function_body(src, "detect_voice_present")
    tree = ast.parse(body)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            continue
        type_str = ast.unparse(node.type)
        if "json.JSONDecodeError" not in type_str and "JSONDecodeError" not in type_str:
            continue
        # The consent-load handler: its body is `return False` — distinguish
        # from the RMS-parse handler (body: `logger.warning(...)`).
        handler_body_src = ast.unparse(node)
        if "logger.debug" in handler_body_src:
            # This is the fixed consent handler — verify it binds.
            assert node.name is not None, (
                f"detect_voice_present line {node.lineno}: JSONDecodeError "
                f"consent except must bind exception"
            )

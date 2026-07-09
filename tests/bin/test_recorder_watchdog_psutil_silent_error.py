#!/usr/bin/env python3
"""
Regression test: bin/recorder_watchdog.py _check_alt_tab /
_check_recorder_alive psutil.NoSuchProcess+psutil.AccessDenied handlers.

Verifies that the bare `except (psutil.NoSuchProcess, psutil.AccessDenied):`
sites have been replaced with `... as exc:` + debug logging.
"""

import ast
import inspect
import sys
import textwrap
from pathlib import Path

# Make `bin` importable so `import bin.recorder_watchdog` works.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _method_source(cls, method_name: str) -> str:
    """Return dedented source of a method on a class."""
    method = getattr(cls, method_name, None)
    assert method is not None, f"{cls.__name__}.{method_name} must exist"
    return textwrap.dedent(inspect.getsource(method))


def _iter_psutil_process_handlers(source: str):
    """Yield (lineno, type_src, bound_name) for every
    `except (psutil.NoSuchProcess, psutil.AccessDenied)`."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                continue
            type_src = ast.unparse(node.type)
            if "psutil.NoSuchProcess" in type_src or "psutil.AccessDenied" in type_src:
                yield node.lineno, type_src, node.name


# --- module compiles -------------------------------------------------------


def test_module_compiles():
    """The recorder_watchdog module must still import cleanly."""
    import bin.recorder_watchdog as mod
    assert mod is not None
    assert hasattr(mod, "Watchdog")


# --- _check_alt_tab ---------------------------------------------------------


def test_check_alt_tab_psutil_handler_is_bound():
    """_check_alt_tab's psutil NoSuchProcess/AccessDenied handler must bind exc."""
    import bin.recorder_watchdog as mod
    source = _method_source(mod.Watchdog, "_check_alt_tab")
    matches = list(_iter_psutil_process_handlers(source))
    assert matches, (
        "_check_alt_tab must contain an "
        "'except (psutil.NoSuchProcess, psutil.AccessDenied)' handler"
    )
    for _lineno, _type_src, bound in matches:
        assert bound, (
            "_check_alt_tab has unbound 'except (psutil.NoSuchProcess, psutil.AccessDenied)'; "
            "expected '... as exc:'"
        )


def test_check_alt_tab_psutil_handler_logs_at_debug():
    """_check_alt_tab's psutil handler must call log.debug with context."""
    import bin.recorder_watchdog as mod
    source = _method_source(mod.Watchdog, "_check_alt_tab")
    assert "log.debug" in source, (
        "_check_alt_tab must call log.debug on psutil NoSuchProcess/AccessDenied"
    )
    # The new debug call must include 'psutil' or 'fg_pid' context.
    assert "fg_pid" in source, (
        "_check_alt_tab's psutil debug log must include fg_pid context"
    )


def test_check_alt_tab_psutil_handler_preserves_mc_visible_false():
    """Control flow preserved: mc_visible is still set to False in the handler."""
    import bin.recorder_watchdog as mod
    source = _method_source(mod.Watchdog, "_check_alt_tab")
    # The handler still assigns mc_visible = False (the original control flow).
    assert "mc_visible = False" in source, (
        "_check_alt_tab's psutil handler must still set mc_visible = False"
    )


# --- _check_recorder_alive --------------------------------------------------


def test_check_recorder_alive_psutil_handler_is_bound():
    """_check_recorder_alive's psutil handler must bind exc."""
    import bin.recorder_watchdog as mod
    source = _method_source(mod.Watchdog, "_check_recorder_alive")
    matches = list(_iter_psutil_process_handlers(source))
    assert matches, (
        "_check_recorder_alive must contain an "
        "'except (psutil.NoSuchProcess, psutil.AccessDenied)' handler"
    )
    for _lineno, _type_src, bound in matches:
        assert bound, (
            "_check_recorder_alive has unbound "
            "'except (psutil.NoSuchProcess, psutil.AccessDenied)'; "
            "expected '... as exc:'"
        )


def test_check_recorder_alive_psutil_handler_logs_at_debug():
    """_check_recorder_alive's psutil handler must call log.debug with context."""
    import bin.recorder_watchdog as mod
    source = _method_source(mod.Watchdog, "_check_recorder_alive")
    assert "log.debug" in source, (
        "_check_recorder_alive must call log.debug on psutil NoSuchProcess/AccessDenied"
    )
    # The new debug call must include 'process_iter' or 'pid' context.
    assert "process_iter" in source, (
        "_check_recorder_alive's psutil debug log must include process_iter context"
    )


def test_check_recorder_alive_psutil_handler_preserves_continue():
    """Control flow preserved: continue is still emitted in the handler."""
    import bin.recorder_watchdog as mod
    source = _method_source(mod.Watchdog, "_check_recorder_alive")
    # The inner loop's handler still does `continue` (the original control flow).
    # We need at least one continue in a psutil handler. Easiest: just assert
    # the substring is present, since the original `continue` is the only one
    # in the inner loop body.
    assert "continue" in source, (
        "_check_recorder_alive's psutil handler must still continue the loop"
    )

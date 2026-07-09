#!/usr/bin/env python3
"""
Regression test: bin/recorder_watchdog.py sample_ui_zone / _check_alt_tab /
_check_recorder_alive ImportError handlers.

Verifies that the bare `except ImportError:` sites have been replaced
with `except ImportError as exc:` + debug logging.
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


def _source_of(name: str) -> str:
    """Return dedented source of a top-level function in recorder_watchdog."""
    import bin.recorder_watchdog as mod
    obj = getattr(mod, name, None)
    assert obj is not None, f"{name} not found in bin.recorder_watchdog"
    return textwrap.dedent(inspect.getsource(obj))


def _method_source(cls, method_name: str) -> str:
    """Return dedented source of a method on a class."""
    method = getattr(cls, method_name, None)
    assert method is not None, f"{cls.__name__}.{method_name} must exist"
    return textwrap.dedent(inspect.getsource(method))


def _iter_import_error_handlers(source: str):
    """Yield (lineno, type_src, bound_name) for every `except ImportError`."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                continue
            type_src = ast.unparse(node.type)
            if "ImportError" in type_src:
                yield node.lineno, type_src, node.name


# --- module compiles -------------------------------------------------------


def test_module_compiles():
    """The recorder_watchdog module must still import cleanly."""
    import bin.recorder_watchdog as mod
    assert mod is not None
    assert hasattr(mod, "Watchdog")
    assert hasattr(mod, "sample_ui_zone")


# --- sample_ui_zone ---------------------------------------------------------


def test_sample_ui_zone_import_error_is_bound():
    """sample_ui_zone's ImportError handler must bind the exception to a name."""
    source = _source_of("sample_ui_zone")
    matches = list(_iter_import_error_handlers(source))
    assert matches, "sample_ui_zone must contain an 'except ImportError' handler"
    for _lineno, _type_src, bound in matches:
        assert bound, (
            "sample_ui_zone has unbound 'except ImportError'; "
            "expected 'except ImportError as exc:'"
        )


def test_sample_ui_zone_import_error_logs_at_debug():
    """sample_ui_zone's ImportError handler must call log.debug with context."""
    source = _source_of("sample_ui_zone")
    assert "log.debug" in source, "sample_ui_zone must call log.debug on ImportError"
    assert "exc" in source, "sample_ui_zone must reference the bound exception variable"


# --- _check_alt_tab ---------------------------------------------------------


def test_check_alt_tab_import_error_is_bound():
    """_check_alt_tab's ImportError handler must bind the exception to a name."""
    import bin.recorder_watchdog as mod
    source = _method_source(mod.Watchdog, "_check_alt_tab")
    matches = list(_iter_import_error_handlers(source))
    assert matches, "_check_alt_tab must contain an 'except ImportError' handler"
    for _lineno, _type_src, bound in matches:
        assert bound, (
            "_check_alt_tab has unbound 'except ImportError'; "
            "expected 'except ImportError as exc:'"
        )


def test_check_alt_tab_import_error_logs_at_debug():
    """_check_alt_tab's ImportError handler must call log.debug with context."""
    import bin.recorder_watchdog as mod
    source = _method_source(mod.Watchdog, "_check_alt_tab")
    assert "log.debug" in source, (
        "_check_alt_tab must call log.debug on ImportError (psutil fallback)"
    )
    assert "exc" in source, "_check_alt_tab must reference the bound exception variable"


# --- _check_recorder_alive --------------------------------------------------


def test_check_recorder_alive_import_error_is_bound():
    """_check_recorder_alive's ImportError handler must bind the exception."""
    import bin.recorder_watchdog as mod
    source = _method_source(mod.Watchdog, "_check_recorder_alive")
    matches = list(_iter_import_error_handlers(source))
    assert matches, "_check_recorder_alive must contain an 'except ImportError' handler"
    for _lineno, _type_src, bound in matches:
        assert bound, (
            "_check_recorder_alive has unbound 'except ImportError'; "
            "expected 'except ImportError as exc:'"
        )


def test_check_recorder_alive_import_error_logs_at_debug():
    """_check_recorder_alive's ImportError handler must call log.debug."""
    import bin.recorder_watchdog as mod
    source = _method_source(mod.Watchdog, "_check_recorder_alive")
    assert "log.debug" in source, (
        "_check_recorder_alive must call log.debug on ImportError (psutil missing)"
    )
    assert "exc" in source, (
        "_check_recorder_alive must reference the bound exception variable"
    )


# --- no bare except ImportError anywhere in the file -----------------------


def test_no_bare_except_importerror_in_file():
    """AST scan: no `except ImportError:` (unbound) anywhere in the source file."""
    src_path = _ROOT / "bin" / "recorder_watchdog.py"
    source = src_path.read_text()
    tree = ast.parse(source)

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                continue
            type_src = ast.unparse(node.type)
            if "ImportError" in type_src and node.name is None:
                offenders.append((node.lineno, type_src))

    assert not offenders, (
        "Unbound 'except ImportError' handlers remain: " + repr(offenders)
    )

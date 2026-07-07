#!/usr/bin/env python3
"""
Regression test: bin/obs_websocket_smoke.py must surface silent errors
at the two OSError swallow sites
(stop_obs shutil.rmtree cleanup, wait_for_websocket connect retry loop)
via logger.debug, not bare `pass` / silent retry.

This test verifies:
1. Module compiles without syntax errors.
2. logging module is imported and a module-level logger is defined.
3. The OSError handler in stop_obs binds the exception to a name (`exc`).
4. The OSError handler in wait_for_websocket binds the exception to a name (`exc`).
5. Both handler bodies call `logger.debug(...)` (not just `pass` or no logging).
6. There is no bare `except OSError: pass` in the module.
7. Control flow is preserved: rmtree is still called, connect retry loop still sleeps.

Round: Surface silent errors in bin/obs_websocket_smoke.py.
"""

import ast
import logging
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TARGET_PATH = REPO_ROOT / "bin" / "obs_websocket_smoke.py"


def _load_module():
    """Import bin.obs_websocket_smoke with a clean sys.modules state."""
    sys.modules.pop("bin.obs_websocket_smoke", None)
    from bin import obs_websocket_smoke

    return obs_websocket_smoke


def test_module_compiles():
    """Module must compile without syntax errors."""
    source = TARGET_PATH.read_text()
    try:
        compile(source, str(TARGET_PATH), "exec")
    except SyntaxError as e:
        raise AssertionError(f"Syntax error in obs_websocket_smoke.py: {e}")


def test_logging_imported():
    """logging module must be imported."""
    source = TARGET_PATH.read_text()
    assert "import logging" in source, "logging import missing in obs_websocket_smoke.py"


def test_logger_defined():
    """A module-level logger must be defined as logging.getLogger(__name__)."""
    source = TARGET_PATH.read_text()
    assert re.search(
        r"^logger\s*=\s*logging\.getLogger\(__name__\)\s*$",
        source,
        re.MULTILINE,
    ), "Module-level logger = logging.getLogger(__name__) missing"


def test_logger_attribute_is_logger():
    """The module must expose a top-level `logger` attribute that is a logging.Logger."""
    mod = _load_module()
    assert hasattr(mod, "logger"), "module-level logger missing"
    assert isinstance(mod.logger, logging.Logger), "logger is not a logging.Logger"
    assert mod.logger.name == "bin.obs_websocket_smoke", (
        f"logger name unexpected: {mod.logger.name!r}"
    )


def test_stop_obs_binds_exception_in_rmtree_handler():
    """stop_obs's shutil.rmtree OSError handler must bind OSError to a name."""
    tree = _load_tree()
    found = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "stop_obs":
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    type_text = ast.unparse(child.type) if child.type else ""
                    if type_text == "OSError":
                        if child.name is not None:
                            found = True
    assert found, (
        "stop_obs's OSError handler must bind the exception to a name (e.g. `as exc`)"
    )


def test_wait_for_websocket_binds_exception_in_connect_handler():
    """wait_for_websocket's websockets.connect OSError handler must bind OSError to a name."""
    tree = _load_tree()
    found = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "wait_for_websocket":
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    type_text = ast.unparse(child.type) if child.type else ""
                    if type_text == "OSError":
                        if child.name is not None:
                            found = True
    assert found, (
        "wait_for_websocket's OSError handler must bind the exception to a name (e.g. `as exc`)"
    )


def test_stop_oserror_handler_calls_logger_debug():
    """stop_obs's OSError handler body must call logger.debug(...) (not bare pass)."""
    source = _read_function("stop_obs")
    pattern = r"except\s+OSError\s+as\s+\w+:\s*\n\s+logger\.debug\("
    assert re.search(pattern, source), (
        "stop_obs's OSError handler body must call logger.debug(...)"
    )


def test_wait_for_websocket_oserror_handler_calls_logger_debug():
    """wait_for_websocket's OSError handler body must call logger.debug(...) (not silent)."""
    source = _read_function("wait_for_websocket")
    pattern = r"except\s+OSError\s+as\s+\w+:\s*\n\s+logger\.debug\("
    assert re.search(pattern, source), (
        "wait_for_websocket's OSError handler body must call logger.debug(...)"
    )


def test_no_bare_except_oserror_pass_in_module():
    """No bare `except OSError: pass` anywhere in the module."""
    source = TARGET_PATH.read_text()
    pattern = r"except\s+OSError\s*:\s*\n\s+pass"
    assert not re.search(pattern, source), (
        "Bare `except OSError: pass` swallow still present in obs_websocket_smoke.py"
    )


def test_control_flow_preserved_rmtree_still_called():
    """Sanity: shutil.rmtree call must still be present in stop_obs (cleanup not removed)."""
    source = _read_function("stop_obs")
    assert "shutil.rmtree" in source, "shutil.rmtree call missing (cleanup removed?)"
    assert "self.temp_dir = None" in source, "temp_dir reset missing"


def test_control_flow_preserved_retry_loop_still_sleeps():
    """Sanity: wait_for_websocket's retry loop must still sleep + return False on timeout."""
    source = _read_function("wait_for_websocket")
    assert "asyncio.sleep(1)" in source, "retry loop sleep missing (control flow broken?)"
    assert "return False" in source, "timeout return False missing"


# --- helpers ---


def _load_tree():
    """Return AST tree for the target module."""
    source = TARGET_PATH.read_text()
    return ast.parse(source)


def _read_function(name: str) -> str:
    """Return the source text of the named top-level function/method."""
    source = TARGET_PATH.read_text()
    match = re.search(
        rf"def\s+{re.escape(name)}\(.*?\n(?=def\s|\Z)",
        source,
        re.DOTALL,
    )
    assert match is not None, f"{name} not found"
    return match.group(0)


def _read_source() -> str:
    return TARGET_PATH.read_text()

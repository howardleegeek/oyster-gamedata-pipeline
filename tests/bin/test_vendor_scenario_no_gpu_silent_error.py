#!/usr/bin/env python3
"""
Regression test: bin/vendor_scenario_no_gpu.py must surface silent errors
at the single swallow site (subprocess to system_profiler in _check_gpu)
via logger.debug, not bare `pass`.

This test verifies:
1. Module compiles without syntax errors.
2. logging module is imported and a module-level logger is defined.
3. The OSError handler in _check_gpu binds the exception to a name (`exc`).
4. The handler body calls `logger.debug(...)` (not just `pass`).
5. There is no bare `except OSError: pass` in the module.

Round: Surface silent errors in bin/vendor_scenario_no_gpu.py.
"""

import ast
import logging
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TARGET_PATH = REPO_ROOT / "bin" / "vendor_scenario_no_gpu.py"


def _load_module():
    """Import bin.vendor_scenario_no_gpu with a clean sys.modules state."""
    sys.modules.pop("bin.vendor_scenario_no_gpu", None)
    from bin import vendor_scenario_no_gpu

    return vendor_scenario_no_gpu


def test_module_compiles():
    """Module must compile without syntax errors."""
    source = TARGET_PATH.read_text()
    try:
        compile(source, str(TARGET_PATH), "exec")
    except SyntaxError as e:
        raise AssertionError(f"Syntax error in vendor_scenario_no_gpu.py: {e}")


def test_logging_imported():
    """logging module must be imported."""
    source = TARGET_PATH.read_text()
    assert "import logging" in source, "logging import missing in vendor_scenario_no_gpu.py"


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
    assert mod.logger.name == "bin.vendor_scenario_no_gpu", (
        f"logger name unexpected: {mod.logger.name!r}"
    )


def test_check_gpu_binds_exception_in_system_profiler_handler():
    """_check_gpu's system_profiler subprocess handler must bind OSError to a name."""
    source, tree = _load_tree_safe()
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_check_gpu":
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    type_text = ast.unparse(child.type) if child.type else ""
                    if type_text == "OSError":
                        if child.name is not None:
                            found = True
    assert found, (
        "_check_gpu's OSError handler must bind the exception "
        "(e.g., 'except OSError as exc:')"
    )


def test_check_gpu_system_profiler_handler_calls_logger_debug():
    """_check_gpu's system_profiler handler body must call logger.debug, not just `pass`."""
    source = TARGET_PATH.read_text()
    # Find the _check_gpu function block
    match = re.search(
        r"def\s+_check_gpu\(.*?\n(?=def\s|\Z)",
        source,
        re.DOTALL,
    )
    assert match is not None, "_check_gpu function not found"
    func_src = match.group(0)
    # Must have logger.debug within the OSError handler
    handler_match = re.search(
        r"except\s+OSError\s+as\s+\w+:\s*\n\s+logger\.debug\(",
        func_src,
    )
    assert handler_match is not None, (
        "_check_gpu's OSError handler body must call logger.debug(...)"
    )


def test_no_bare_except_oserror_pass_in_module():
    """No bare `except OSError: pass` anywhere in the module."""
    source = TARGET_PATH.read_text()
    pattern = r"except\s+OSError\s*:\s*\n\s+pass"
    assert not re.search(pattern, source), (
        "Bare `except OSError: pass` swallow still present in vendor_scenario_no_gpu.py"
    )


def test_control_flow_preserved_subprocess_try_still_runs():
    """Sanity: the try block surrounding the subprocess.run is still present."""
    source = TARGET_PATH.read_text()
    assert "subprocess.run" in source, "subprocess.run call missing"
    assert "system_profiler" in source, "system_profiler invocation missing"
    assert "SPDisplaysDataType" in source, "SPDisplaysDataType argument missing"
    # The return statement after the swallow should still be reachable
    assert "return info" in source, "return info statement missing (control flow broken?)"


def _load_tree_safe():
    """Return (source, AST) for the target module, parsing safely."""
    source = TARGET_PATH.read_text()
    tree = ast.parse(source)
    return source, tree

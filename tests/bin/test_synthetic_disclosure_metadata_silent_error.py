"""
Regression tests for silent error swallows in bin/synthetic_disclosure_metadata.py.

These tests verify that error conditions are logged rather than silently swallowed.

Round 395: Surface silent error in synthetic_disclosure_metadata.py _load_yaml()
ImportError handler.
"""

import ast
import logging
import re
import sys
from pathlib import Path

SRC_PATH = Path("bin/synthetic_disclosure_metadata.py")


def _load_tree():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src, ast.parse(src)


def test_load_yaml_except_binds_exception():
    """The _load_yaml ImportError handler must bind the exception to a name."""
    _src, tree = _load_tree()
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_load_yaml":
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler) and child.name is not None:
                    assert child.type is not None
                    if isinstance(child.type, ast.Name):
                        assert child.type.id == "ImportError", (
                            f"Expected ImportError, got {child.type.id}"
                        )
                    found = True
    assert found, (
        "_load_yaml() except block must bind exception "
        "(e.g., 'except ImportError as exc:')"
    )


def test_load_yaml_except_logs_error():
    """The _load_yaml ImportError handler must call logger.debug."""
    src = SRC_PATH.read_text()
    # The except ImportError handler should call logger.debug (comments may precede it)
    pattern = r"except\s+ImportError\s+as\s+\w+:.*?logger\.debug\("
    match = re.search(pattern, src, re.DOTALL)
    assert match is not None, (
        "_load_yaml() ImportError handler must call logger.debug with the error"
    )


def test_load_yaml_does_not_silently_swallow():
    """The _load_yaml ImportError handler must not be a bare `except ImportError: return None`."""
    src = SRC_PATH.read_text()
    pattern = (
        r"except\s+ImportError:\s*\n"
        r"\s+return\s+None\s*$"
    )
    match = re.search(pattern, src, re.MULTILINE)
    assert match is None, (
        "_load_yaml() ImportError handler must bind exception and log — "
        "bare `except ImportError: return None` is the silent-swallow anti-pattern"
    )


def test_load_yaml_control_flow_preserved():
    """The _load_yaml handler must still return None to preserve caller contract."""
    src = SRC_PATH.read_text()
    pattern = (
        r"except\s+ImportError\s+as\s+\w+:[^\n]*\n"
        r"(?:\s+[^\n]*\n)*?"
        r"\s+return\s+None"
    )
    match = re.search(pattern, src)
    assert match is not None, (
        "_load_yaml() handler must still return None after logging — "
        "callers depend on the None contract"
    )


def test_module_has_logging_import():
    """Verify synthetic_disclosure_metadata.py imports logging."""
    src = SRC_PATH.read_text()
    assert "import logging" in src, "Module must import logging"


def test_module_has_logger_definition():
    """Verify synthetic_disclosure_metadata.py defines a module-level logger."""
    src = SRC_PATH.read_text()
    assert "logger = logging.getLogger(__name__)" in src, (
        "Module must define `logger = logging.getLogger(__name__)`"
    )


def test_load_yaml_logs_at_debug_level():
    """The logger.debug call must use lazy %s formatting (not eager f-string)."""
    src = SRC_PATH.read_text()
    pattern = r'logger\.debug\(\s*"[^"]*%s[^"]*"\s*,\s*path\s*,\s*exc\s*\)'
    match = re.search(pattern, src)
    assert match is not None, (
        "logger.debug must use lazy %s formatting with (path, exc) args, "
        "not eager f-string or .format()"
    )


def test_load_yaml_logger_debug_emits():
    """Verify that calling _load_yaml when yaml is missing emits a debug log."""
    if "bin.synthetic_disclosure_metadata" in sys.modules:
        del sys.modules["bin.synthetic_disclosure_metadata"]
    import bin.synthetic_disclosure_metadata as mod  # noqa: E402

    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("PyYAML not installed (test stub)")
        return real_import(name, *args, **kwargs)

    cap = _CaptureHandler()
    mod.logger.addHandler(cap)
    mod.logger.setLevel(logging.DEBUG)
    builtins.__import__ = fake_import
    try:
        result = mod._load_yaml(Path("/tmp/nonexistent.yaml"))
    finally:
        builtins.__import__ = real_import
        mod.logger.removeHandler(cap)

    assert result is None, "must return None to preserve caller contract"
    pyyaml_records = [r for r in cap.records if "PyYAML" in r.getMessage()]
    assert len(pyyaml_records) == 1, (
        f"Expected exactly one DEBUG log mentioning PyYAML, got "
        f"{[r.getMessage() for r in cap.records]}"
    )
    assert pyyaml_records[0].levelno == logging.DEBUG
    assert "/tmp/nonexistent.yaml" in pyyaml_records[0].getMessage()


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)

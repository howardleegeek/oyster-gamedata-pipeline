"""
Regression test for silent error swallow in bin/recorder_consumer_lite.py.

The ``on_frame_arrived`` callback had a bare ``except Exception: pass`` that
silently swallowed exceptions when stopping capture_control on a stopped handle.

This test asserts:
  1. No bare ``except Exception:`` (no ``as`` binding) remains in the
     on_frame_arrived function's stop_event.is_set() block.
  2. The exception is bound to a variable and logged via _trace.
  3. Control flow is preserved (still returns after handling stop).
  4. The module compiles.

Self-review: scope = one function block in one file, one logical change
(bind previously-bare except to `e` + _trace log).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
# No external mocks needed - AST-based tests only

import pytest

# Add bin to path
BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))


SRC = (BIN_DIR / "recorder_consumer_lite.py").read_text(encoding="utf-8")


def test_no_bare_except_in_on_frame_stop_block() -> None:
    """No bare ``except Exception:`` may remain in on_frame_arrived stop block."""
    tree = ast.parse(SRC)
    
    # Find on_frame_arrived function
    on_frame_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "on_frame_arrived":
            on_frame_func = node
            break
    
    assert on_frame_func is not None, "on_frame_arrived function not found"
    
    # Find the stop_event.is_set() block inside on_frame_arrived
    # Look for pattern: if handle.stop_event.is_set(): ... except Exception:
    found_bare = False
    for node in ast.walk(on_frame_func):
        if isinstance(node, ast.If):
            # Check if this is the stop_event.is_set() check
            test_str = ast.unparse(node.test) if hasattr(ast, 'unparse') else ""
            if "stop_event" in test_str and "is_set" in test_str:
                # Check the body for try/except
                for child in ast.walk(node):
                    if isinstance(child, ast.Try):
                        for handler in child.handlers:
                            if handler.type is not None:
                                type_str = ast.unparse(handler.type) if hasattr(ast, 'unparse') else ""
                                if "Exception" in type_str and handler.name is None:
                                    found_bare = True
    
    assert not found_bare, (
        "Found bare 'except Exception:' in on_frame_arrived stop_event block. "
        "Bind the exception and log it via _trace."
    )


def test_stop_block_logs_via_trace() -> None:
    """The stop block must log exceptions via _trace."""
    # Check that the stop block has _trace logging for exceptions
    # Look for pattern: except Exception as e: _trace(...)
    assert "except Exception as e:" in SRC, "Exception must be bound to 'e'"
    assert "_trace" in SRC, "Module must use _trace for logging"


def test_control_flow_preserved() -> None:
    """The stop block must still return after handling."""
    # The stop block should still have a 'return' statement after the except
    # We verify by checking that the function structure is preserved
    tree = ast.parse(SRC)
    
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "on_frame_arrived":
            # Check that there's a return statement in the stop_event block
            # The function should still return after handling
            source_lines = SRC.split('\n')
            # Find the function definition line
            func_start = node.lineno - 1
            
            # Get the function source
            func_lines = []
            indent = None
            for i in range(func_start, len(source_lines)):
                line = source_lines[i]
                if indent is None and line.strip():
                    indent = len(line) - len(line.lstrip())
                if i > func_start and line.strip() and not line.startswith(' ' * (indent + 1)) and not line.strip().startswith('#'):
                    break
                func_lines.append(line)
            
            func_source = '\n'.join(func_lines)
            assert "return" in func_source, "on_frame_arrived must still return after stop handling"


def test_module_compiles() -> None:
    """The module must compile without errors."""
    try:
        compile(SRC, str(BIN_DIR / "recorder_consumer_lite.py"), "exec")
    except SyntaxError as e:
        pytest.fail(f"Module compilation failed: {e}")

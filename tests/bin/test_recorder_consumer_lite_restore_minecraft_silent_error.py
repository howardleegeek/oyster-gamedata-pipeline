"""Regression test: _restore_minecraft_window_for_capture() logs int(hwnd) failures
instead of silently swallowing the exception.

Round 334: surface silent error in _restore_minecraft_window_for_capture()
when int(rect.get('hwnd') or 0) raises (e.g. hwnd is a non-numeric string or
rect.get throws a KeyError-style error in a subclass). Bound bare
`except Exception: hwnd = 0` to a named exception + _trace() call so the
operator gets a non-fatal log line in ~/OysterRecorder.log.
Control flow preserved: hwnd still defaults to 0 and the function still
returns early via the `if hwnd <= 0: return` guard.
"""
import ast


def test_restore_minecraft_binds_exception_in_hwnd_int_conversion():
    """Verify _restore_minecraft_window_for_capture binds Exception and calls _trace."""
    source_path = "bin/recorder_consumer_lite.py"
    with open(source_path) as f:
        source = f.read()

    tree = ast.parse(source)

    # Find the _restore_minecraft_window_for_capture function
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_restore_minecraft_window_for_capture":
            fn = node
            break

    assert fn is not None, "_restore_minecraft_window_for_capture function not found"

    # Find the first try block (int(hwnd) conversion)
    int_hwnd_try = None
    for node in ast.walk(fn):
        if isinstance(node, ast.Try):
            # Check that try body contains int(rect.get("hwnd") or 0) call
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and stmt.targets[0].id == "hwnd"
                    and isinstance(stmt.value, ast.Call)
                ):
                    call = stmt.value
                    if (
                        isinstance(call.func, ast.Name)
                        and call.func.id == "int"
                    ):
                        int_hwnd_try = node
                        break
            if int_hwnd_try:
                break

    assert int_hwnd_try is not None, "try block with int(hwnd) assignment not found"

    assert len(int_hwnd_try.handlers) == 1, "expected exactly one except handler"

    handler = int_hwnd_try.handlers[0]
    # Check exception is bound (not bare `except Exception:`)
    assert handler.type is not None, "except handler must have a type"
    assert isinstance(handler.type, ast.Name), "exception type should be Name"
    assert handler.type.id == "Exception", "should catch Exception"
    assert handler.name is not None, "exception must be bound to a variable (no bare except)"
    assert handler.name != "e" or handler.name.startswith("_"), (
        "exception name should be descriptive (e.g. _restore_hwnd_exc)"
    )

    # Check except body contains _trace call (not bare pass)
    assert len(handler.body) >= 2, "except body must have _trace + hwnd = 0"
    trace_calls = [
        n for n in ast.walk(handler)
        if isinstance(n, ast.Call)
        and hasattr(n.func, "id")
        and n.func.id == "_trace"
    ]
    assert len(trace_calls) >= 1, "except block must call _trace with exception info"

    # Verify hwnd = 0 still present (control flow preserved)
    assigns_hwnd_zero = [
        n for n in ast.walk(handler)
        if isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "hwnd"
        and isinstance(n.value, ast.Constant)
        and n.value.value == 0
    ]
    assert len(assigns_hwnd_zero) >= 1, "hwnd = 0 must remain in except block"


def test_module_compiles():
    """Verify the module source is syntactically valid."""
    source_path = "bin/recorder_consumer_lite.py"
    with open(source_path) as f:
        source = f.read()
    compile(source, source_path, "exec")


if __name__ == "__main__":
    test_restore_minecraft_binds_exception_in_hwnd_int_conversion()
    test_module_compiles()
    print("All tests passed!")

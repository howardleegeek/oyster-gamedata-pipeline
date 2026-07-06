"""Regression test: _on_close() logs upload failures instead of silent swallow."""
import ast
import sys


def test_on_close_upload_logs_debug():
    """Verify _on_close binds exception and logs when _upload_log_remote fails."""
    source_path = "bin/recorder_consumer_lite.py"
    with open(source_path) as f:
        source = f.read()

    tree = ast.parse(source)

    # Find the _on_close method
    on_close = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_on_close":
            on_close = node
            break

    assert on_close is not None, "_on_close function not found"

    # Find the try/except block with _upload_log_remote call
    upload_try_except = None
    for node in ast.walk(on_close):
        if isinstance(node, ast.Try):
            # Check if the try body contains _upload_log_remote call
            for stmt in node.body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                    if hasattr(stmt.value.func, 'id') and stmt.value.func.id == "_upload_log_remote":
                        upload_try_except = node
                        break
            if upload_try_except:
                break

    assert upload_try_except is not None, "try block with _upload_log_remote not found in _on_close"
    assert len(upload_try_except.handlers) == 1, "expected exactly one except handler"

    handler = upload_try_except.handlers[0]
    # Check exception is bound
    assert handler.type is not None, "except handler must bind exception"
    assert isinstance(handler.type, ast.Name), "exception type should be Name (Exception)"
    assert handler.type.id == "Exception", "should catch Exception"
    assert handler.name is not None, "exception must be bound to a variable"

    # Check _trace is called in the except block (replacing bare pass)
    assert len(handler.body) > 0, "except block must not be empty (no bare pass)"
    # Should contain a call to _trace with the exception
    trace_calls = [n for n in ast.walk(handler) if isinstance(n, ast.Call) and hasattr(n.func, 'id') and n.func.id == '_trace']
    assert len(trace_calls) >= 1, "except block must call _trace with exception info"

    # Verify module compiles
    compile(source, source_path, 'exec')


def test_module_has_trace():
    """Verify module has _trace function for logging."""
    source_path = "bin/recorder_consumer_lite.py"
    with open(source_path) as f:
        source = f.read()

    tree = ast.parse(source)

    trace_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_trace":
            trace_func = node
            break

    assert trace_func is not None, "_trace function must exist"


if __name__ == "__main__":
    test_on_close_upload_logs_debug()
    test_module_has_trace()
    print("All tests passed!")

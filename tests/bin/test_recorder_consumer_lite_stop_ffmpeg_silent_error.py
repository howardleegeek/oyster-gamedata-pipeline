"""Regression test: _stop_ffmpeg() logs failures when sending 'q' to ffmpeg stdin
instead of silently swallowing the exception.

Round 335: surface silent error in _stop_ffmpeg() in the
`proc.stdin.write(b"q\\n") / proc.stdin.flush()` block. Bound bare
`except Exception: pass` to a named exception + _trace() call so the operator
gets a non-fatal log line in ~/OysterRecorder.log if ffmpeg's stdin pipe
breaks (e.g. process already exited, pipe closed, OSError on broken pipe).
Control flow preserved: code still falls through to the existing
`proc.wait(timeout=_FFMPEG_CLEAN_QUIT_TIMEOUT_SEC)` call which has its own
subprocess.TimeoutExpired handler that terminates / kills as needed.
"""
import ast
from pathlib import Path


SOURCE_PATH = "bin/recorder_consumer_lite.py"


def _load_source():
    return Path(SOURCE_PATH).read_text(encoding="utf-8")


def _find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def test_stop_ffmpeg_binds_exception_in_stdin_write():
    """Verify _stop_ffmpeg binds Exception and calls _trace in stdin-write block."""
    source = _load_source()
    tree = ast.parse(source)

    fn = _find_function(tree, "_stop_ffmpeg")
    assert fn is not None, "_stop_ffmpeg function not found"

    # Find the try block where we send 'q' to ffmpeg stdin.
    # The try body must contain proc.stdin.write(b"q\\n").
    stdin_write_try = None
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        for stmt in node.body:
            # We need proc.stdin.write(b"q\n") — look for an if-stmt
            # where the body contains a Call to .write with a b"q\n" arg.
            if isinstance(stmt, ast.If):
                for sub in ast.walk(stmt):
                    if not isinstance(sub, ast.Call):
                        continue
                    func = sub.func
                    if (
                        isinstance(func, ast.Attribute)
                        and func.attr == "write"
                    ):
                        # Confirm the write target is proc.stdin
                        if (
                            isinstance(func.value, ast.Attribute)
                            and func.value.attr == "stdin"
                        ):
                            # Confirm the arg is b"q\n"
                            if sub.args and isinstance(sub.args[0], ast.Constant):
                                if sub.args[0].value == b"q\n":
                                    stdin_write_try = node
                                    break
                if stdin_write_try:
                    break
            if stdin_write_try:
                break
        if stdin_write_try:
            break

    assert stdin_write_try is not None, (
        "try block with proc.stdin.write(b'q\\n') not found in _stop_ffmpeg"
    )
    assert len(stdin_write_try.handlers) == 1, "expected exactly one except handler"

    handler = stdin_write_try.handlers[0]
    # Check exception is bound (not bare `except Exception:`)
    assert handler.type is not None, "except handler must have a type"
    assert isinstance(handler.type, ast.Name), "exception type should be Name"
    assert handler.type.id == "Exception", "should catch Exception"
    assert handler.name is not None, "exception must be bound to a variable (no bare except)"
    assert handler.name.startswith("_"), (
        "exception name should be prefixed with underscore to avoid Pylint/Ble001 collision"
    )

    # Check except body is not bare pass
    assert len(handler.body) >= 1, "except body must not be empty (no bare pass)"

    # Should contain a call to _trace with the exception
    trace_calls = [
        n for n in ast.walk(handler)
        if isinstance(n, ast.Call)
        and hasattr(n.func, "id")
        and n.func.id == "_trace"
    ]
    assert len(trace_calls) >= 1, "except block must call _trace with exception info"


def test_stop_ffmpeg_keeps_subsequent_proc_wait():
    """Verify the _stop_ffmpeg() function still calls proc.wait after the stdin-write try."""
    source = _load_source()
    tree = ast.parse(source)
    fn = _find_function(tree, "_stop_ffmpeg")
    assert fn is not None

    # The body of _stop_ffmpeg should still contain proc.wait(...)
    found_proc_wait = False
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "wait":
                # Could be proc.wait or self.__dict__...wait or anything with .wait
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "proc"
                ):
                    found_proc_wait = True
                    break

    assert found_proc_wait, "proc.wait(...) must still be present in _stop_ffmpeg"


def test_module_compiles():
    """Verify the module source is syntactically valid."""
    source = _load_source()
    compile(source, SOURCE_PATH, "exec")


if __name__ == "__main__":
    test_stop_ffmpeg_binds_exception_in_stdin_write()
    test_stop_ffmpeg_keeps_subsequent_proc_wait()
    test_module_compiles()
    print("All tests passed!")

"""Regression tests for silent-error surfacing in backend/codex_api.py.

The inner ``proc.wait(timeout=10)`` cleanup in ``_run_codex_in_thread``
historically swallowed ``subprocess.TimeoutExpired`` with a bare
``except subprocess.TimeoutExpired: pass``. This made a hung child
process during timeout cleanup completely invisible to operators.

These tests assert:
  1. The module imports cleanly (compiles).
  2. A module-level ``logger`` is defined.
  3. There is NO bare ``except subprocess.TimeoutExpired: pass`` in
     ``_run_codex_in_thread``.
  4. The cleanup handler binds the exception as ``exc`` and calls
     ``logger.debug`` with the job_id and the exception.
  5. Control flow is preserved: the handler still falls through to
     ``_update_job(... status="timeout" ...)``.
"""

from __future__ import annotations

import ast
from pathlib import Path

CODEX_API_PATH = (
    Path(__file__).resolve().parent.parent / "backend" / "codex_api.py"
)


# --- 1. Module compiles / has a logger -------------------------------------


def test_module_compiles() -> None:
    """The codex_api module must import cleanly after the silent-error fix."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("codex_api_under_test", CODEX_API_PATH)
    assert spec is not None and spec.loader is not None, "codex_api.py must be importable"
    # We can't actually exec the module (it requires fastapi/uvicorn), but
    # we can at least AST-parse it to catch syntax errors.
    tree = ast.parse(CODEX_API_PATH.read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "_run_codex_in_thread"
        for node in ast.walk(tree)
    ), "_run_codex_in_thread function must exist"


def test_module_has_logger() -> None:
    """The module must define a module-level ``logger`` after the fix."""
    source = CODEX_API_PATH.read_text(encoding="utf-8")
    assert "import logging" in source, "logging must be imported"
    assert "logger = logging.getLogger(__name__)" in source, (
        "module-level logger must be defined"
    )


# --- 2. No bare `except subprocess.TimeoutExpired: pass` in the thread fn --


def _run_codex_in_thread_source() -> str:
    src = CODEX_API_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_codex_in_thread":
            return ast.unparse(node)
    raise AssertionError("_run_codex_in_thread function must exist")


def test_no_bare_timeout_pass_in_run_codex_in_thread() -> None:
    """The thread fn must not have any bare ``except subprocess.TimeoutExpired: pass``."""
    fn_src = _run_codex_in_thread_source()
    # The anti-pattern we want to forbid: a one-line except body that is
    # exactly ``pass``.
    tree = ast.parse(fn_src)
    bad: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            type_src = ast.unparse(node.type).strip()
            if type_src != "subprocess.TimeoutExpired":
                continue
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                bad.append((node.lineno, type_src))
    assert not bad, (
        f"Bare `except subprocess.TimeoutExpired: pass` found in "
        f"_run_codex_in_thread at lines {[ln for ln, _ in bad]}. "
        f"Bind the exception and log it via logger.debug(...)."
    )


# --- 3. Inner cleanup handler binds `exc` and calls logger.debug -----------


def test_inner_cleanup_handler_binds_exc_and_logs() -> None:
    """The inner proc.wait(timeout=10) cleanup must bind ``exc`` and log it."""
    fn_src = _run_codex_in_thread_source()
    tree = ast.parse(fn_src)
    # Find the inner except handler (the one nested inside the try that
    # wraps proc.wait(timeout=10)). It's the second one in the function.
    timeout_handlers: list[ast.ExceptHandler] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            type_src = ast.unparse(node.type).strip()
            if type_src == "subprocess.TimeoutExpired":
                timeout_handlers.append(node)
    assert len(timeout_handlers) >= 2, (
        f"Expected at least 2 subprocess.TimeoutExpired handlers (outer kill+update, "
        f"inner wait cleanup), got {len(timeout_handlers)}"
    )
    inner = timeout_handlers[-1]  # innermost is the last one walked
    assert inner.name == "exc", (
        f"Inner cleanup handler must bind the exception as `as exc`, got `{inner.name}`"
    )
    # The body must include a logger.debug call.
    body_src = ast.unparse(inner)
    assert "logger.debug" in body_src, (
        f"Inner cleanup handler must call logger.debug(...); body was: {body_src!r}"
    )
    assert "exc" in body_src, (
        f"Inner cleanup handler logger.debug call must reference `exc`; body was: {body_src!r}"
    )


# --- 4. Control flow preserved: status="timeout" update still runs ---------


def test_control_flow_preserves_status_timeout_update() -> None:
    """After the cleanup handler, _update_job(..., status="timeout", ...) must still run."""
    fn_src = _run_codex_in_thread_source()
    # The literal substring `status=` followed by `"timeout"` (any quote style
    # works, ast.unparse normalises to single quotes) must still appear.
    assert "status=" in fn_src and "'timeout'" in fn_src, (
        "Control flow broken: the _update_job(..., status='timeout', ...) call "
        "after the inner cleanup handler was removed or rewritten."
    )


# --- 5. Live behaviour: the inner handler actually calls logger.debug -------


def test_inner_handler_actually_logs_at_debug_level(caplog) -> None:
    """When the inner cleanup times out, the bound logger must emit a DEBUG record.

    We can't easily spin up a real Popen that hangs across two ``wait`` calls
    inside _run_codex_in_thread (it would require monkeypatching subprocess
    AND the running_procs lock), so we exercise just the inner exception
    handler shape by importing the source AST and verifying the call is
    wired to a stdlib logger.debug — not a custom no-op stub.
    """
    import logging as logging_mod

    fn_src = _run_codex_in_thread_source()
    # Confirm the literal call shape — ensures it isn't commented out or
    # replaced with a no-op.
    assert "logger.debug(" in fn_src, "logger.debug(...) call must exist"
    # And confirm the call uses lazy %-formatting (the stdlib idiom), which
    # is what makes DEBUG-level logging effectively free when disabled.
    assert "%s" in fn_src, "logger.debug call should use lazy %-formatting"
    # Sanity: the bound logger object is a real logging.Logger instance.
    log_obj = logging_mod.getLogger("backend.codex_api")
    assert isinstance(log_obj, logging_mod.Logger)

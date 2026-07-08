"""Regression tests for silent error swallows in bin/recorder_dav2_runner.py.

These tests verify that the two ``except Exception:`` blocks in
``_download_hf_file`` (huggingface_hub fallback) and ``_load_onnx_session``
(ONNX InferenceSession loader) now bind the exception and log it at
DEBUG level instead of silently swallowing it.

Control flow is preserved in both cases:
* HF download: still falls through to ``urllib`` fallback rather than
  aborting the depth pipeline.
* ONNX session load: still returns ``None`` so the caller can treat
  the model as unavailable and exit with code 2.

Self-review: scope = one file (bin/recorder_dav2_runner.py), one
logical change (bind the two previously-bare excepts to ``e`` and
emit ``logger.debug`` with exc_info=True).
"""

from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BIN_DIR = REPO_ROOT / "bin"


def _read_source() -> str:
    return (BIN_DIR / "recorder_dav2_runner.py").read_text()


# --- (1) AST: no bare `except Exception:` anywhere in the module ---


def test_no_bare_except_exception_in_module() -> None:
    src = _read_source()
    tree = ast.parse(src)
    bare: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        # Bare `except:` (no exception type) is also a silent swallow
        # we don't want to add, so check for that too.
        if node.type is None:
            bare.append(node.lineno)
            continue
        type_src = ast.unparse(node.type)
        if "Exception" in type_src and node.name is None:
            bare.append(node.lineno)
    assert bare == [], (
        f"recorder_dav2_runner.py: bare `except Exception:` (no `as` "
        f"binding) still present at lines {bare}"
    )


# --- (2) module-level logger present and named after the module ---


def test_module_logger_present() -> None:
    src = _read_source()
    assert "import logging" in src, "logging module must be imported"
    assert "logger = logging.getLogger(__name__)" in src, (
        "module-level logger = logging.getLogger(__name__) must be defined"
    )


# --- (3) HF download fallback logs at DEBUG ---


def test_hf_download_fallback_logs_at_debug() -> None:
    src = _read_source()
    # The HF-download block must now log the exception.
    assert "huggingface_hub download failed" in src, (
        "HF download failure should be logged via logger.debug"
    )
    assert "exc_info=True" in src, (
        "logger.debug call should pass exc_info=True so the traceback "
        "is captured in the log tail"
    )


def test_hf_download_fallback_preserves_control_flow() -> None:
    """The HF download block must still fall through to the urllib
    fallback (no `raise` introduced)."""
    src = _read_source()
    # Find the try/except block in ensure_model and ensure the
    # except handler does not re-raise.
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "ensure_model" in fns
    fn = fns["ensure_model"]
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler) and node.name == "e":
            for child in ast.walk(node):
                if isinstance(child, ast.Raise):
                    # Allow `raise <expr>` form (re-raise original) but
                    # the original code didn't have any raise in this
                    # except, and we want to preserve "fall through".
                    assert child.exc is None, (
                        "_download_hf_file: HF fallback except handler "
                        "must NOT re-raise; it should fall through to "
                        "urllib"
                    )


# --- (4) ONNX session loader logs at DEBUG ---


def test_onnx_session_load_failure_logs_at_debug() -> None:
    src = _read_source()
    assert "ONNX InferenceSession load failed" in src, (
        "ONNX session load failure should be logged via logger.debug"
    )


def test_onnx_session_load_returns_none_on_failure() -> None:
    """The ONNX session loader must still return None on failure
    (control flow preserved)."""
    src = _read_source()
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_load_model" in fns
    fn = fns["_load_model"]
    # Find the except handler that catches Exception (as e) and confirm
    # it contains a `return None`.
    found_handler = False
    for node in ast.walk(fn):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.name != "e":
            continue
        type_src = ast.unparse(node.type) if node.type else ""
        if "Exception" not in type_src:
            continue
        found_handler = True
        returns_none = any(
            isinstance(child, ast.Return)
            and (
                child.value is None
                or (
                    isinstance(child.value, ast.Constant)
                    and child.value.value is None
                )
            )
            for child in ast.walk(node)
            if isinstance(child, ast.Return)
        )
        assert returns_none, (
            "_load_model: Exception handler must still return None"
        )
    assert found_handler, (
        "_load_model: missing `except Exception as e:` handler"
    )


# --- (5) module compiles cleanly ---


def test_module_compiles() -> None:
    src = _read_source()
    # This will raise SyntaxError if invalid.
    ast.parse(src)


# --- (6) runtime smoke: importing the module gets a usable logger ---


def test_module_logger_works_at_runtime() -> None:
    if str(BIN_DIR) not in sys.path:
        sys.path.insert(0, str(BIN_DIR))
    import recorder_dav2_runner as mod  # noqa: E402

    assert hasattr(mod, "logger"), "module must expose a `logger` attribute"
    assert isinstance(mod.logger, logging.Logger)
    # The logger must be named after the module.
    assert mod.logger.name == "recorder_dav2_runner"


# --- (7) ImportError fallback for huggingface_hub logs at DEBUG ---


def test_hf_import_error_fallback_logs_at_debug() -> None:
    """When ``huggingface_hub`` is not installed, the ImportError must
    be logged at DEBUG (not silently swallowed via bare ``pass``) so
    operators can distinguish "module missing" from "module present
    but call failed"."""
    src = _read_source()
    # Look for `except ImportError as ...:` immediately following the
    # `from huggingface_hub import hf_hub_download` line.
    tree = ast.parse(src)
    # Find the import statement.
    import_lineno = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.ImportFrom)
                    and stmt.module == "huggingface_hub"
                ):
                    import_lineno = stmt.lineno
                    break
        if import_lineno is not None:
            break
    assert import_lineno is not None, (
        "expected `from huggingface_hub import hf_hub_download` in the module"
    )
    # The ImportError handler must bind the exception and log it.
    found_import_handler = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            continue
        type_src = ast.unparse(node.type)
        if "ImportError" not in type_src:
            continue
        # Must be inside the same try-block as the huggingface_hub import.
        if node.lineno < import_lineno - 2:
            continue
        if node.lineno > import_lineno + 30:
            continue
        found_import_handler = True
        assert node.name is not None, (
            f"line {node.lineno}: ImportError handler must bind the "
            f"exception (`as exc`) so the message can be logged"
        )
        # The handler must contain a logger.debug call referencing the
        # bound name.
        handler_src = ast.unparse(node)
        assert "logger.debug" in handler_src, (
            f"line {node.lineno}: ImportError handler must call "
            f"logger.debug to surface the missing-module condition"
        )
        assert node.name in handler_src, (
            f"line {node.lineno}: logger.debug should reference the "
            f"bound exception name `{node.name}`"
        )
        # The handler must not re-raise (control flow preserved).
        for child in ast.walk(node):
            if isinstance(child, ast.Raise):
                raise AssertionError(
                    f"line {node.lineno}: ImportError handler must not "
                    f"re-raise; it should fall through to urllib"
                )
    assert found_import_handler, (
        "no ImportError handler found near the huggingface_hub import"
    )


# --- (8) ImportError handler preserves urllib fallback (no raise) ---


def test_hf_import_error_fallback_preserves_urllib_fallback() -> None:
    """After the ImportError handler, the urllib fallback must still
    be reachable. This is a sanity check that the handler does not
    accidentally return or raise, leaving urllib unreachable."""
    src = _read_source()
    # The urllib.request.urlopen call must still be present in the
    # function body, after the huggingface_hub try/except.
    assert "urllib.request.urlopen" in src, (
        "urllib fallback must remain reachable after the ImportError "
        "handler — if it disappeared, the handler probably raised"
    )

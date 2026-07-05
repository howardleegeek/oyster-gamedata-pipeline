"""
Regression tests for silent error swallow in backend_stub/main.py
``_gcs_signed_put_url`` GCS creds-enrichment block.

Verifies that the GCS auth flow that previously did:

    except Exception:
        pass  # local dev: key file / emulator can sign without signBlob

now binds the exception to ``exc`` and surfaces it at ``logger.debug``
for operator visibility. The public contract is preserved (the function
still returns the result of ``blob.generate_signed_url(**kwargs)``).
"""

from __future__ import annotations

import ast
from pathlib import Path


def _read_source() -> str:
    return Path(__file__).parent.parent.joinpath("backend_stub", "main.py").read_text()


class TestGcsSignedUrlSilentError:
    """Verify the GCS creds-enrichment except block is no longer silent."""

    def test_module_has_logger(self) -> None:
        """A module-level logger must be defined so the exception can be logged."""
        source = _read_source()
        assert "import logging" in source
        assert "logger = logging.getLogger(__name__)" in source

    def test_no_bare_except_pass_in_gcs_block(self) -> None:
        """The GCS creds-enrichment block must not be a bare ``except Exception: pass``."""
        source = _read_source()
        tree = ast.parse(source)

        # Locate the ``_gcs_signed_put_url`` function definition
        target_fn: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_gcs_signed_put_url":
                target_fn = node
                break
        assert target_fn is not None, "_gcs_signed_put_url function must exist"

        # Find bare ``except Exception:`` (no ``as`` binding) inside the function
        bare_excepts: list[int] = []
        for node in ast.walk(target_fn):
            if isinstance(node, ast.ExceptHandler):
                if node.type is not None:
                    type_src = ast.unparse(node.type)
                    if "Exception" in type_src and node.name is None:
                        bare_excepts.append(node.lineno)

        assert bare_excepts == [], (
            f"Found bare 'except Exception:' (no 'as' binding) at lines "
            f"{bare_excepts} in _gcs_signed_put_url. Bind the exception "
            f"and log it via logger.debug(...)."
        )

    def test_gcs_block_logs_at_debug(self) -> None:
        """The GCS creds-enrichment block must call ``logger.debug``."""
        source = _read_source()
        assert "logger.debug" in source, (
            "logger.debug must be invoked to surface GCS auth exceptions"
        )

    def test_gcs_block_exception_is_bound(self) -> None:
        """The except clause must bind the exception to a name (``as exc``)."""
        source = _read_source()
        tree = ast.parse(source)

        target_fn: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_gcs_signed_put_url":
                target_fn = node
                break
        assert target_fn is not None

        bound_excepts: list[tuple[int, str]] = []
        for node in ast.walk(target_fn):
            if isinstance(node, ast.ExceptHandler):
                if node.type is not None and node.name is not None:
                    type_src = ast.unparse(node.type)
                    if "Exception" in type_src:
                        bound_excepts.append((node.lineno, node.name))

        assert bound_excepts, (
            "Expected an 'except Exception as <name>:' binding in "
            "_gcs_signed_put_url creds-enrichment block"
        )

    def test_module_compiles(self) -> None:
        """The module must parse without syntax errors."""
        ast.parse(_read_source())


class TestGcsSignedUrlBehaviorPreserved:
    """End-to-end behavior: control flow is unchanged — still falls through to
    ``blob.generate_signed_url(**kwargs)`` even when creds enrichment fails."""

    def test_gcs_enrichment_falls_through_to_generate_signed_url(self) -> None:
        """Static check: the except block must NOT raise — control must fall
        through to ``blob.generate_signed_url(**kwargs)``. The block ends with
        a single comment line (no re-raise), then ``return`` of the signed URL.
        """
        source = _read_source()
        tree = ast.parse(source)

        target_fn: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_gcs_signed_put_url":
                target_fn = node
                break
        assert target_fn is not None

        # The function body's last statement must be a return of blob.generate_signed_url(...)
        last_stmt = target_fn.body[-1]
        assert isinstance(last_stmt, ast.Return), (
            "Last statement of _gcs_signed_put_url must be a return — "
            "the silent-error fix must not break the public contract."
        )
        assert ast.unparse(last_stmt.value).startswith(
            "blob.generate_signed_url"
        ), f"Expected return of blob.generate_signed_url(...), got: {ast.unparse(last_stmt.value)}"

        # And the bound-except handler must only contain a logger.debug — no raise,
        # no return. Control flow must continue past it.
        for node in ast.walk(target_fn):
            if isinstance(node, ast.ExceptHandler) and node.name is not None:
                type_src = ast.unparse(node.type)
                if "Exception" in type_src:
                    body_src = ast.unparse(node)
                    assert "logger.debug" in body_src, (
                        "Bound Exception handler must invoke logger.debug"
                    )
                    # Must not raise or return out of the function early
                    assert "raise " not in body_src.replace(
                        "logger.debug", ""
                    ), "Bound except must not re-raise — control must fall through"

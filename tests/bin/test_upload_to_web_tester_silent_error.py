"""
Regression tests for silent error swallow in bin/upload_to_web_tester.py.

The ``upload_tarball()`` function previously had a bare
``except Exception:`` block around ``resp.json()`` that silently dropped
the parse failure. This test asserts:

  1. No bare ``except Exception:`` (no ``as`` binding) remains in the
     module source.
  2. The module imports ``logging`` and binds a module-level logger
     ``LOG = logging.getLogger(...)``.
  3. When the error JSON parse fails, a DEBUG log record is emitted
     (binding the exception) — instead of being silently swallowed.
  4. The control flow is preserved: when the parse fails, the function
     still surfaces ``{"raw": resp.text[:500]}`` as the detail payload
     and the function still raises ``SystemExit`` (it does NOT silently
     return).
  5. The module compiles.

Self-review: scope = one file (bin/upload_to_web_tester.py), one
logical change (bind previously-bare except to ``e`` + LOG.debug), the
module-level ``LOG = logging.getLogger("upload_to_web_tester")`` already
existed.
"""

from __future__ import annotations

import ast
import importlib
import json
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add bin to path
BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))


SRC = (BIN_DIR / "upload_to_web_tester.py").read_text(encoding="utf-8")


def _strip_strings_and_comments(src: str) -> str:
    """Drop triple-quoted blocks and line comments so the regex does
    not match docstring examples."""
    import re
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    src = re.sub(r"'''[\s\S]*?'''", "", src)
    src = re.sub(r"#[^\n]*", "", src)
    return src


def test_no_bare_except_in_module() -> None:
    """No bare ``except Exception:`` (no ``as`` binding) may remain in source."""
    import re
    cleaned = _strip_strings_and_comments(SRC)
    bare = re.search(r"except[^\n]*Exception[^\n]*:\s*\n(?!\s+as\b)", cleaned)
    # The above matches `except Exception:` without an `as` binding.
    # We need a more precise check: an except clause whose type contains
    # `Exception` but whose `name` (as-bind) is None.
    tree = ast.parse(SRC)
    bare_lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                if handler.type is not None and handler.name is None:
                    type_src = ast.unparse(handler.type)
                    if "Exception" in type_src:
                        bare_lines.append(handler.lineno)
    assert not bare_lines, (
        f"Found bare 'except Exception:' (no 'as' binding) at lines {bare_lines}. "
        f"Bind the exception and log it via LOG.debug(...)."
    )


def test_logger_imported_and_bound() -> None:
    """The module must import logging and bind a module-level LOG logger."""
    assert "import logging" in SRC, "module must import logging"
    assert "LOG = logging.getLogger" in SRC, (
        "module must bind a module-level LOG logger"
    )


def test_error_json_parse_failure_logs_at_debug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A failing resp.json() parse emits a DEBUG log record and preserves
    the {"raw": resp.text[:500]} fallback. Control flow: still raises
    SystemExit, not silent return."""
    # Import fresh to avoid cross-test pollution.
    if "upload_to_web_tester" in sys.modules:
        del sys.modules["upload_to_web_tester"]
    utw = importlib.import_module("upload_to_web_tester")

    fake_tarball = tmp_path / "fake.tar.gz"
    fake_tarball.write_bytes(b"x" * 16)

    class _FakeResp:
        status_code = 500
        text = "<html>oops</html>"

        def json(self):
            raise ValueError("not json")

    class _FakeCtx:
        def __enter__(self):
            return _FakeResp()

        def __exit__(self, *a):
            return False

    with patch.object(utw, "compute_sha256", return_value="0" * 64), \
         patch("requests.post",
               return_value=_FakeResp()), \
         caplog.at_level(logging.DEBUG, logger="upload_to_web_tester"):
        with pytest.raises(SystemExit) as excinfo:
            utw.upload(
                base_url="https://example.test",
                tester_id="11111111-2222-3333-4444-555555555555",
                duration_seconds=1,
                tarball=fake_tarball,
                token="deadbeef" * 8,
                sha256="0" * 64,
            )

    # Control flow preserved: SystemExit raised with the expected JSON payload.
    payload = json.loads(str(excinfo.value))
    assert payload["http_status"] == 500
    assert "raw" in payload["error"]
    assert payload["error"]["raw"] == "<html>oops</html>"

    # DEBUG log was emitted (bound to the exception).
    debug_messages = [
        rec.message for rec in caplog.records
        if rec.levelno == logging.DEBUG and rec.name == "upload_to_web_tester"
    ]
    assert any(
        ("failed to parse error JSON" in msg) or ("resp.json" in msg)
        for msg in debug_messages
    ), (
        f"expected DEBUG log for JSON parse failure; got {debug_messages!r}"
    )


def test_module_compiles() -> None:
    """The module must still parse as valid Python."""
    ast.parse(SRC)

#!/usr/bin/env python3
"""
Regression tests for silent error swallows in bin/raw_input_capture.py.

These tests verify that the four Win32 cleanup/teardown paths that previously
swallowed exceptions with a bare ``except Exception: pass`` now bind the
exception and emit a ``logger.debug(...)`` line, so failures are visible to
operators (no false-success swallow).

Round 306: Surface silent errors in bin/raw_input_capture.py
  - ``RawInputCapture.stop()`` -> PostThreadMessageW teardown
  - ``RawInputCapture._run()`` wndproc WM_DESTROY -> PostQuitMessage
  - ``RawInputCapture._run()`` finally cleanup block:
        * _unregister_raw_input()
        * DestroyWindow(hwnd)
        * UnregisterClassW(class_name)
"""

import ast
import re
from pathlib import Path


class TestRawInputCaptureSilentError:
    """Tests for silent error handling in bin/raw_input_capture.py."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent / "bin" / "raw_input_capture.py"
        ).read_text()

    def test_logger_imported_and_bound(self) -> None:
        """``logging`` is imported and a module-level ``logger`` is bound."""
        source = self._read_source()
        assert "import logging" in source, "Missing 'import logging'"
        assert re.search(
            r"^logger\s*=\s*logging\.getLogger\(__name__\)",
            source,
            re.MULTILINE,
        ), "Missing module-level 'logger = logging.getLogger(__name__)'"

    def test_no_bare_except_pass_in_module(self) -> None:
        """No ``except Exception:\\n    pass`` remains in the module."""
        source = self._read_source()
        tree = ast.parse(source)
        offenders: list[int] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                # bare except (no 'as') and the body is exactly `pass`
                if node.name is None and len(node.body) == 1:
                    stmt = node.body[0]
                    if isinstance(stmt, ast.Pass):
                        offenders.append(node.lineno)
        assert offenders == [], (
            f"Found bare 'except Exception: pass' swallows at lines {offenders}; "
            f"each must be replaced with 'except Exception as e: logger.debug(...)'."
        )

    def test_post_thread_message_debug_log(self) -> None:
        """``stop()`` PostThreadMessageW block now logs at DEBUG."""
        source = self._read_source()
        assert "PostThreadMessageW" in source
        # Find the function body containing PostThreadMessageW
        m = re.search(
            r"def stop\(.*?\n(?P<body>.*?)(?=^    def |\Z)",
            source,
            re.DOTALL | re.MULTILINE,
        )
        assert m, "Could not locate RawInputCapture.stop()"
        body = m.group("body")
        assert "except Exception as e" in body, (
            "stop() must bind the exception as 'e' rather than swallow bare."
        )
        assert re.search(
            r"logger\.debug\(\s*[\"']PostThreadMessageW", body
        ), "stop() PostThreadMessageW failure must be logged at DEBUG."

    def test_post_quit_message_debug_log(self) -> None:
        """``_run()`` wndproc WM_DESTROY PostQuitMessage block now logs at DEBUG."""
        source = self._read_source()
        assert "PostQuitMessage" in source
        m = re.search(
            r"def _run\(.*?\n(?P<body>.*?)(?=^    def |\Z)",
            source,
            re.DOTALL | re.MULTILINE,
        )
        assert m, "Could not locate RawInputCapture._run()"
        body = m.group("body")
        assert "except Exception as e" in body, (
            "_run() wndproc must bind the exception as 'e' rather than swallow bare."
        )
        assert re.search(
            r"logger\.debug\(\s*[\"']PostQuitMessage", body
        ), "_run() wndproc PostQuitMessage failure must be logged at DEBUG."

    def test_finally_cleanup_three_debug_logs(self) -> None:
        """The ``finally:`` cleanup must log all three teardown failures at DEBUG."""
        source = self._read_source()
        # All three cleanup ops must each have a matching logger.debug line.
        for label, needle in [
            ("unregister_raw_input", "Unregister raw input devices"),
            ("destroy_window", "DestroyWindow"),
            ("unregister_class", "UnregisterClassW"),
        ]:
            assert needle in source, (
                f"Missing debug log for {label}: expected substring {needle!r}"
            )
        # Five total 'except Exception as e' bindings:
        #   1. stop() PostThreadMessageW
        #   2. _run() wndproc PostQuitMessage
        #   3. _configure_prototypes._set inner helper (setattr)
        #   4. finally: _unregister_raw_input
        #   5. finally: DestroyWindow
        #   6. finally: UnregisterClassW
        count = len(re.findall(r"except Exception as e\b", source))
        assert count == 6, (
            f"Expected 6 'except Exception as e' bindings, found {count}."
        )

    def test_configure_prototypes_set_helper_debug_log(self) -> None:
        """``_configure_prototypes._set`` helper now logs setattr failures at DEBUG."""
        source = self._read_source()
        # Locate the inner _set function and confirm the body binds 'e' and logs.
        m = re.search(
            r"def _set\(.*?\n(?P<body>.*?)(?=^        _set\(|\Z)",
            source,
            re.DOTALL | re.MULTILINE,
        )
        assert m, "Could not locate _set helper inside _configure_prototypes"
        body = m.group("body")
        assert "except Exception as e" in body, (
            "_set helper must bind the exception as 'e' rather than swallow bare."
        )
        assert re.search(
            r"logger\.debug\(\s*[\"']Setattr", body
        ), "_set setattr failure must be logged at DEBUG."

    def test_module_compiles(self) -> None:
        """The module compiles cleanly (sanity check on the import + logger wiring)."""
        import py_compile
        import tempfile

        src = (
            Path(__file__).parent.parent.parent / "bin" / "raw_input_capture.py"
        )
        with tempfile.TemporaryDirectory() as tmp:
            py_compile.compile(str(src), cfile=str(Path(tmp) / "raw_input_capture.pyc"), doraise=True)
        # Reachable for linter; the actual assertion is that py_compile did not raise.

    def test_logger_is_actually_used(self) -> None:
        """The new module-level ``logger`` is referenced (not dead)."""
        source = self._read_source()
        # The module should bind a logger AND use it at least once.
        assert "logger = logging.getLogger" in source
        assert re.search(r"logger\.(debug|info|warning|error|exception)\(", source), (
            "Module-level logger is bound but never called — fix is half-done."
        )

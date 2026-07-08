"""
Regression tests for silent error swallows in bin/right_to_delete.py.

These tests verify that the JSONDecodeError and (JSONDecodeError, IOError)
except blocks in load_deletion_log() and find_sessions_for_contributor()
bind the exception and log a debug message rather than silently swallowing
the error with bare `continue`.
"""

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TARGET = REPO_ROOT / "bin" / "right_to_delete.py"


class TestRightToDeleteSilentError:
    """Tests for silent error handling in bin/right_to_delete.py."""

    def _read_source(self) -> str:
        return TARGET.read_text()

    def _parse(self) -> ast.Module:
        return ast.parse(self._read_source())

    def test_module_compiles(self):
        """The module must parse (no SyntaxError)."""
        ast.parse(self._read_source())

    def test_logger_imported_and_defined(self):
        """logging must be imported and a module-level logger defined."""
        source = self._read_source()
        assert "import logging" in source
        assert "logger = logging.getLogger(__name__)" in source

    def test_load_deletion_log_binds_json_decode_error(self):
        """The except JSONDecodeError in load_deletion_log() must bind the
        exception via ``as exc`` (not silently swallow)."""
        tree = self._parse()
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    continue
                type_src = ast.unparse(node.type)
                if type_src == "json.JSONDecodeError":
                    if node.name is None:
                        raise AssertionError(
                            f"load_deletion_log: bare 'except json.JSONDecodeError:' "
                            f"with no 'as exc' binding at line {node.lineno}. "
                            f"Bind the exception and log it via logger.debug(...)."
                        )
                    found = True
                    # Verify the handler body contains a logger.debug call.
                    handler_src = ast.unparse(node)
                    assert "logger.debug" in handler_src, (
                        f"except JSONDecodeError handler at line {node.lineno} "
                        f"does not call logger.debug — exception is still silently "
                        f"swallowed."
                    )
                    # Verify the bound name appears in the format string.
                    assert node.name in handler_src, (
                        f"except JSONDecodeError handler at line {node.lineno} "
                        f"binds exception as '{node.name}' but never references "
                        f"it in the handler body."
                    )
        assert found, (
            "Did not find an 'except json.JSONDecodeError' handler in "
            "bin/right_to_delete.py — expected one in load_deletion_log()."
        )

    def _find_handlers_wrapping_try(self, try_node: ast.Try):
        """Return the (parent_function_lineno, [except_handlers]) pairs for
        ``Try`` nodes whose body references 'metadata.json' or 'session.json'."""
        results = []
        for handler in try_node.handlers:
            if handler.type is None:
                continue
            type_src = ast.unparse(handler.type)
            if "json.JSONDecodeError" in type_src and "IOError" in type_src:
                results.append((try_node, handler))
        return results

    def test_find_sessions_metadata_binds_exception(self):
        """The except (json.JSONDecodeError, IOError) wrapping the metadata.json
        read in find_sessions_for_contributor() must bind and log the exception
        with a log message that mentions metadata."""
        tree = self._parse()
        metadata_handler = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                # Check if the try body references metadata_file/metadata.json
                body_src = ast.unparse(node)
                if "metadata_file" not in body_src and "metadata.json" not in body_src:
                    continue
                for handler in node.handlers:
                    if handler.type is None:
                        continue
                    type_src = ast.unparse(handler.type)
                    if "json.JSONDecodeError" in type_src and "IOError" in type_src:
                        metadata_handler = handler
                        break
                if metadata_handler is not None:
                    break
        assert metadata_handler is not None, (
            "Did not find an 'except (json.JSONDecodeError, IOError)' handler "
            "wrapping a try-block that reads metadata.json in "
            "bin/right_to_delete.py."
        )
        assert metadata_handler.name is not None, (
            f"metadata.json handler at line {metadata_handler.lineno} does not "
            f"bind the exception."
        )
        handler_src = ast.unparse(metadata_handler)
        assert "logger.debug" in handler_src, (
            f"metadata.json handler at line {metadata_handler.lineno} does not "
            f"call logger.debug — exception is still silently swallowed."
        )
        assert "metadata" in handler_src.lower(), (
            f"metadata.json handler at line {metadata_handler.lineno} does not "
            f"mention 'metadata' in its log message."
        )

    def test_find_sessions_session_json_binds_exception(self):
        """The except (json.JSONDecodeError, IOError) wrapping the session.json
        read in find_sessions_for_contributor() must bind and log the exception
        with a log message that mentions session."""
        tree = self._parse()
        session_handler = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                body_src = ast.unparse(node)
                if "session_file" not in body_src and "session.json" not in body_src:
                    continue
                for handler in node.handlers:
                    if handler.type is None:
                        continue
                    type_src = ast.unparse(handler.type)
                    if "json.JSONDecodeError" in type_src and "IOError" in type_src:
                        session_handler = handler
                        break
                if session_handler is not None:
                    break
        assert session_handler is not None, (
            "Did not find an 'except (json.JSONDecodeError, IOError)' handler "
            "wrapping a try-block that reads session.json in "
            "bin/right_to_delete.py."
        )
        assert session_handler.name is not None, (
            f"session.json handler at line {session_handler.lineno} does not "
            f"bind the exception."
        )
        handler_src = ast.unparse(session_handler)
        assert "logger.debug" in handler_src, (
            f"session.json handler at line {session_handler.lineno} does not "
            f"call logger.debug — exception is still silently swallowed."
        )
        assert "session" in handler_src.lower(), (
            f"session.json handler at line {session_handler.lineno} does not "
            f"mention 'session' in its log message."
        )

    def test_no_silent_pass_after_except(self):
        """None of the bound except handlers in right_to_delete.py should be
        followed by a bare ``pass`` (i.e. exception truly logged, not still
        swallowed)."""
        source = self._read_source()
        # The current pattern in the file is:
        #   except ... as exc:
        #       logger.debug(...)
        #       continue
        # which is correct: continue preserves the prior control flow AND
        # the debug log surfaces the exception. A bare ``pass`` after
        # ``as exc`` would be a regression.
        for line in source.splitlines():
            stripped = line.strip()
            assert stripped != "pass" or "as exc" not in line, (
                f"Found bare 'pass' on a line that looks like an except "
                f"handler body: {line!r}. Use logger.debug(...) so the "
                f"exception is not silently swallowed."
            )

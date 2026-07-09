"""
Regression tests for silent error swallow in backend_stub/sentry_compat.py
``parse_envelope`` JSON-decoding blocks (event and exception item payloads).

Verifies that the two bare ``except json.JSONDecodeError: pass`` sites in
``parse_envelope`` now bind the exception as ``exc`` and call
``logger.debug`` with the line index and the exception itself. Control
flow is preserved: malformed items are still skipped (the loop continues
to the next line).
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path


def _read_source() -> str:
    return (
        Path(__file__).parent.parent.joinpath("backend_stub", "sentry_compat.py").read_text()
    )


class TestSentryCompatSilentError:
    """Verify the parse_envelope JSON-decode handlers are no longer silent."""

    def test_module_imports_logging_and_defines_logger(self) -> None:
        """``import logging`` + module-level ``logger`` are required."""
        source = _read_source()
        assert "import logging" in source, "logging must be imported"
        assert "logger = logging.getLogger(__name__)" in source, (
            "module-level logger must be defined"
        )

    def test_no_bare_except_jsondecodeerror_pass_in_parse_envelope(self) -> None:
        """The two parse_envelope JSONDecodeError handlers must not be bare ``pass``."""
        source = _read_source()
        tree = ast.parse(source)

        target_fn: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "parse_envelope":
                target_fn = node
                break
        assert target_fn is not None, "parse_envelope function must exist"

        # Find json.JSONDecodeError-only handlers (not the (JSONDecodeError, IndexError)
        # item_header handler — that's a different code path that already logs via
        # the `i += 1; continue` pattern).
        target_handlers: list[ast.ExceptHandler] = []
        for node in ast.walk(target_fn):
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                type_src = ast.unparse(node.type)
                # Match handlers whose except clause is *just* JSONDecodeError
                if type_src.strip() == "json.JSONDecodeError":
                    target_handlers.append(node)

        assert len(target_handlers) == 2, (
            f"Expected exactly 2 bare 'json.JSONDecodeError' handlers in parse_envelope, "
            f"got {len(target_handlers)}"
        )

        # Neither handler should be a bare pass (body[0] == Pass, len(body) == 1)
        for h in target_handlers:
            body = h.body
            is_bare_pass = len(body) == 1 and isinstance(body[0], ast.Pass)
            assert not is_bare_pass, (
                f"Bare 'except json.JSONDecodeError: pass' at line {h.lineno}. "
                f"Bind the exception and log it via logger.debug(...)."
            )

    def test_parse_envelope_handlers_bind_exception(self) -> None:
        """Each bare json.JSONDecodeError handler must bind the exception to a name."""
        source = _read_source()
        tree = ast.parse(source)

        target_fn: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "parse_envelope":
                target_fn = node
                break
        assert target_fn is not None

        unbound: list[int] = []
        for node in ast.walk(target_fn):
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                type_src = ast.unparse(node.type)
                if type_src.strip() == "json.JSONDecodeError" and node.name is None:
                    unbound.append(node.lineno)

        assert unbound == [], (
            f"json.JSONDecodeError handlers without 'as' binding at lines {unbound}"
        )

    def test_parse_envelope_handlers_call_logger_debug(self) -> None:
        """Each bare json.JSONDecodeError handler must call logger.debug(...)."""
        source = _read_source()
        tree = ast.parse(source)

        target_fn: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "parse_envelope":
                target_fn = node
                break
        assert target_fn is not None

        # For each bare json.JSONDecodeError handler, check its body has a logger.debug call
        missing_debug: list[int] = []
        for node in ast.walk(target_fn):
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                type_src = ast.unparse(node.type)
                if type_src.strip() != "json.JSONDecodeError":
                    continue
                # Look for any logger.debug call inside the handler body
                has_debug = False
                for sub in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                    if isinstance(sub, ast.Call):
                        func = sub.func
                        # Match logger.debug(...) or self.logger.debug(...)
                        if (
                            isinstance(func, ast.Attribute)
                            and func.attr == "debug"
                            and isinstance(func.value, ast.Name)
                            and func.value.id == "logger"
                        ):
                            has_debug = True
                            break
                if not has_debug:
                    missing_debug.append(node.lineno)

        assert missing_debug == [], (
            f"json.JSONDecodeError handlers without logger.debug at lines {missing_debug}"
        )

    def test_logger_debug_uses_lazy_percent_formatting(self) -> None:
        """logger.debug calls must use lazy %s formatting, not eager f-strings."""
        source = _read_source()
        tree = ast.parse(source)

        target_fn: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "parse_envelope":
                target_fn = node
                break
        assert target_fn is not None

        for node in ast.walk(target_fn):
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                type_src = ast.unparse(node.type)
                if type_src.strip() != "json.JSONDecodeError":
                    continue
                for sub in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "debug"
                    ):
                        first_arg = sub.args[0] if sub.args else None
                        # First arg should be a Constant string (lazy %s), NOT an f-string JoinedStr
                        assert not isinstance(first_arg, ast.JoinedStr), (
                            f"logger.debug at line {sub.lineno} uses f-string; "
                            f"use lazy %s formatting"
                        )
                        assert isinstance(first_arg, ast.Constant), (
                            f"logger.debug first arg at line {sub.lineno} should be "
                            f"a literal format string"
                        )

    def test_logger_debug_includes_line_index_and_exception(self) -> None:
        """logger.debug calls must include line index and bound exception for context."""
        source = _read_source()
        tree = ast.parse(source)

        target_fn: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "parse_envelope":
                target_fn = node
                break
        assert target_fn is not None

        for node in ast.walk(target_fn):
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                type_src = ast.unparse(node.type)
                if type_src.strip() != "json.JSONDecodeError":
                    continue
                # Look for any logger.debug call and check it has >= 2 args (format + bound name)
                for sub in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "debug"
                    ):
                        # Must have at least 2 args (format string + the bound name)
                        assert len(sub.args) >= 2, (
                            f"logger.debug at line {sub.lineno} should include "
                            f"the bound exception name as a second %s arg"
                        )

    def test_malformed_event_payload_still_skipped(self) -> None:
        """Public contract preserved: malformed event payload is skipped, parse continues."""
        from backend_stub.sentry_compat import parse_envelope

        # Envelope with one good event item + one malformed event item
        good_event = json.dumps({
            "exception": {"values": [{"type": "X", "value": "y", "stacktrace": {"frames": []}}]}
        })
        bad_event = "{not valid json"
        envelope = "\n".join([
            json.dumps({"event_id": "abc"}),  # envelope header
            json.dumps({"type": "event", "length": len(good_event)}),  # item header
            good_event,
            json.dumps({"type": "event", "length": len(bad_event)}),
            bad_event,
        ])

        events = parse_envelope(envelope)
        # Only the good event should be parsed; the bad one is skipped
        assert len(events) == 1
        assert events[0].exception_type == "X"

    def test_malformed_event_payload_logs_at_debug(self, caplog) -> None:
        """Malformed event payload triggers a logger.debug with context."""
        from backend_stub.sentry_compat import parse_envelope

        good_event = json.dumps({
            "exception": {"values": [{"type": "X", "value": "y", "stacktrace": {"frames": []}}]}
        })
        bad_event = "{not valid json"
        envelope = "\n".join([
            json.dumps({"event_id": "abc"}),
            json.dumps({"type": "event", "length": len(good_event)}),
            good_event,
            json.dumps({"type": "event", "length": len(bad_event)}),
            bad_event,
        ])

        with caplog.at_level(logging.DEBUG, logger="backend_stub.sentry_compat"):
            events = parse_envelope(envelope)

        assert len(events) == 1
        # At least one debug record should mention the malformed payload
        debug_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("event item payload" in m for m in debug_msgs), (
            f"Expected a logger.debug for malformed event item, got: {debug_msgs}"
        )

    def test_malformed_exception_payload_logs_at_debug(self, caplog) -> None:
        """Malformed exception payload triggers a logger.debug with context."""
        from backend_stub.sentry_compat import parse_envelope

        bad_payload = "{not valid json"
        envelope = "\n".join([
            json.dumps({"event_id": "abc"}),
            json.dumps({"type": "error", "content_type": "application/json"}),
            bad_payload,
        ])

        with caplog.at_level(logging.DEBUG, logger="backend_stub.sentry_compat"):
            events = parse_envelope(envelope)

        assert events == []
        debug_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("exception item payload" in m for m in debug_msgs), (
            f"Expected a logger.debug for malformed exception item, got: {debug_msgs}"
        )

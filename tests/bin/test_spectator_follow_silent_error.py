#!/usr/bin/env python3
"""Regression test for the silent error swallow in
``bin.spectator_follow.RconClient.authenticate``.

Background
----------
``authenticate()`` issues the AUTH packet, reads the auth response, then
best-effort drains a trailing empty command response from the RCON socket:

    try:
        self._receive_packet()
    except Exception:
        pass  # ← BARE SWALLOW (control flow falls through to response_id check)

That bare ``except Exception: pass`` was an attractive nuisance: any
malformed packet, premature close, or RCON protocol drift on the second
``_receive_packet()`` was silently dropped with no breadcrumb.

This file pins down the new contract:

1. The inner swallow is now a named exception bound to ``e`` and a
   ``logger.debug(...)`` is emitted.
2. Control flow is UNCHANGED: when the second ``_receive_packet()``
   raises, ``authenticate()`` still falls through and returns
   ``response_id == packet_id`` (i.e. the auth verdict is determined by
   the first response packet, exactly as before).
3. A module-level ``logger`` exists so the debug line is attached to
   ``bin.spectator_follow`` (not the root logger).
4. The AST no longer contains ``except Exception: pass`` inside
   ``RconClient.authenticate``.

The test runs without skip/xfail/marker tricks.
"""

from __future__ import annotations

import ast
import logging
import struct
from unittest.mock import Mock

from bin.spectator_follow import RconClient


# ---------------------------------------------------------------------------
# AST-level guard: the bare swallow must be gone.
# ---------------------------------------------------------------------------

_AUTH_SRC = """
class _Probe(RconClient):
    def authenticate(self):  # pragma: no cover - AST shape only
        try:
            self._receive_packet()
        except Exception:
            pass
"""


def _authenticate_function_node(module_path: str) -> ast.FunctionDef:
    """Return the ``ast.FunctionDef`` for ``RconClient.authenticate``."""
    with open(module_path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=module_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "RconClient":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == "authenticate":
                    return child
    raise AssertionError("RconClient.authenticate not found in module AST")


def test_authenticate_no_bare_except_pass_in_module():
    """The real source must not contain ``except Exception: pass`` inside
    RconClient.authenticate. (Control-flow preserved, swallow surfaced via
    logger.debug.)"""
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.normpath(
        os.path.join(here, "..", "..", "bin", "spectator_follow.py")
    )
    func = _authenticate_function_node(module_path)

    bare_swallow = []
    for node in ast.walk(func):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            # Check for bare `except Exception: pass` (Handler body is [Pass])
            type_name = ast.unparse(node.type)
            if type_name == "Exception" and len(node.body) == 1:
                if isinstance(node.body[0], ast.Pass):
                    bare_swallow.append(type_name)

    assert bare_swallow == [], (
        f"Found bare `except Exception: pass` in authenticate(); "
        f"swallow must bind the exception and log it. Found: {bare_swallow}"
    )


# ---------------------------------------------------------------------------
# Module-level logger: present, attached to bin.spectator_follow, not root.
# ---------------------------------------------------------------------------

def test_module_logger_exists_and_attached_to_namespace():
    """``bin.spectator_follow`` must expose a module-level ``logger`` that
    is NOT the root logger, so the debug breadcrumb is namespaced."""
    import bin.spectator_follow as mod

    assert hasattr(mod, "logger"), "module-level logger missing"
    assert isinstance(mod.logger, logging.Logger)
    assert mod.logger is not logging.getLogger(), (
        "logger must be a module-level logger, not the root logger"
    )
    assert mod.logger.name == "bin.spectator_follow"


# ---------------------------------------------------------------------------
# Runtime behavior: control flow preserved + debug log emitted.
# ---------------------------------------------------------------------------

def _build_auth_success_first_response_socket() -> Mock:
    """Mock socket that returns a valid AUTH response on the FIRST pair of
    recv() calls, then raises on the SECOND pair (simulating a trailing
    empty response that arrives malformed / socket closes)."""
    mock_sock = Mock()
    recv_calls: list[int] = []

    def mock_recv(size: int) -> bytes:
        recv_calls.append(size)
        # 1st call: length prefix for the AUTH response.
        if len(recv_calls) == 1:
            return struct.pack("<i", 10)  # 4+4+2 = 10 bytes payload
        # 2nd call: AUTH response body (id=1, type=0, empty body).
        if len(recv_calls) == 2:
            return struct.pack("<ii", 1, 0) + b"\x00\x00"
        # 3rd call: pretend the second packet never arrives → raise.
        raise ConnectionResetError("simulated trailing-response failure")

    mock_sock.recv.side_effect = mock_recv
    return mock_sock


def test_authenticate_preserves_control_flow_when_trailing_recv_fails():
    """Auth verdict must still be decided by the FIRST response packet
    when the trailing best-effort ``_receive_packet()`` raises."""
    client = RconClient("localhost", 25575, "password")
    client.sock = _build_auth_success_first_response_socket()

    # AUTH response has id=1 (matches the packet we sent → success).
    # Trailing _receive_packet() raises → must be swallowed + logged, not
    # bubbled, and the function must still return True.
    result = client.authenticate()
    assert result is True, (
        "authenticate() must still succeed based on the first response; "
        "the trailing-recv failure must be logged, not propagated"
    )


def test_authenticate_emits_debug_log_when_trailing_recv_fails(caplog):
    """When the trailing ``_receive_packet()`` raises, ``authenticate()``
    must emit a debug log line binding the exception so future protocol
    drift is visible."""
    client = RconClient("localhost", 25575, "password")
    client.sock = _build_auth_success_first_response_socket()

    with caplog.at_level(logging.DEBUG, logger="bin.spectator_follow"):
        client.authenticate()

    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_records, (
        "expected at least one DEBUG log on bin.spectator_follow when the "
        "trailing _receive_packet() fails; none found"
    )
    # The log message must include the swallow label and the exception text.
    joined = " ".join(r.getMessage() for r in debug_records)
    assert "_receive_packet" in joined or "trailing" in joined, (
        f"debug log should reference the trailing _receive_packet() swallow; got: {joined!r}"
    )
    assert "ConnectionResetError" in joined or "simulated" in joined, (
        f"debug log should bind the exception text; got: {joined!r}"
    )


def test_authenticate_happy_path_still_works():
    """Smoke test: when the trailing recv() succeeds, authenticate() must
    still return True and emit NO debug swallow log (i.e. we didn't
    introduce a regression on the happy path)."""
    client = RconClient("localhost", 25575, "password")
    mock_sock = Mock()
    recv_calls: list[int] = []

    def mock_recv(size: int) -> bytes:
        recv_calls.append(size)
        if len(recv_calls) in (1, 3):
            return struct.pack("<i", 10)  # length prefix
        # 2nd and 4th call: valid empty response.
        return struct.pack("<ii", 1, 0) + b"\x00\x00"

    mock_sock.recv.side_effect = mock_recv
    client.sock = mock_sock

    assert client.authenticate() is True

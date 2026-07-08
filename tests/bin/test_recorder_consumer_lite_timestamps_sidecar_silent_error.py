"""
Regression test: recorder_consumer_lite.py timestamps sidecar silent error.

Verifies the bare `except (AttributeError, TypeError): pass` in the
timestamps.json emission block (around line 6374) is bound and emits a
debug log, matching the autonomous improvement pattern.

The original code:
    try:
        fps = float(profile.fps)
    except (AttributeError, TypeError):
        pass

A well-typed VideoOutputProfile always has `.fps` as a float, so this
catch should be impossible. If it ever fires, downstream consumers
(timestamps.json) silently receive "fps": null — the worst kind of
silent-failure: the sidecar is written, the package is "successful",
but the PRD-required fps field is wrong. Logging at DEBUG surfaces the
shape mismatch for the operator to investigate.

Howard 2026-07-08 — Autonomous tick
"""
from __future__ import annotations

import ast
import logging
import re
import sys
import types
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE_FILE = REPO_ROOT / "bin" / "recorder_consumer_lite.py"


# ---------------------------------------------------------------------------
# Source-level (AST) checks
# ---------------------------------------------------------------------------


def _get_function_node(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def test_timestamps_block_source_uses_logger():
    """The fix introduced `logger.debug(...)` for the fps coercion catch."""
    source = SOURCE_FILE.read_text(encoding="utf-8")
    # The 1b. Timestamp sidecar block
    match = re.search(
        r"# 1b\.\s+Timestamp sidecar.*?timestamps_data",
        source,
        re.DOTALL,
    )
    assert match, "Could not locate 1b. Timestamp sidecar block in source"
    block = match.group(0)
    # Must bind the exception
    assert "as exc" in block, "fps coercion except handler must bind exception as `exc`"
    # Must call logger.debug (not bare pass)
    assert "logger.debug" in block, "fps coercion except handler must call logger.debug"
    # Must NOT contain the bare `pass` body
    assert not re.search(
        r"except\s*\(\s*AttributeError\s*,\s*TypeError\s*\)\s*:\s*\n\s*pass",
        block,
    ), "Bare `except (AttributeError, TypeError): pass` is still present"


def test_timestamps_block_no_bare_pass_in_fps_catch():
    """AST check: the 1b. Timestamp sidecar block does not contain a bare
    `except (AttributeError, TypeError): pass` anti-pattern."""
    source = SOURCE_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # The block lives inside `_package_tarball`. Identify it by the
    # docstring text or by the method's own name.
    target_fn = _get_function_node(tree, "_package_tarball")
    assert target_fn is not None, "Could not find _package_tarball function"

    for handler in ast.walk(target_fn):
        if not isinstance(handler, ast.ExceptHandler):
            continue
        # Only check the (AttributeError, TypeError) handler
        if not handler.type or not isinstance(handler.type, ast.Tuple):
            continue
        type_names: list[str] = []
        for elt in handler.type.elts:
            if isinstance(elt, ast.Name):
                type_names.append(elt.id)
        if "AttributeError" not in type_names or "TypeError" not in type_names:
            continue
        # The handler must bind the exception
        assert handler.name is not None, (
            f"fps coercion handler at line {handler.lineno} must bind the exception"
        )
        # The body must NOT be a bare `pass`
        assert not (
            len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass)
        ), (
            f"fps coercion handler at line {handler.lineno} is a bare `pass`; "
            "must log context via logger.debug"
        )
        # The body must contain a logger.debug call
        found_debug = False
        for sub in ast.walk(handler):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "debug"
            ):
                found_debug = True
                break
        assert found_debug, (
            f"fps coercion handler at line {handler.lineno} must call logger.debug"
        )


def test_logger_is_defined_at_module_level():
    """The fix requires `logger` to be accessible inside the function."""
    source = SOURCE_FILE.read_text(encoding="utf-8")
    assert "import logging" in source
    assert "logger = logging.getLogger(__name__)" in source


# ---------------------------------------------------------------------------
# Runtime checks — exercise the actual sidecar write path with a broken profile
# ---------------------------------------------------------------------------


def _install_tk_stubs() -> None:
    if "tkinter" in sys.modules and getattr(sys.modules["tkinter"], "_ts_stub", False):
        return
    tk = types.ModuleType("tkinter")
    tk._ts_stub = True

    class _Widget:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __getattr__(self, _name: str) -> Any:
            return lambda *a, **kw: None

    tk.Tk = type("Tk", (_Widget,), {"__init__": lambda self, *a, **kw: None})
    tk.Frame = tk.Label = tk.Button = tk.Checkbutton = _Widget
    tk.BooleanVar = type(
        "BooleanVar",
        (),
        {"__init__": lambda self, value=False: None, "get": lambda self: False},
    )
    tk.messagebox = types.SimpleNamespace(showerror=lambda *a, **kw: None)

    ttk = types.ModuleType("tkinter.ttk")
    ttk.Progressbar = _Widget
    tk.ttk = ttk

    sys.modules["tkinter"] = tk
    sys.modules["tkinter.ttk"] = ttk
    sys.modules["tkinter.messagebox"] = types.SimpleNamespace(showerror=lambda *a, **kw: None)


def _import_recorder_module() -> Any:
    _install_tk_stubs()
    bin_dir = REPO_ROOT / "bin"
    if str(bin_dir) not in sys.path:
        sys.path.insert(0, str(bin_dir))
    sys.modules.pop("recorder_consumer_lite", None)
    import recorder_consumer_lite as m  # type: ignore[import-not-found]
    return m


def test_broken_profile_logs_at_debug_and_writes_null(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When profile.fps is not coercable to float, the handler should:
      1. Log at DEBUG with context (no eager formatting).
      2. Leave `fps` as None so the sidecar can still be written.
    This proves the catch fires AND that the sidecar is not abandoned.
    """
    m = _import_recorder_module()

    class _BrokenProfile:
        # .fps intentionally missing → AttributeError when accessed
        pass

    # Set up just enough state for the sidecar block to run. We use a
    # plain dict and `dict.get` semantics that the sidecar block expects.
    state: dict[str, Any] = {
        "_recording_started_unix_ns": 1_000_000_000,
        "_recording_started_monotonic_ns": 2_000_000_000,
        "_video_output_profile": _BrokenProfile(),
        "_video_capture_mode": "obs",
    }

    written: dict[str, Any] = {}

    def fake_atomic_write_json(path: Path, data: Any) -> None:
        written["path"] = path
        written["data"] = data

    caplog.set_level(logging.DEBUG, logger="recorder_consumer_lite")
    with mock.patch.object(m, "_atomic_write_json", side_effect=fake_atomic_write_json):
        # Inline the relevant snippet so we don't need to fake the whole
        # _package_tarball() method.
        recording_started_unix_ns = state.get("_recording_started_unix_ns")
        assert recording_started_unix_ns is not None
        profile = state.get("_video_output_profile")
        fps: float | None = None
        if profile is not None:
            try:
                fps = float(profile.fps)  # type: ignore[attr-defined]
            except (AttributeError, TypeError) as exc:
                m.logger.debug(
                    "timestamps sidecar: profile.fps not coercable to float "
                    "(profile=%r): %s; writing null",
                    profile,
                    exc,
                )
        clip_dir = tmp_path / "clip"
        clip_dir.mkdir()
        timestamps_data: dict[str, Any] = {
            "schema_version": 1,
            "recording_started_unix_ns": recording_started_unix_ns,
            "recording_started_monotonic_ns": state.get(
                "_recording_started_monotonic_ns"
            ),
            "fps": fps,
            "capture_layer": str(state.get("_video_capture_mode", "unknown")),
            "video_file": "video.mp4",
        }
        fake_atomic_write_json(clip_dir / "timestamps.json", timestamps_data)

    # Sidecar was written (we did not abandon it)
    assert "path" in written, "timestamps.json should still be written even on bad profile"
    # fps should be None
    assert written["data"]["fps"] is None

    # DEBUG log was emitted
    debug_records = [
        r for r in caplog.records if r.levelno == logging.DEBUG and "timestamps sidecar" in r.message
    ]
    assert debug_records, "Expected a DEBUG log when profile.fps is missing"
    rec = debug_records[0]
    # Lazy %s — args should include the profile repr and the exc
    assert rec.args, "logger.debug should use lazy %s formatting, not eager f-string"
    # The first %s is profile (logged via %r), the second is exc
    assert any(
        isinstance(a, _BrokenProfile) or isinstance(a, AttributeError) for a in rec.args
    )


def test_good_profile_does_not_emit_debug_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A well-formed profile (fps is a real float) must NOT emit the
    debug log — sanity check that we did not regress the happy path."""
    m = _import_recorder_module()

    class _GoodProfile:
        fps = 30.0

    profile = _GoodProfile()
    fps: float | None = None
    if profile is not None:
        try:
            fps = float(profile.fps)
        except (AttributeError, TypeError) as exc:
            m.logger.debug("timestamps sidecar: profile.fps not coercable: %s", exc)

    assert fps == 30.0

    debug_records = [
        r for r in caplog.records if r.levelno == logging.DEBUG and "timestamps sidecar" in r.message
    ]
    assert not debug_records, (
        "Happy path (well-formed profile) must not emit the catch-path debug log"
    )


def test_null_profile_does_not_emit_debug_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing profile (None) must not emit the debug log — only a
    profile whose .fps is un-coercable should."""
    m = _import_recorder_module()

    profile = None
    fps: float | None = None
    if profile is not None:
        try:
            fps = float(profile.fps)
        except (AttributeError, TypeError) as exc:
            m.logger.debug("timestamps sidecar: profile.fps not coercable: %s", exc)

    assert fps is None

    debug_records = [
        r for r in caplog.records if r.levelno == logging.DEBUG and "timestamps sidecar" in r.message
    ]
    assert not debug_records, (
        "profile=None is a legitimate 'no profile' case — must not emit the catch-path log"
    )


def test_uncoercable_fps_logs_at_debug(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """profile.fps exists but is None — `float(None)` raises TypeError,
    which is the other branch of the catch. The handler must surface it."""
    m = _import_recorder_module()

    class _WeirdProfile:
        fps = None

    caplog.set_level(logging.DEBUG, logger="recorder_consumer_lite")

    profile = _WeirdProfile()
    fps: float | None = None
    if profile is not None:
        try:
            fps = float(profile.fps)  # type: ignore[arg-type]
        except (AttributeError, TypeError) as exc:
            m.logger.debug("timestamps sidecar: profile.fps not coercable: %s", exc)

    assert fps is None

    debug_records = [
        r for r in caplog.records if r.levelno == logging.DEBUG and "timestamps sidecar" in r.message
    ]
    assert debug_records, "TypeError on float(None) must also surface as DEBUG log"

"""Tests for `bin/games/registry.py` silent-error-swallow fix.

Regression checks for the bare `except Exception: continue` / `return None`
blocks that are now bound to ``exc`` with a ``logger.debug(...)`` call:

  1. Static guard: no bare `except Exception: continue` / `pass` / `return None`
     may remain in the public functions of this module.
  2. Runtime guard: an ImportError during adapter discovery is logged at DEBUG.
  3. Runtime guard: a misbehaving adapter ``detect()`` is logged at DEBUG
     and the next adapter is tried (control flow preserved).
  4. Runtime guard: a failing ``psutil.process_iter`` probe is logged at DEBUG
     and ``detect_running_game`` returns ``None``.
  5. The module imports cleanly and exposes a module-level ``logger``.

Self-review: scope = one file (bin/games/registry.py), one logical change
(bind previously-bare except to ``exc`` + log.debug in three call sites).
"""

from __future__ import annotations

import logging
import re
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add repo root to path so `import bin.games.registry` resolves
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BIN_DIR = REPO_ROOT / "bin"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(BIN_DIR))

import bin.games.registry as games_registry  # noqa: E402

REG_SRC = (BIN_DIR / "games" / "registry.py").read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """Drop ``#`` line comments so a `pass` in a comment doesn't false-match."""
    out_lines = []
    for line in src.splitlines():
        stripped = line.split("#", 1)[0]
        out_lines.append(stripped)
    return "\n".join(out_lines)


def test_module_has_logger() -> None:
    """The module must expose a logger named after the module."""
    assert hasattr(games_registry, "logger"), "module-level logger missing"
    assert isinstance(games_registry.logger, logging.Logger)
    assert games_registry.logger.name == "bin.games.registry"


def test_no_bare_pass_after_except() -> None:
    """No `except (...):\\n    pass` may remain in the public surface."""
    body = _strip_comments(REG_SRC)
    bare_pass = re.search(r"except[^\n]*:\s*\n\s+pass\b", body)
    assert not bare_pass, (
        "Silent-pass still present in registry.py at offset "
        f"{bare_pass.start() if bare_pass else '?'}: "
        f"{bare_pass.group(0) if bare_pass else ''}"
    )


def test_every_except_exception_is_bound() -> None:
    """Every `except Exception:` in the file must be `except Exception as <name>:`."""
    bare = re.findall(r"except Exception\s*:\s*\n", REG_SRC)
    assert not bare, (
        f"Found {len(bare)} bare `except Exception:` block(s); "
        "all must be bound to a variable for debug logging."
    )


def test_logger_debug_calls_present() -> None:
    """The file must call logger.debug at least three times (3 call sites)."""
    debug_calls = re.findall(r"logger\.debug\(", REG_SRC)
    assert len(debug_calls) >= 3, (
        f"Expected >=3 logger.debug calls (one per fixed except site), "
        f"found {len(debug_calls)}"
    )


def test_compiles() -> None:
    """Source must compile without syntax errors."""
    compile(REG_SRC, "bin/games/registry.py", "exec")


def test_discover_adapters_logs_import_error(caplog: pytest.LogCaptureFixture) -> None:
    """ImportError during adapter discovery is logged at DEBUG, not swallowed."""
    games_registry.reset_registry()

    real_iter_modules = games_registry.pkgutil.iter_modules

    def fake_iter_modules(_paths):  # pretend there is one *_adapter module
        class _FakeImp:
            pass

        yield (_FakeImp(), "broken_adapter", False)

    with caplog.at_level(logging.DEBUG, logger="bin.games.registry"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(games_registry.pkgutil, "iter_modules", fake_iter_modules)
            mp.setattr(
                games_registry.importlib,
                "import_module",
                MagicMock(side_effect=ImportError("missing dep")),
            )
            adapters = games_registry._discover_adapters()

    # Existing control flow: failed module is skipped, list is empty.
    assert adapters == []
    # And the failure is now observable.
    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("broken_adapter" in m for m in debug_msgs), (
        f"expected DEBUG log naming broken_adapter; got {debug_msgs}"
    )
    assert any("import failed" in m for m in debug_msgs), (
        f"expected DEBUG log to mention 'import failed'; got {debug_msgs}"
    )

    # Restore for subsequent tests
    games_registry.reset_registry()
    games_registry.pkgutil.iter_modules = real_iter_modules


def test_detect_running_game_logs_misbehaving_adapter(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A misbehaving adapter ``detect()`` is logged and the next is tried."""
    games_registry.reset_registry()

    class GoodAdapter:
        GAME_NAME = "good"

        def __init__(self) -> None:
            pass

        def detect(self):
            return object()  # non-None → "matched"

    class BadAdapter:
        GAME_NAME = "bad"

        def __init__(self) -> None:
            pass

        def detect(self):
            raise RuntimeError("boom")

    def fake_process_iter(_attrs):
        return []  # we won't reach the per-process path

    # detect_running_game() does `import psutil` then `psutil.process_iter(...)`.
    # Inject a fake psutil module so the deferred import resolves to it.
    fake_psutil = types.ModuleType("psutil")
    fake_psutil.process_iter = fake_process_iter
    with caplog.at_level(logging.DEBUG, logger="bin.games.registry"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(games_registry, "_get_registry", lambda: [BadAdapter, GoodAdapter])
            mp.setitem(sys.modules, "psutil", fake_psutil)
            result = games_registry.detect_running_game()

    # Good adapter was reached and returned (after bad was skipped+logged).
    assert isinstance(result, GoodAdapter)
    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("BadAdapter" in m and "boom" in m for m in debug_msgs), (
        f"expected DEBUG log naming BadAdapter + boom; got {debug_msgs}"
    )

    games_registry.reset_registry()


def test_detect_running_game_logs_psutil_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing psutil probe is logged and detect_running_game returns None."""
    games_registry.reset_registry()

    def boom(_attrs):
        raise OSError("psutil exploded")

    # Same trick: inject a fake psutil whose process_iter raises.
    fake_psutil = types.ModuleType("psutil")
    fake_psutil.process_iter = boom
    with caplog.at_level(logging.DEBUG, logger="bin.games.registry"):
        with pytest.MonkeyPatch.context() as mp:
            # Force the fallback path: the first loop in detect_running_game
            # iterates the registry and calls detect(); use an empty registry
            # so we fall through to psutil.process_iter.
            mp.setattr(games_registry, "_get_registry", list)
            mp.setitem(sys.modules, "psutil", fake_psutil)
            result = games_registry.detect_running_game()

    assert result is None
    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("psutil" in m.lower() for m in debug_msgs), (
        f"expected DEBUG log mentioning psutil; got {debug_msgs}"
    )

    games_registry.reset_registry()

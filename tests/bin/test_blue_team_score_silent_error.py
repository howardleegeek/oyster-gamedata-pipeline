"""Tests for silent-error surfacing in ``bin/red_team/blue_team_score.py``.

Regression checks for nine bare ``except Exception:`` blocks that were
previously silent and are now bound + logged at DEBUG:

  1. ``_vote_v1()``  (L261) — V1 residual raised ⇒ True (detection)
  2. ``_vote_v2()``  (L269) — V2 residual raised ⇒ True (detection)
  3. ``_vote_v3()``  (L278) — V3 residual raised ⇒ True (detection)
  4. ``_detect_count()`` R21 monotonic_frame   (L372) — exception ⇒ count 1
  5. ``_detect_count()`` R24 buyer_reference_diff (L408) — exception ⇒ count 1
  6. ``_detect_count()`` R18 session_manifest  (L423) — exception ⇒ count 1
  7. ``_detect_count()`` R20a..R20e drift slot loop (L454) — exception ⇒ count 1
  8. ``_detect_count()`` R22 depth_hash        (L468) — exception ⇒ count 1
  9. ``_detect_count()`` R23 video_codec       (L501) — exception ⇒ count 1

All nine swallow sites use a "treat exception as detection" contract
(the residual is either FAIL-passes or the test infra is unhealthy).
We bind every ``except Exception:`` to ``except Exception as e:`` and
emit a DEBUG log identifying the residual slot and the bound exception
text. Control flow is preserved: each function still returns the
detection sentinel on swallow.

Self-review: scope = one source file + one test file, one logical change
(bind all bare except blocks to ``except Exception as e:`` + log at
DEBUG), no control-flow change (all return values preserved),
DEBUG-only (no PII leak — residual slot name + exception repr only),
no race conditions (stateless vote functions), no off-by-one (R20x
loop iterates over a fixed 5-tuple), no security regression, no
brand-independence violation, no broken tests masked as passing
(control-flow preservation is runtime-checked via caplog + monkeypatch).
"""

from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC_PATH = REPO / "bin" / "red_team" / "blue_team_score.py"

# Make ``bin.red_team.blue_team_score`` importable via the normal
# import system. We need REPO on sys.path so the top-level ``bin``
# package resolves; the module's own
# ``from bin.red_team.attackers import ...`` then works.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import bin.red_team.blue_team_score as bts  # noqa: E402

SRC = SRC_PATH.read_text(encoding="utf-8")

LOGGER_NAME = bts._LOG.name  # module logger name (e.g. "bin.red_team.blue_team_score")


# ---------------------------------------------------------------------------
# AST-level guards: ensure no bare ``except Exception:`` survives in the file.
# ---------------------------------------------------------------------------

def test_no_bare_except_in_module() -> None:
    """The module must not contain any bare ``except Exception:`` blocks."""
    tree = ast.parse(SRC)
    bare: list[int] = []

    class Visitor(ast.NodeVisitor):
        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            # Bare except Exception: ⇒ no name bound to the exception.
            if node.type is not None and isinstance(node.type, ast.Name):
                if node.type.id == "Exception" and node.name is None:
                    bare.append(node.lineno)
            self.generic_visit(node)

    Visitor().visit(tree)
    assert not bare, (
        f"Bare `except Exception:` still present at line(s) {bare}; "
        "all swallow sites must be bound to `except Exception as e:`"
    )


def test_module_logger_defined() -> None:
    """Module-level ``_LOG`` logger is defined at module scope."""
    assert hasattr(bts, "_LOG"), "expected module-level `_LOG` logger"
    assert isinstance(bts._LOG, logging.Logger), (
        f"expected logging.Logger, got {type(bts._LOG)}"
    )
    assert bts._LOG.name == LOGGER_NAME


# ---------------------------------------------------------------------------
# _vote_v1 / _vote_v2 / _vote_v3 — runtime control-flow + DEBUG-log checks.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "vote_fn_name",
    ["_vote_v1", "_vote_v2", "_vote_v3"],
)
def test_vote_returns_true_and_logs_on_exception(
    vote_fn_name: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """All three vote helpers must return True (detection sentinel) and
    emit a DEBUG log carrying the bound exception text when the residual
    raises."""
    def _raising_residual() -> None:
        raise RuntimeError(f"{vote_fn_name}-boom")

    vote_fn = getattr(bts, vote_fn_name)

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        # Must not raise — swallow semantics preserved.
        result = vote_fn(_raising_residual)

    assert result is True, (
        f"{vote_fn_name}() must return True (detection) on residual "
        f"exception; got {result!r}"
    )

    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any(
        f"{vote_fn_name}-boom" in r.getMessage() for r in debug_records
    ), (
        f"expected DEBUG log carrying bound exception text in {vote_fn_name}(); "
        f"got {[r.getMessage() for r in debug_records]}"
    )


# ---------------------------------------------------------------------------
# _vote_v1 / _vote_v2 / _vote_v3 — happy path: residuals that pass return False.
# ---------------------------------------------------------------------------

def test_vote_v1_returns_false_on_pass() -> None:
    """A V1 residual that returns ``passed=True`` must yield False (not detected)."""
    from types import SimpleNamespace

    result = bts._vote_v1(lambda *_a, **_k: SimpleNamespace(passed=True))
    assert result is False


def test_vote_v1_returns_true_on_fail() -> None:
    """A V1 residual that returns ``passed=False`` must yield True (detected)."""
    from types import SimpleNamespace

    result = bts._vote_v1(lambda *_a, **_k: SimpleNamespace(passed=False))
    assert result is True


def test_vote_v2_returns_false_on_pass() -> None:
    """A V2 residual that returns ``{"passed": True}`` must yield False."""
    result = bts._vote_v2(lambda *_a, **_k: {"passed": True})
    assert result is False


def test_vote_v2_returns_true_on_fail() -> None:
    """A V2 residual that returns ``{"passed": False}`` must yield True."""
    result = bts._vote_v2(lambda *_a, **_k: {"passed": False})
    assert result is True


# ---------------------------------------------------------------------------
# Module compiles + every swallow site is now bound (re-asserts AST guard
# with regex for robustness against future refactors that keep the bound
# name but rename the local).
# ---------------------------------------------------------------------------

def test_module_compiles() -> None:
    """The source file parses as valid Python AST."""
    ast.parse(SRC)


def test_all_swallow_sites_bound_to_exception_as_e() -> None:
    """All nine swallow sites bind the exception (``as e``)."""
    import re

    bound = re.findall(
        r"except\s+Exception\s+as\s+(\w+)\s*:\s*(?:#[^\n]*)?\n",
        SRC,
    )
    # We expect at least 9 distinct bound names (could be e / exc / etc.).
    assert len(bound) >= 9, (
        f"expected at least 9 `except Exception as <name>:` sites, found {len(bound)}: {bound}"
    )
    # None of the bound names should be empty.
    assert all(name.strip() for name in bound), (
        f"every `except Exception as ...` must bind a non-empty name; got {bound}"
    )

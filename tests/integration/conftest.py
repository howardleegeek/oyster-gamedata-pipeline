"""Shared fixtures and helpers for E2E integration tests.

Keep this minimal — integration tests build their own synthetic sessions
in ``tmp_path``. The fixtures here are limited to repo-wide discovery
(``repo_root``) and dependency probes (``has_ffmpeg``, ``has_openexr``,
``has_openpyxl``).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the oyster-agent-runner repo root."""
    return Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def _ensure_repo_on_path(repo_root: Path) -> None:
    """Make ``bin.*`` and ``src/oyster_agent_runner`` importable without install.

    ``pyproject.toml`` already lists `pythonpath = [".", "src"]` but
    standalone ``pytest tests/integration/...`` invocations from outside
    the repo can drop that, so we belt-and-suspenders here.
    """
    repo_str = str(repo_root)
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)
    src_str = str(repo_root / "src")
    if src_str not in sys.path:
        sys.path.insert(0, src_str)


@pytest.fixture(scope="session")
def has_ffmpeg() -> bool:
    """True if ffmpeg is on PATH (needed for synthetic video generation)."""
    return shutil.which("ffmpeg") is not None


@pytest.fixture(scope="session")
def has_ffprobe() -> bool:
    """True if ffprobe is on PATH (needed for video frame-count probing)."""
    return shutil.which("ffprobe") is not None


@pytest.fixture(scope="session")
def has_openexr() -> bool:
    """True if the OpenEXR Python module is importable.

    Without OpenEXR, depth EXR files are written as size-padded stubs;
    lint criterion 15 falls back to a "file size >= 50KB" check.
    """
    try:
        import OpenEXR  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.fixture(scope="session")
def has_openpyxl() -> bool:
    """True if openpyxl is importable (needed for gameinfo.xlsx)."""
    try:
        import openpyxl  # noqa: F401

        return True
    except ImportError:
        return False

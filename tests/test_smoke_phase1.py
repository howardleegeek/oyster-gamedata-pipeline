"""Tests for bin/smoke_phase1.sh — the automated Phase 1 §6 runbook.

These tests intentionally do not exercise the full Paper + run-mc path
(that requires Java + Node + a downloaded server jar). Instead they cover:

1. The script exists and is executable.
2. ``--help`` exits 0 (so CI can introspect the script without side effects).
3. The graceful-skip behaviour when java/node/npm are absent — verified by
   running the script with a sanitised PATH that contains none of them.
4. ``--dry-run`` mode runs the script's plumbing end-to-end without
   launching Paper or invoking the Python coordinator. This is the test
   we lean on most heavily for CI smoke coverage of the script itself.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "bin" / "smoke_phase1.sh"


def _run(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    """Run smoke_phase1.sh with the given args, capturing output."""
    return subprocess.run(
        [str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        env=env if env is not None else os.environ.copy(),
        timeout=timeout,
        check=False,
    )


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT_PATH.is_file(), f"missing script: {SCRIPT_PATH}"
    mode = SCRIPT_PATH.stat().st_mode
    assert mode & stat.S_IXUSR, "script is not user-executable"


def test_help_exits_zero() -> None:
    result = _run(["--help"])
    assert result.returncode == 0, result.stderr
    # Sanity: usage text mentions the script and the key flags.
    assert "smoke_phase1.sh" in result.stdout
    assert "--dry-run" in result.stdout
    assert "--no-download" in result.stdout


def test_skips_gracefully_when_tools_missing(tmp_path: Path) -> None:
    """With a PATH that contains no java/node/npm, the script must print a
    skip message and exit 0 (informational, not a hard fail).

    We simulate the missing-tools state by handing the script an empty
    directory as PATH. We deliberately keep core /usr/bin and /bin out of
    PATH so ``command -v java`` returns false even on machines that ship
    the JDK at /usr/bin/java.
    """
    sandbox_bin = tmp_path / "bin"
    sandbox_bin.mkdir()
    # The script's shebang is `#!/usr/bin/env bash`, so bash must be on
    # PATH or env(1) cannot find the interpreter. We symlink bash (and
    # nothing else) — the tool-detection block uses `command -v`, which is
    # a bash builtin, so it does not need java/node/npm to be physically
    # absent from the filesystem; it only needs them missing from PATH.
    bash_path = shutil.which("bash") or "/bin/bash"
    (sandbox_bin / "bash").symlink_to(bash_path)
    env = {
        "PATH": str(sandbox_bin),
        "HOME": os.environ.get("HOME", str(tmp_path)),
    }
    result = _run([], env=env)
    assert result.returncode == 0, (
        f"expected graceful skip (rc=0), got rc={result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "Phase 1 smoke skipped" in result.stdout
    # The skip line should name what's missing so operators can fix it.
    assert "java" in result.stdout
    assert "node" in result.stdout
    assert "npm" in result.stdout


@pytest.mark.skipif(
    not all(shutil.which(t) for t in ("java", "node", "npm")),
    reason="dry-run still requires java+node+npm on PATH so the skip branch isn't taken",
)
def test_dry_run_succeeds_without_launching_paper() -> None:
    """``--dry-run`` should walk through detection + cache + npm-install
    branches but never actually launch Paper or invoke the Python CLI."""
    result = _run(["--dry-run"], timeout=120)
    # Plain --dry-run: detection runs, cache check prints a synthetic
    # "would download" line if the jar is absent (no actual network call
    # because we're in dry-run), npm-install branch is logged-only, and
    # the launch + run-mc steps are skipped entirely. Result: rc=0.
    assert result.returncode == 0, (
        f"dry-run failed rc={result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "PHASE 1 SMOKE DRY-RUN OK" in result.stdout
    # Sanity: dry-run must NOT have produced a Paper runtime dir with a
    # real eula.txt, because the launch step is the *only* place that
    # writes those files.
    runtime_eula = REPO_ROOT / "bin" / ".cache" / "paper-runtime" / "eula.txt"
    assert not runtime_eula.exists(), (
        "dry-run leaked a paper-runtime/eula.txt — launch path ran when it shouldn't have"
    )

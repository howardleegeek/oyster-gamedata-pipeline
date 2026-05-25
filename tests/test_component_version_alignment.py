from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDER_SUBMODULE_COMMIT = "e171f20cf27aeaea1ac2f1b63434d7e2a1e09f61"


def test_recorder_submodule_is_pinned_to_release_buildable_commit() -> None:
    submodule_git = REPO_ROOT / "vendor" / "recorder" / ".git"
    if submodule_git.exists():
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT / "vendor" / "recorder",
            capture_output=True,
            check=True,
            text=True,
        )
        assert result.stdout.strip() == RECORDER_SUBMODULE_COMMIT
        return

    result = subprocess.run(
        ["git", "ls-tree", "HEAD", "vendor/recorder"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
    )

    assert RECORDER_SUBMODULE_COMMIT in result.stdout


def test_version_alignment_doc_distinguishes_release_from_source() -> None:
    doc = (REPO_ROOT / "docs" / "RECORDER_PIPELINE_CONTRACT.md").read_text(encoding="utf-8")

    assert "Latest GitHub release remains `v0.11.13`" in doc
    assert "Latest recorder release remains `v2.6.0`" in doc
    assert "`vendor/recorder` is pinned to release-buildable commit `e171f20`" in doc
    assert "no-popup runtime guards" in doc
    assert "docs/RELEASE_CHANNELS.md" in doc

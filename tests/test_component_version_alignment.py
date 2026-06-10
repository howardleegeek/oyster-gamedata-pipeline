from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDER_SUBMODULE_COMMIT = "7de8a38b881214f3fb617d0644e21a709eecf3df"


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
    from oyster_agent_runner.release_channels import (
        CURRENT_CONSUMER_INSTALLER,
        CURRENT_CONSUMER_TAG,
    )

    doc = (REPO_ROOT / "docs" / "RECORDER_PIPELINE_CONTRACT.md").read_text(encoding="utf-8")

    # Single source of truth: the doc must name the SAME consumer anchor as
    # release_channels.py so documentation drift fails loudly at test time.
    assert f"Latest GitHub release remains `{CURRENT_CONSUMER_TAG}`" in doc
    assert CURRENT_CONSUMER_INSTALLER in doc
    assert "Latest recorder release remains" in doc
    assert "`vendor/recorder` is pinned to current source candidate commit `7de8a38`" in doc
    assert "verified x64 recorder runtime" in doc
    assert "docs/RELEASE_CHANNELS.md" in doc

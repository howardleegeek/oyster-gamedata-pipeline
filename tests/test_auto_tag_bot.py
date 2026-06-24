"""Tests for scripts/auto_tag_bot.sh.

Tests cover:
  - YAML workflow validity
  - Commit-count threshold logic (configurable via env)
  - Dry-run mode does not push
  - Patch-only bump (never major/minor)
  - Release body includes git log + spec IDs
  - shellcheck on the script
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _shellcheck_available() -> bool:
    """Check if shellcheck is installed and actually functional."""
    sc = shutil.which("shellcheck")
    if sc is None:
        return False
    # Verify shellcheck actually works (not just exists as a stub)
    try:
        result = subprocess.run(
            ["shellcheck", "--version"],
            capture_output=True,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        # shellcheck exists but can't be executed (e.g., stub file)
        return False


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "auto_tag_bot.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "auto-tag-on-merge.yml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_script(
    env: dict | None = None,
    cwd: Path | None = None,
    expect_fail: bool = False,
) -> subprocess.CompletedProcess:
    """Run the auto_tag_bot.sh script with the given env overrides."""
    full_env = os.environ.copy()
    # Provide a fake GITHUB_TOKEN so the pre-flight check passes
    full_env["GITHUB_TOKEN"] = "ghp_fake_token_for_testing"
    if env:
        full_env.update(env)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=full_env,
        cwd=cwd or REPO_ROOT,
    )
    if not expect_fail:
        assert result.returncode == 0, (
            f"Script failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def _init_test_repo(tmp_path: Path, tag: str | None = None, commits: int = 0) -> Path:
    """Create a minimal git repo with optional tag and N commits."""
    repo = tmp_path / "test-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        capture_output=True,
        check=True,
    )

    if tag:
        # Create initial commit + tag
        (repo / "init.txt").write_text("init")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "initial commit S01"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "tag", "-a", tag, "-m", f"Tag {tag}"],
            cwd=repo,
            capture_output=True,
            check=True,
        )

    for i in range(commits):
        (repo / f"file_{i}.txt").write_text(f"content {i}")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"commit {i} S{90 + i}"],
            cwd=repo,
            capture_output=True,
            check=True,
        )

    return repo


# ---------------------------------------------------------------------------
# YAML validity
# ---------------------------------------------------------------------------


class TestYamlValidity:
    def test_workflow_is_valid_yaml(self):
        """The workflow file must parse as valid YAML."""
        yaml = pytest.importorskip("yaml")
        with open(WORKFLOW) as f:
            data = yaml.safe_load(f)
        assert data is not None
        assert True in data or "on" in data  # 'on' becomes True in PyYAML
        assert "jobs" in data

    def test_workflow_has_push_trigger_on_main(self):
        """Workflow must trigger on push to main."""
        yaml = pytest.importorskip("yaml")
        with open(WORKFLOW) as f:
            data = yaml.safe_load(f)
        # PyYAML parses 'on' as True (boolean)
        trigger_block = data.get(True) or data.get("on") or {}
        push_cfg = trigger_block.get("push", {})
        branches = push_cfg.get("branches", [])
        assert "main" in branches

    def test_workflow_has_commit_threshold_env(self):
        """COMMIT_THRESHOLD must be configurable via env."""
        content = WORKFLOW.read_text()
        assert "COMMIT_THRESHOLD" in content

    def test_workflow_has_contents_write_permission(self):
        """Workflow needs contents:write to push tags."""
        yaml = pytest.importorskip("yaml")
        with open(WORKFLOW) as f:
            data = yaml.safe_load(f)
        perms = data.get("permissions", {})
        assert perms.get("contents") == "write"


# ---------------------------------------------------------------------------
# Commit-count threshold
# ---------------------------------------------------------------------------


class TestCommitThreshold:
    def test_below_threshold_no_tag(self, tmp_path):
        """If commits < threshold, script exits cleanly without tagging."""
        repo = _init_test_repo(tmp_path, tag="v0.6.2", commits=2)
        result = _run_script(
            env={"COMMIT_THRESHOLD": "3", "DRY_RUN": "true"},
            cwd=repo,
        )
        assert "below threshold" in result.stdout.lower() or "skipping" in result.stdout.lower()

    def test_at_threshold_triggers(self, tmp_path):
        """If commits == threshold, script proceeds."""
        repo = _init_test_repo(tmp_path, tag="v0.6.2", commits=3)
        result = _run_script(
            env={"COMMIT_THRESHOLD": "3", "DRY_RUN": "true"},
            cwd=repo,
        )
        assert "v0.6.3" in result.stdout

    def test_above_threshold_triggers(self, tmp_path):
        """If commits > threshold, script proceeds."""
        repo = _init_test_repo(tmp_path, tag="v0.6.2", commits=5)
        result = _run_script(
            env={"COMMIT_THRESHOLD": "3", "DRY_RUN": "true"},
            cwd=repo,
        )
        assert "v0.6.3" in result.stdout

    def test_custom_threshold(self, tmp_path):
        """COMMIT_THRESHOLD env var is respected."""
        repo = _init_test_repo(tmp_path, tag="v0.6.2", commits=4)
        # With threshold=5, 4 commits should NOT trigger
        result = _run_script(
            env={"COMMIT_THRESHOLD": "5", "DRY_RUN": "true"},
            cwd=repo,
        )
        assert "below threshold" in result.stdout.lower() or "skipping" in result.stdout.lower()

    def test_default_threshold_is_3(self, tmp_path):
        """Default threshold is 3 when COMMIT_THRESHOLD is not set."""
        repo = _init_test_repo(tmp_path, tag="v0.6.2", commits=2)
        result = _run_script(
            env={"DRY_RUN": "true"},
            cwd=repo,
        )
        assert "below threshold" in result.stdout.lower() or "skipping" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_does_not_push(self, tmp_path):
        """DRY_RUN=true must not create or push any tag."""
        repo = _init_test_repo(tmp_path, tag="v0.6.2", commits=3)
        result = _run_script(
            env={"DRY_RUN": "true"},
            cwd=repo,
        )
        assert "DRY_RUN=true" in result.stdout
        assert "skipping" in result.stdout.lower()

        # Verify no new tags were created locally
        tags = (
            subprocess.run(
                ["git", "tag", "-l"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            )
            .stdout.strip()
            .splitlines()
        )
        assert "v0.6.3" not in tags

    def test_dry_run_shows_would_create(self, tmp_path):
        """Dry-run output mentions what would be created."""
        repo = _init_test_repo(tmp_path, tag="v0.6.2", commits=3)
        result = _run_script(
            env={"DRY_RUN": "true"},
            cwd=repo,
        )
        assert "Would create tag" in result.stdout


# ---------------------------------------------------------------------------
# Patch-only bump
# ---------------------------------------------------------------------------


class TestPatchOnlyBump:
    def test_bumps_patch(self, tmp_path):
        """v0.6.2 → v0.6.3"""
        repo = _init_test_repo(tmp_path, tag="v0.6.2", commits=3)
        result = _run_script(
            env={"DRY_RUN": "true"},
            cwd=repo,
        )
        assert "v0.6.3" in result.stdout
        assert "v0.7.0" not in result.stdout
        assert "v1.0.0" not in result.stdout

    def test_no_major_bump_even_with_breaking(self, tmp_path):
        """Even with BREAKING CHANGE commits, only patch is bumped."""
        repo = _init_test_repo(tmp_path, tag="v1.2.3", commits=0)
        # Add a commit with BREAKING CHANGE
        (repo / "breaking.txt").write_text("breaking")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "BREAKING CHANGE: remove API S99"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        # Add more commits to reach threshold
        for i in range(2):
            (repo / f"extra_{i}.txt").write_text(f"extra {i}")
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"extra commit {i}"],
                cwd=repo,
                capture_output=True,
                check=True,
            )

        result = _run_script(
            env={"DRY_RUN": "true"},
            cwd=repo,
        )
        # Should be v1.2.4 (patch bump), NOT v2.0.0
        assert "v1.2.4" in result.stdout
        assert "v2.0.0" not in result.stdout

    def test_no_minor_bump_even_with_feat(self, tmp_path):
        """Even with feat: commits, only patch is bumped."""
        repo = _init_test_repo(tmp_path, tag="v2.5.1", commits=0)
        for i in range(3):
            (repo / f"feat_{i}.txt").write_text(f"feat {i}")
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"feat: add feature {i} S{80 + i}"],
                cwd=repo,
                capture_output=True,
                check=True,
            )

        result = _run_script(
            env={"DRY_RUN": "true"},
            cwd=repo,
        )
        # Should be v2.5.2 (patch bump), NOT v2.6.0
        assert "v2.5.2" in result.stdout
        assert "v2.6.0" not in result.stdout

    def test_uses_highest_semver_tag_as_version_base(self, tmp_path):
        """Use the highest SemVer tag as the version base, even with an older range tag."""
        repo = _init_test_repo(tmp_path, tag="v0.6.2", commits=3)
        subprocess.run(
            ["git", "tag", "-a", "v0.6.3", "-m", "existing release"],
            cwd=repo,
            capture_output=True,
            check=True,
        )

        result = _run_script(
            env={"DRY_RUN": "true", "LATEST_TAG": "v0.6.2"},
            cwd=repo,
        )
        assert "New version: v0.6.4 (from v0.6.3)" in result.stdout
        assert "Would create tag: v0.6.4" in result.stdout


# ---------------------------------------------------------------------------
# Release body content
# ---------------------------------------------------------------------------


class TestReleaseBody:
    def test_body_includes_git_log(self, tmp_path):
        """Release body must include git log output."""
        repo = _init_test_repo(tmp_path, tag="v0.6.2", commits=3)
        result = _run_script(
            env={"DRY_RUN": "true"},
            cwd=repo,
        )
        # The body should contain commit info
        assert "Commits" in result.stdout

    def test_body_includes_spec_ids(self, tmp_path):
        """Release body must include spec IDs parsed from commit messages."""
        repo = _init_test_repo(tmp_path, tag="v0.6.2", commits=0)
        # Add commits with spec IDs
        for i, sid in enumerate(["S93", "S94", "S95"]):
            (repo / f"spec_{i}.txt").write_text(f"spec {i}")
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"work on {sid}"],
                cwd=repo,
                capture_output=True,
                check=True,
            )

        result = _run_script(
            env={"DRY_RUN": "true"},
            cwd=repo,
        )
        assert "S93" in result.stdout
        assert "S94" in result.stdout
        assert "S95" in result.stdout
        assert "Spec IDs" in result.stdout

    def test_body_has_version_header(self, tmp_path):
        """Release body includes version and date header."""
        repo = _init_test_repo(tmp_path, tag="v0.6.2", commits=3)
        result = _run_script(
            env={"DRY_RUN": "true"},
            cwd=repo,
        )
        # Should contain the version in the body
        assert "0.6.3" in result.stdout


# ---------------------------------------------------------------------------
# No previous tag
# ---------------------------------------------------------------------------


class TestNoPreviousTag:
    def test_no_tag_uses_v0_0_0_base(self, tmp_path):
        """When no tag exists, start from v0.0.0 and bump to v0.0.1."""
        repo = _init_test_repo(tmp_path, tag=None, commits=3)
        result = _run_script(
            env={"DRY_RUN": "true"},
            cwd=repo,
        )
        assert "v0.0.1" in result.stdout


# ---------------------------------------------------------------------------
# shellcheck
# ---------------------------------------------------------------------------


class TestShellcheck:
    @pytest.mark.skipif(not _shellcheck_available(), reason="shellcheck not available")
    def test_shellcheck_passes(self):
        """scripts/auto_tag_bot.sh must pass shellcheck."""
        result = subprocess.run(
            ["shellcheck", str(SCRIPT)],
            capture_output=True,
            text=True,
        )
        # shellcheck returns 0 on success, non-zero on findings
        assert result.returncode == 0, f"shellcheck found issues:\n{result.stdout}\n{result.stderr}"

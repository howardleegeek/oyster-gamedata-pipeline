"""Tests for scripts/auto_release.sh — SemVer bump logic via mocked git log."""

import shutil
import subprocess
import textwrap
from pathlib import Path


def _run_script(git_log_output, latest_tag="v0.4.1", dry_run="true", extra_tags=None):
    """
    Run auto_release.sh in a temporary git repo with mocked git log output.

    We override `git log` by creating a wrapper script that returns the
    desired output, and put it first in PATH.
    """
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        # Initialise a git repo
        subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmpdir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmpdir,
            capture_output=True,
            check=True,
        )

        # Create a dummy CHANGELOG.md if latest_tag is set
        if latest_tag:
            changelog = textwrap.dedent("""\
                # Changelog

                All notable changes to this project will be documented in this file.
                """)
            with open(os.path.join(tmpdir, "CHANGELOG.md"), "w") as f:
                f.write(changelog)
            subprocess.run(
                ["git", "add", "CHANGELOG.md"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "tag", latest_tag],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )

        # Add a dummy commit so there's something after the tag
        with open(os.path.join(tmpdir, "dummy.txt"), "w") as f:
            f.write("dummy")
        subprocess.run(
            ["git", "add", "dummy.txt"],
            cwd=tmpdir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "dummy commit"],
            cwd=tmpdir,
            capture_output=True,
            check=True,
        )
        for tag in extra_tags or []:
            subprocess.run(
                ["git", "tag", tag],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )

        # Create a git wrapper that returns our mocked log output
        wrapper_dir = os.path.join(tmpdir, "bin")
        os.makedirs(wrapper_dir)
        wrapper_path = os.path.join(wrapper_dir, "git")
        with open(wrapper_path, "w") as f:
            f.write(textwrap.dedent(f"""\
                    #!/usr/bin/env bash
                    if [[ "$*" == *"log"*"--format"* ]]; then
                        echo '{git_log_output}'
                    else
                        /usr/bin/git "$@"
                    fi
                    """))
        os.chmod(wrapper_path, 0o755)

        # Copy the script into the temp repo
        script_src = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts",
            "auto_release.sh",
        )
        script_dst = os.path.join(tmpdir, "scripts", "auto_release.sh")
        os.makedirs(os.path.dirname(script_dst), exist_ok=True)
        shutil.copy2(script_src, script_dst)
        os.chmod(script_dst, 0o755)

        env = os.environ.copy()
        env["PATH"] = wrapper_dir + ":" + env["PATH"]
        env["GITHUB_TOKEN"] = "fake-token-for-testing"
        env["DRY_RUN"] = dry_run
        env["LATEST_TAG"] = latest_tag

        result = subprocess.run(
            ["bash", script_dst],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            env=env,
        )

        return result


def _script_text() -> str:
    repo_root = Path(__file__).resolve().parent.parent
    return (repo_root / "scripts" / "auto_release.sh").read_text()


class TestSemVerPatchBump:
    """Patch bump when no feat: or BREAKING CHANGE."""

    def test_patch_bump_from_commits(self):
        log_output = (
            "abc1234 fix: resolve memory leak in parser\n"
            "def5678 chore: update dependencies\n"
            "ghi9012 docs: add API reference\n"
        )
        result = _run_script(log_output, latest_tag="v0.4.1")
        assert result.returncode == 0
        assert "v0.4.2" in result.stdout

    def test_patch_bump_single_commit(self):
        log_output = "abc1234 refactor: clean up utils module\n"
        result = _run_script(log_output, latest_tag="v0.4.1")
        assert result.returncode == 0
        assert "v0.4.2" in result.stdout


class TestSemVerMinorBump:
    """Minor bump when feat: commit is present."""

    def test_minor_bump_from_feat(self):
        log_output = "abc1234 feat: add new search endpoint\n" "def5678 fix: handle null response\n"
        result = _run_script(log_output, latest_tag="v0.4.1")
        assert result.returncode == 0
        assert "v0.5.0" in result.stdout

    def test_minor_bump_feat_with_scope(self):
        log_output = "abc1234 feat(api): add pagination support\n"
        result = _run_script(log_output, latest_tag="v0.4.1")
        assert result.returncode == 0
        assert "v0.5.0" in result.stdout

    def test_minor_bump_feat_at_end(self):
        log_output = "abc1234 fix: typo in readme\n" "def5678 feat: add dark mode toggle\n"
        result = _run_script(log_output, latest_tag="v0.4.1")
        assert result.returncode == 0
        assert "v0.5.0" in result.stdout


class TestSemVerMajorBump:
    """Major bump when BREAKING CHANGE is present."""

    def test_major_bump_from_breaking_change(self):
        log_output = "abc1234 feat!: redesign authentication flow\n" "def5678 fix: update tests\n"
        result = _run_script(log_output, latest_tag="v0.4.1")
        assert result.returncode == 0
        assert "v1.0.0" in result.stdout

    def test_major_bump_breaking_change_footer(self):
        log_output = (
            "abc1234 refactor: change API response format\n"
            "def5678 BREAKING CHANGE: new response schema\n"
        )
        result = _run_script(log_output, latest_tag="v0.4.1")
        assert result.returncode == 0
        assert "v1.0.0" in result.stdout

    def test_major_bump_resets_minor_and_patch(self):
        log_output = "abc1234 feat!: complete rewrite\n"
        result = _run_script(log_output, latest_tag="v2.7.3")
        assert result.returncode == 0
        assert "v3.0.0" in result.stdout


class TestDryRun:
    """Dry-run mode should not create real tags."""

    def test_dry_run_exits_cleanly(self):
        log_output = "abc1234 fix: minor bugfix\n"
        result = _run_script(log_output, latest_tag="v0.4.1", dry_run="true")
        assert result.returncode == 0
        assert "DRY_RUN=true" in result.stdout
        assert "skipping tag" in result.stdout

    def test_dry_run_shows_would_create_tag(self):
        log_output = "abc1234 feat: new feature\n"
        result = _run_script(log_output, latest_tag="v0.4.1", dry_run="true")
        assert result.returncode == 0
        assert "Would create tag: v0.5.0" in result.stdout


class TestChangelogFormat:
    """CHANGELOG.md should follow Keep-a-Changelog format."""

    def test_changelog_created_when_missing(self):
        # fix: triggers patch bump from v0.0.0 → v0.0.1
        log_output = "abc1234 fix: add logging\n"
        result = _run_script(log_output, latest_tag="")
        assert result.returncode == 0
        assert "v0.0.1" in result.stdout

    def test_changelog_has_added_section_for_feat(self):
        log_output = "abc1234 feat: add user profiles\n"
        result = _run_script(log_output, latest_tag="v0.4.1")
        assert result.returncode == 0
        # The script logs "CHANGELOG segment built"
        assert "CHANGELOG segment built" in result.stdout


class TestEdgeCases:
    """Edge cases for version parsing and bumping."""

    def test_version_with_double_digit_minor(self):
        log_output = "abc1234 feat: add something\n"
        result = _run_script(log_output, latest_tag="v0.10.5")
        assert result.returncode == 0
        assert "v0.11.0" in result.stdout

    def test_version_with_double_digit_patch(self):
        log_output = "abc1234 fix: something\n"
        result = _run_script(log_output, latest_tag="v1.2.15")
        assert result.returncode == 0
        assert "v1.2.16" in result.stdout

    def test_version_1_0_0_patch(self):
        log_output = "abc1234 fix: hotfix\n"
        result = _run_script(log_output, latest_tag="v1.0.0")
        assert result.returncode == 0
        assert "v1.0.1" in result.stdout

    def test_uses_highest_semver_tag_as_version_base(self):
        log_output = "abc1234 fix: avoid duplicate release tag\n"
        result = _run_script(
            log_output,
            latest_tag="v0.4.1",
            extra_tags=["v0.4.2"],
        )
        assert result.returncode == 0
        assert "New version: v0.4.3 (from v0.4.2)" in result.stdout
        assert "Would create tag: v0.4.3" in result.stdout


class TestInstallerAssetAttachment:
    """Automatic v* releases must keep the installer distribution path intact."""

    def test_release_script_attaches_latest_known_good_installer(self):
        script = _script_text()
        assert 'prepare_latest_installer_assets "$NEW_VERSION"' in script
        assert 'attach_latest_installer_assets "$NEW_VERSION"' in script
        assert "run_with_retries" in script
        assert "gh release list" in script
        assert "gh release download" in script
        assert "gh release upload" in script
        assert "OysterRecorder-[Ss]etup-*.exe" in script

    def test_release_script_prefetches_assets_before_git_state_changes(self):
        script = _script_text()
        assert script.index('prepare_latest_installer_assets "$NEW_VERSION"') < script.index(
            "git add CHANGELOG.md"
        )
        assert '"${INSTALLER_ASSET_FILES[@]}"' in script
        assert "directly to `gh release create`" in script

    def test_release_script_adds_installer_section_to_release_notes(self):
        script = _script_text()
        assert "installer_release_notes" in script
        assert 'RELEASE_BODY="${CHANGELOG_BODY}$(installer_release_notes "$NEW_VERSION")"' in script
        assert '--notes "$RELEASE_BODY"' in script
        assert "## Windows installer" in script
        assert "Windows SmartScreen may warn" in script
        assert "%LOCALAPPDATA%\\\\OysterRecorder\\\\" in script

    def test_release_script_uploads_sha256sums_with_installer(self):
        script = _script_text()
        assert "SHA256SUMS.txt" in script
        assert "hash_files" in script
        assert "sha256sum" in script
        assert "shasum -a 256" in script


class TestReleaseRaceHardening:
    """Manual and bot releases should not create duplicate local release commits."""

    def test_release_script_syncs_origin_before_bumping(self):
        script = _script_text()
        assert "sync_release_branch" in script
        assert 'git fetch origin "$branch"' in script
        assert 'git merge --ff-only "origin/${branch}"' in script
        assert "diverged from origin/${branch}" in script

    def test_release_script_checks_remote_tag_before_writing_changelog(self):
        script = _script_text()
        assert "remote_tag_exists" in script
        assert 'git ls-remote --exit-code --tags origin "refs/tags/$1"' in script
        assert 'if remote_tag_exists "$NEW_VERSION"; then' in script
        assert script.index('if remote_tag_exists "$NEW_VERSION"; then') < script.index(
            'prepare_latest_installer_assets "$NEW_VERSION"'
        )

    def test_release_script_uses_utc_changelog_date(self):
        script = _script_text()
        assert "TODAY=$(date -u +%Y-%m-%d)" in script

    def test_release_script_treats_concurrent_existing_release_as_complete(self):
        script = _script_text()
        assert "checking for concurrent release" in script
        assert 'gh release view "$NEW_VERSION"' in script
        assert "Concurrent release ${NEW_VERSION} already exists" in script
        assert "Could not push release commit for ${NEW_VERSION}" in script

    def test_release_script_syncs_source_anchor_before_tagging(self):
        script = _script_text()
        assert "sync_release_anchor_files" in script
        assert 'sync_release_anchor_files "$CURRENT_VERSION" "$NEW_VERSION"' in script
        assert 'CURRENT_CONSUMER_TAG = "{target_tag}"' in script
        assert 'DEFAULT_RECORDER_VERSION = "{target_version}"' in script
        assert "tests/test_release_channels.py" in script
        assert script.index(
            'sync_release_anchor_files "$CURRENT_VERSION" "$NEW_VERSION"'
        ) < script.index("git tag -a")
        assert script.index("git_add_if_exists") < script.index("git commit -m")

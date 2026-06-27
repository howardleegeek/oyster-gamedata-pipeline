"""Tests for scripts/upload_release_asset.sh — mocked gh CLI."""

import os
import shutil
import subprocess
import textwrap


def _create_mock_gh(tmpdir, mock_output="", mock_exit=0):
    """Create a mock `gh` script that records calls and returns mock output."""
    mock_gh = os.path.join(tmpdir, "gh")
    with open(mock_gh, "w") as f:
        f.write(
            textwrap.dedent(f"""\
            #!/usr/bin/env bash
            # Mock gh CLI — records invocations to a log file
            LOGFILE="{tmpdir}/gh_calls.log"
            echo "$*" >> "$LOGFILE"
            exit {mock_exit}
        """)
        )
    os.chmod(mock_gh, 0o755)
    return mock_gh


def _run_script(tag, artifact_dir, env_extra=None, mock_exit=0):
    """
    Run upload_release_asset.sh with a mocked gh CLI.

    Returns (returncode, stdout, stderr, gh_calls).
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create mock gh
        _create_mock_gh(tmpdir, mock_exit=mock_exit)

        # Create artifact directory with a dummy installer
        adir = os.path.join(tmpdir, artifact_dir)
        os.makedirs(adir, exist_ok=True)
        dummy_exe = os.path.join(adir, "OysterRecorder-setup-v1.2.3.exe")
        with open(dummy_exe, "wb") as f:
            f.write(b"MZ" + b"\x00" * 100)

        # Build environment
        env = os.environ.copy()
        env["PATH"] = tmpdir + ":" + env["PATH"]
        env["GH_TOKEN"] = "fake-token-for-testing"
        if env_extra:
            env.update(env_extra)

        script_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts",
            "upload_release_asset.sh",
        )

        result = subprocess.run(
            ["bash", script_path, tag, adir],
            capture_output=True,
            text=True,
            env=env,
        )

        # Read gh calls log
        gh_calls = ""
        logfile = os.path.join(tmpdir, "gh_calls.log")
        if os.path.exists(logfile):
            with open(logfile) as f:
                gh_calls = f.read()

        return result.returncode, result.stdout, result.stderr, gh_calls


class TestUploadReleaseAsset:
    """Test suite for upload_release_asset.sh."""

    def test_successful_upload(self):
        """Normal flow: finds installer, uploads it, uploads SHA256SUMS.txt."""
        rc, stdout, stderr, gh_calls = _run_script(
            tag="recorder-v1.2.3",
            artifact_dir="dist",
        )
        assert rc == 0, f"Script failed: stderr={stderr}"
        # Verify gh release upload was called for the installer
        assert "release upload" in gh_calls
        assert "OysterRecorder-setup-v1.2.3.exe" in gh_calls
        assert "--clobber" in gh_calls
        # Verify SHA256SUMS.txt was uploaded
        assert "SHA256SUMS.txt" in gh_calls

    def test_successful_upload_v_prefix_tag(self):
        """Tag with just v-prefix (no recorder- prefix) should also work."""
        rc, stdout, stderr, gh_calls = _run_script(
            tag="v2.0.0",
            artifact_dir="dist",
        )
        assert rc == 0, f"Script failed: stderr={stderr}"
        assert "release upload" in gh_calls

    def test_missing_tag_argument(self):
        """No tag argument should fail with usage message."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            _create_mock_gh(tmpdir)
            env = os.environ.copy()
            env["PATH"] = tmpdir + ":" + env["PATH"]
            env["GH_TOKEN"] = "fake-token"

            script_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "scripts",
                "upload_release_asset.sh",
            )

            result = subprocess.run(
                ["bash", script_path],
                capture_output=True,
                text=True,
                env=env,
            )
            assert result.returncode != 0
            assert "Usage" in result.stderr

    def test_invalid_tag_format(self):
        """Non-semver tag should be rejected."""
        rc, stdout, stderr, gh_calls = _run_script(
            tag="not-a-valid-tag",
            artifact_dir="dist",
        )
        assert rc != 0
        assert "does not look like a valid semver tag" in stderr

    def test_missing_gh_cli(self):
        """Script should fail if gh CLI is not in PATH."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            # No mock gh. Keep only the tools needed before the gh preflight.
            tool_bin = os.path.join(tmpdir, "bin")
            os.makedirs(tool_bin, exist_ok=True)
            grep_path = shutil.which("grep")
            assert grep_path is not None
            os.symlink(grep_path, os.path.join(tool_bin, "grep"))

            adir = os.path.join(tmpdir, "dist")
            os.makedirs(adir, exist_ok=True)
            dummy_exe = os.path.join(adir, "OysterRecorder-setup-v1.0.0.exe")
            with open(dummy_exe, "wb") as f:
                f.write(b"MZ")

            env = os.environ.copy()
            env["PATH"] = tool_bin
            env["GH_TOKEN"] = "fake-token"

            script_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "scripts",
                "upload_release_asset.sh",
            )

            result = subprocess.run(
                [shutil.which("bash") or "/bin/bash", script_path, "v1.0.0", adir],
                capture_output=True,
                text=True,
                env=env,
            )
            assert result.returncode != 0
            assert "gh CLI is required" in result.stderr

    def test_missing_gh_token(self):
        """Script should fail if GH_TOKEN is not set."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            _create_mock_gh(tmpdir)
            adir = os.path.join(tmpdir, "dist")
            os.makedirs(adir, exist_ok=True)
            dummy_exe = os.path.join(adir, "OysterRecorder-setup-v1.0.0.exe")
            with open(dummy_exe, "wb") as f:
                f.write(b"MZ")

            env = os.environ.copy()
            env["PATH"] = tmpdir + ":" + env["PATH"]
            env.pop("GH_TOKEN", None)

            script_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "scripts",
                "upload_release_asset.sh",
            )

            result = subprocess.run(
                ["bash", script_path, "v1.0.0", adir],
                capture_output=True,
                text=True,
                env=env,
            )
            assert result.returncode != 0
            assert "GH_TOKEN" in result.stderr

    def test_no_installer_found(self):
        """Script should fail if no installer exe exists in artifact dir."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            _create_mock_gh(tmpdir)
            adir = os.path.join(tmpdir, "dist")
            os.makedirs(adir, exist_ok=True)
            # No installer exe — just a random file
            with open(os.path.join(adir, "readme.txt"), "w") as f:
                f.write("nothing here")

            env = os.environ.copy()
            env["PATH"] = tmpdir + ":" + env["PATH"]
            env["GH_TOKEN"] = "fake-token"

            script_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "scripts",
                "upload_release_asset.sh",
            )

            result = subprocess.run(
                ["bash", script_path, "v1.0.0", adir],
                capture_output=True,
                text=True,
                env=env,
            )
            assert result.returncode != 0
            assert "No OysterRecorder-setup" in result.stderr

    def test_gh_release_upload_called_with_clobber(self):
        """Verify --clobber flag is passed to gh release upload."""
        rc, stdout, stderr, gh_calls = _run_script(
            tag="recorder-v3.0.0",
            artifact_dir="dist",
        )
        assert rc == 0
        # Each call to gh should include --clobber
        for line in gh_calls.strip().split("\n"):
            if "release upload" in line:
                assert "--clobber" in line

    def test_sha256sums_generated(self):
        """Verify SHA256SUMS.txt is generated and uploaded."""
        rc, stdout, stderr, gh_calls = _run_script(
            tag="recorder-v1.0.0",
            artifact_dir="dist",
        )
        assert rc == 0
        assert "SHA256SUMS.txt" in gh_calls

    def test_multiple_installers(self):
        """If multiple installer variants exist, all should be uploaded."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            _create_mock_gh(tmpdir)
            adir = os.path.join(tmpdir, "dist")
            os.makedirs(adir, exist_ok=True)

            # Create two installer variants with distinct names
            # (macOS has case-insensitive FS, so use truly different names)
            for name in [
                "OysterRecorder-setup-v1.0.0.exe",
                "OysterRecorder-setup-v1.0.0-portable.exe",
            ]:
                with open(os.path.join(adir, name), "wb") as f:
                    f.write(b"MZ" + b"\x00" * 50)

            env = os.environ.copy()
            env["PATH"] = tmpdir + ":" + env["PATH"]
            env["GH_TOKEN"] = "fake-token"

            script_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "scripts",
                "upload_release_asset.sh",
            )

            result = subprocess.run(
                ["bash", script_path, "v1.0.0", adir],
                capture_output=True,
                text=True,
                env=env,
            )
            assert result.returncode == 0

            # Read gh calls
            logfile = os.path.join(tmpdir, "gh_calls.log")
            with open(logfile) as f:
                gh_calls = f.read()

            # Both installers should be uploaded
            assert "OysterRecorder-setup-v1.0.0.exe" in gh_calls
            assert "OysterRecorder-setup-v1.0.0-portable.exe" in gh_calls

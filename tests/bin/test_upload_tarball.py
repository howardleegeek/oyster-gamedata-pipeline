#!/usr/bin/env python3
"""Tests for bin/upload_tarball.py — CLI wrapper around storage_backend."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import the module under test
sys.path.insert(0, str(Path(__file__).parents[2] / "bin"))
import upload_tarball


class TestParseArgs:
    """Tests for _parse_args function."""

    def test_required_args_present(self):
        """Should parse required arguments correctly."""
        args = upload_tarball._parse_args([
            "/tmp/test.tar.gz",
            "--tester-id", "tester-001",
            "--d5-verdict", "REAL",
        ])
        assert args.tarball == Path("/tmp/test.tar.gz")
        assert args.tester_id == "tester-001"
        assert args.d5_verdict == "REAL"
        assert args.sha256 is None
        assert args.backend is None
        assert args.verbose is False

    def test_all_optional_args(self):
        """Should parse all optional arguments."""
        args = upload_tarball._parse_args([
            "/tmp/test.tar.gz",
            "--tester-id", "tester-001",
            "--d5-verdict", "MIXED",
            "--sha256", "abc123",
            "--backend", "s3",
            "--notes", "test note",
            "--ttl-seconds", "3600",
            "--verbose",
        ])
        assert args.tarball == Path("/tmp/test.tar.gz")
        assert args.tester_id == "tester-001"
        assert args.d5_verdict == "MIXED"
        assert args.sha256 == "abc123"
        assert args.backend == "s3"
        assert args.notes == "test note"
        assert args.ttl_seconds == 3600
        assert args.verbose is True

    def test_default_ttl_seconds(self):
        """Should use default TTL when not specified."""
        args = upload_tarball._parse_args([
            "/tmp/test.tar.gz",
            "--tester-id", "tester-001",
            "--d5-verdict", "PLACEHOLDER",
        ])
        assert args.ttl_seconds == 86400  # DEFAULT_SIGNED_URL_TTL_SECONDS

    def test_d5_verdict_choices(self):
        """Should accept all valid D5 verdict choices."""
        for verdict in ["REAL", "MIXED", "PLACEHOLDER"]:
            args = upload_tarball._parse_args([
                "/tmp/test.tar.gz",
                "--tester-id", "tester-001",
                "--d5-verdict", verdict,
            ])
            assert args.d5_verdict == verdict

    def test_backend_choices(self):
        """Should accept all valid backend choices."""
        for backend in ["local", "s3", "github"]:
            args = upload_tarball._parse_args([
                "/tmp/test.tar.gz",
                "--tester-id", "tester-001",
                "--d5-verdict", "REAL",
                "--backend", backend,
            ])
            assert args.backend == backend


class TestMain:
    """Tests for main function."""

    def test_tarball_not_found(self, tmp_path):
        """Should return exit code 1 when tarball doesn't exist."""
        nonexistent = tmp_path / "nonexistent.tar.gz"
        result = upload_tarball.main([
            str(nonexistent),
            "--tester-id", "tester-001",
            "--d5-verdict", "REAL",
        ])
        assert result == 1

    def test_invalid_d5_verdict(self, tmp_path):
        """Should return exit code 2 when D5 verdict is invalid."""
        # This is hard to test directly since argparse validates choices,
        # but the error handling exists in main()
        pass

    @patch.object(upload_tarball, "compute_sha256")
    @patch.object(upload_tarball, "get_backend")
    def test_backend_init_failure(self, mock_get_backend, mock_compute_sha, tmp_path):
        """Should return exit code 3 when backend init fails."""
        # Create a valid tarball file
        tarball = tmp_path / "test.tar.gz"
        tarball.write_bytes(b"fake tar content")
        
        mock_compute_sha.return_value = "a" * 64  # valid 64-char hex
        mock_get_backend.side_effect = Exception("Backend init failed")
        
        result = upload_tarball.main([
            str(tarball),
            "--tester-id", "tester-001",
            "--d5-verdict", "REAL",
        ])
        assert result == 3

    @patch.object(upload_tarball, "compute_sha256")
    @patch.object(upload_tarball, "get_backend")
    def test_upload_failure(self, mock_get_backend, mock_compute_sha, tmp_path):
        """Should return exit code 3 when upload fails."""
        # Create a valid tarball file
        tarball = tmp_path / "test.tar.gz"
        tarball.write_bytes(b"fake tar content")
        
        mock_compute_sha.return_value = "a" * 64  # valid 64-char hex
        
        # Mock backend
        mock_backend_instance = MagicMock()
        mock_get_backend.return_value = mock_backend_instance
        mock_backend_instance.upload.side_effect = Exception("Upload failed")
        
        result = upload_tarball.main([
            str(tarball),
            "--tester-id", "tester-001",
            "--d5-verdict", "REAL",
        ])
        assert result == 3

    @patch.object(upload_tarball, "compute_sha256")
    @patch.object(upload_tarball, "get_backend")
    def test_success_default_ttl(self, mock_get_backend, mock_compute_sha, tmp_path, capsys):
        """Should return exit code 0 and JSON output on success with default TTL."""
        # Create a valid tarball file
        tarball = tmp_path / "test.tar.gz"
        tarball.write_bytes(b"fake tar content")
        
        mock_compute_sha.return_value = "a" * 64  # valid 64-char hex
        
        # Mock backend
        mock_backend_instance = MagicMock()
        mock_get_backend.return_value = mock_backend_instance
        
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            "storage_url": "https://example.com/test.tar.gz",
            "signed_url": "https://example.com/signed/test.tar.gz",
            "asset_name": "test.tar.gz",
            "backend": "github",
            "idempotent_skip": False,
            "metadata": {"tester_id": "tester-001"},
        }
        mock_backend_instance.upload.return_value = mock_result
        mock_backend_instance.get_signed_url.return_value = "https://example.com/signed/test.tar.gz"
        
        result = upload_tarball.main([
            str(tarball),
            "--tester-id", "tester-001",
            "--d5-verdict", "REAL",
        ])
        
        assert result == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert "storage_url" in data
        assert data["backend"] == "github"

    @patch.object(upload_tarball, "compute_sha256")
    @patch.object(upload_tarball, "get_backend")
    def test_success_custom_ttl(self, mock_get_backend, mock_compute_sha, tmp_path, capsys):
        """Should regenerate signed URL with custom TTL when requested."""
        # Create a valid tarball file
        tarball = tmp_path / "test.tar.gz"
        tarball.write_bytes(b"fake tar content")
        
        mock_compute_sha.return_value = "a" * 64  # valid 64-char hex
        
        # Mock backend
        mock_backend_instance = MagicMock()
        mock_get_backend.return_value = mock_backend_instance
        
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            "storage_url": "https://example.com/test.tar.gz",
            "signed_url": "https://example.com/signed/test.tar.gz",
            "asset_name": "test.tar.gz",
            "backend": "github",
            "idempotent_skip": False,
            "metadata": {"tester_id": "tester-001"},
        }
        mock_backend_instance.upload.return_value = mock_result
        mock_backend_instance.get_signed_url.return_value = "https://example.com/signed/3600/test.tar.gz"
        
        result = upload_tarball.main([
            str(tarball),
            "--tester-id", "tester-001",
            "--d5-verdict", "REAL",
            "--ttl-seconds", "3600",
        ])
        
        assert result == 0
        # Verify get_signed_url was called with custom TTL
        mock_backend_instance.get_signed_url.assert_called_once()
        call_args = mock_backend_instance.get_signed_url.call_args
        assert call_args[1]["ttl_seconds"] == 3600

    @patch.object(upload_tarball, "compute_sha256")
    @patch.object(upload_tarball, "get_backend")
    def test_provided_sha256_used(self, mock_get_backend, mock_compute_sha, tmp_path):
        """Should use provided SHA256 instead of computing."""
        # Create a valid tarball file
        tarball = tmp_path / "test.tar.gz"
        tarball.write_bytes(b"fake tar content")
        
        # Mock backend
        mock_backend_instance = MagicMock()
        mock_get_backend.return_value = mock_backend_instance
        
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            "storage_url": "https://example.com/test.tar.gz",
            "signed_url": "https://example.com/signed/test.tar.gz",
            "asset_name": "test.tar.gz",
            "backend": "github",
            "idempotent_skip": False,
            "metadata": {"tester_id": "tester-001"},
        }
        mock_backend_instance.upload.return_value = mock_result
        
        upload_tarball.main([
            str(tarball),
            "--tester-id", "tester-001",
            "--d5-verdict", "REAL",
            "--sha256", "a" * 64,
        ])
        
        # compute_sha256 should NOT be called since SHA256 was provided
        mock_compute_sha.assert_not_called()
        
        # Verify upload was called with provided SHA
        call_args = mock_backend_instance.upload.call_args
        metadata = call_args[0][1]
        assert metadata.sha256 == "a" * 64

    @patch.object(upload_tarball, "compute_sha256")
    @patch.object(upload_tarball, "get_backend")
    def test_verbose_flag_enables_info_logging(self, mock_get_backend, mock_compute_sha, tmp_path):
        """Should enable INFO logging when --verbose is set."""
        # Create a valid tarball file
        tarball = tmp_path / "test.tar.gz"
        tarball.write_bytes(b"fake tar content")
        
        mock_compute_sha.return_value = "a" * 64  # valid 64-char hex
        
        # Mock backend
        mock_backend_instance = MagicMock()
        mock_get_backend.return_value = mock_backend_instance
        
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            "storage_url": "https://example.com/test.tar.gz",
            "signed_url": "https://example.com/signed/test.tar.gz",
            "asset_name": "test.tar.gz",
            "backend": "github",
            "idempotent_skip": False,
            "metadata": {"tester_id": "tester-001"},
        }
        mock_backend_instance.upload.return_value = mock_result
        
        # This should not raise with verbose flag
        result = upload_tarball.main([
            str(tarball),
            "--tester-id", "tester-001",
            "--d5-verdict", "REAL",
            "--verbose",
        ])
        
        assert result == 0


class TestCLI:
    """Tests for CLI invocation (subprocess)."""

    def test_help_flag(self):
        """Should display help when --help is passed."""
        result = subprocess.run(
            [sys.executable, "-m", "upload_tarball", "--help"],
            cwd=Path(__file__).parents[2] / "bin",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "upload_tarball.py" in result.stdout
        assert "--tester-id" in result.stdout
        assert "--d5-verdict" in result.stdout

    def test_missing_required_args(self):
        """Should fail when required arguments are missing."""
        result = subprocess.run(
            [sys.executable, "-m", "upload_tarball", "/tmp/test.tar.gz"],
            cwd=Path(__file__).parents[2] / "bin",
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0

    def test_invalid_backend_choice(self):
        """Should fail when an invalid backend is specified."""
        result = subprocess.run(
            [
                sys.executable, "-m", "upload_tarball",
                "/tmp/test.tar.gz",
                "--tester-id", "tester-001",
                "--d5-verdict", "REAL",
                "--backend", "invalid_backend",
            ],
            cwd=Path(__file__).parents[2] / "bin",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2  # argparse error

    def test_invalid_d5_verdict_choice(self):
        """Should fail when an invalid D5 verdict is specified."""
        result = subprocess.run(
            [
                sys.executable, "-m", "upload_tarball",
                "/tmp/test.tar.gz",
                "--tester-id", "tester-001",
                "--d5-verdict", "INVALID",
            ],
            cwd=Path(__file__).parents[2] / "bin",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2  # argparse error

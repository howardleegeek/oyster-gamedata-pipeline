"""Tests for scripts/upload_to_r2.py."""

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# Ensure the scripts directory is importable
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import upload_to_r2  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_env():
    """Remove all R2-related env vars before each test."""
    for var in upload_to_r2.REQUIRED_ENV_VARS:
        os.environ.pop(var, None)
    yield
    for var in upload_to_r2.REQUIRED_ENV_VARS:
        os.environ.pop(var, None)


@pytest.fixture
def full_env():
    """Set all required R2 env vars."""
    env = {
        "R2_ACCESS_KEY": "test-access-key",
        "R2_SECRET": "test-secret",
        "R2_BUCKET": "test-bucket",
        "R2_ENDPOINT": "https://test.r2.cloudflarestorage.com",
    }
    for k, v in env.items():
        os.environ[k] = v
    yield env
    for k in env:
        os.environ.pop(k, None)


# ---------------------------------------------------------------------------
# validate_env tests
# ---------------------------------------------------------------------------


class TestValidateEnv:
    def test_all_vars_present(self, full_env):
        result = upload_to_r2.validate_env()
        assert result == full_env

    def test_missing_single_var(self, clean_env):
        os.environ["R2_ACCESS_KEY"] = "key"
        os.environ["R2_SECRET"] = "secret"
        os.environ["R2_BUCKET"] = "bucket"
        # R2_ENDPOINT is missing
        with pytest.raises(SystemExit) as exc_info:
            upload_to_r2.validate_env()
        assert exc_info.value.code == 1

    def test_missing_all_vars(self, clean_env):
        with pytest.raises(SystemExit) as exc_info:
            upload_to_r2.validate_env()
        assert exc_info.value.code == 1

    def test_missing_multiple_vars(self, clean_env):
        os.environ["R2_ACCESS_KEY"] = "key"
        # R2_SECRET, R2_BUCKET, R2_ENDPOINT missing
        with pytest.raises(SystemExit) as exc_info:
            upload_to_r2.validate_env()
        assert exc_info.value.code == 1

    def test_empty_string_treated_as_missing(self, clean_env):
        os.environ["R2_ACCESS_KEY"] = ""
        os.environ["R2_SECRET"] = "secret"
        os.environ["R2_BUCKET"] = "bucket"
        os.environ["R2_ENDPOINT"] = "https://example.com"
        with pytest.raises(SystemExit) as exc_info:
            upload_to_r2.validate_env()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# upload_to_r2 tests
# ---------------------------------------------------------------------------


class TestUploadToR2:
    @mock.patch("upload_to_r2.boto3.Session")
    def test_successful_upload(self, mock_session, full_env, tmp_path):
        # Create a dummy .exe file
        exe_file = tmp_path / "foo.exe"
        exe_file.write_bytes(b"MZ" + b"\x00" * 100)

        mock_client = mock.MagicMock()
        mock_session.return_value.client.return_value = mock_client

        url = upload_to_r2.upload_to_r2(
            file_path=str(exe_file),
            access_key=full_env["R2_ACCESS_KEY"],
            secret=full_env["R2_SECRET"],
            bucket=full_env["R2_BUCKET"],
            endpoint=full_env["R2_ENDPOINT"],
        )

        # Verify upload_file was called
        mock_client.upload_file.assert_called_once_with(
            str(exe_file),
            full_env["R2_BUCKET"],
            "foo.exe",
            ExtraArgs={"ContentType": "application/octet-stream"},
        )

        # Verify returned URL
        expected_url = f"{full_env['R2_ENDPOINT'].rstrip('/')}/{full_env['R2_BUCKET']}/foo.exe"
        assert url == expected_url

    @mock.patch("upload_to_r2.boto3.Session")
    def test_upload_nonexistent_file(self, mock_session, full_env):
        with pytest.raises(SystemExit) as exc_info:
            upload_to_r2.upload_to_r2(
                file_path="/nonexistent/path/foo.exe",
                access_key=full_env["R2_ACCESS_KEY"],
                secret=full_env["R2_SECRET"],
                bucket=full_env["R2_BUCKET"],
                endpoint=full_env["R2_ENDPOINT"],
            )
        assert exc_info.value.code == 1

    @mock.patch("upload_to_r2.boto3.Session")
    def test_upload_no_credentials_error(self, mock_session, full_env, tmp_path):
        from botocore.exceptions import NoCredentialsError

        exe_file = tmp_path / "bar.exe"
        exe_file.write_bytes(b"data")

        mock_client = mock.MagicMock()
        mock_client.upload_file.side_effect = NoCredentialsError()
        mock_session.return_value.client.return_value = mock_client

        with pytest.raises(SystemExit) as exc_info:
            upload_to_r2.upload_to_r2(
                file_path=str(exe_file),
                access_key=full_env["R2_ACCESS_KEY"],
                secret=full_env["R2_SECRET"],
                bucket=full_env["R2_BUCKET"],
                endpoint=full_env["R2_ENDPOINT"],
            )
        assert exc_info.value.code == 1

    @mock.patch("upload_to_r2.boto3.Session")
    def test_upload_client_error(self, mock_session, full_env, tmp_path):
        from botocore.exceptions import ClientError

        exe_file = tmp_path / "baz.exe"
        exe_file.write_bytes(b"data")

        error_response = {"Error": {"Code": "AccessDenied", "Message": "Denied"}}
        mock_client = mock.MagicMock()
        mock_client.upload_file.side_effect = ClientError(error_response, "PutObject")
        mock_session.return_value.client.return_value = mock_client

        with pytest.raises(SystemExit) as exc_info:
            upload_to_r2.upload_to_r2(
                file_path=str(exe_file),
                access_key=full_env["R2_ACCESS_KEY"],
                secret=full_env["R2_SECRET"],
                bucket=full_env["R2_BUCKET"],
                endpoint=full_env["R2_ENDPOINT"],
            )
        assert exc_info.value.code == 1

    @mock.patch("upload_to_r2.boto3.Session")
    def test_session_created_with_correct_creds(self, mock_session, full_env, tmp_path):
        exe_file = tmp_path / "test.exe"
        exe_file.write_bytes(b"data")

        mock_client = mock.MagicMock()
        mock_session.return_value.client.return_value = mock_client

        upload_to_r2.upload_to_r2(
            file_path=str(exe_file),
            access_key=full_env["R2_ACCESS_KEY"],
            secret=full_env["R2_SECRET"],
            bucket=full_env["R2_BUCKET"],
            endpoint=full_env["R2_ENDPOINT"],
        )

        mock_session.assert_called_once_with(
            aws_access_key_id=full_env["R2_ACCESS_KEY"],
            aws_secret_access_key=full_env["R2_SECRET"],
        )

    @mock.patch("upload_to_r2.boto3.Session")
    def test_client_configured_with_endpoint(self, mock_session, full_env, tmp_path):
        exe_file = tmp_path / "test.exe"
        exe_file.write_bytes(b"data")

        mock_client = mock.MagicMock()
        mock_session.return_value.client.return_value = mock_client

        upload_to_r2.upload_to_r2(
            file_path=str(exe_file),
            access_key=full_env["R2_ACCESS_KEY"],
            secret=full_env["R2_SECRET"],
            bucket=full_env["R2_BUCKET"],
            endpoint=full_env["R2_ENDPOINT"],
        )

        mock_session.return_value.client.assert_called_once()
        call_kwargs = mock_session.return_value.client.call_args
        assert call_kwargs[0][0] == "s3"
        assert call_kwargs[1]["endpoint_url"] == full_env["R2_ENDPOINT"]


# ---------------------------------------------------------------------------
# main() / CLI tests
# ---------------------------------------------------------------------------


class TestMain:
    @mock.patch("upload_to_r2.upload_to_r2")
    @mock.patch("upload_to_r2.validate_env")
    def test_main_calls_upload(self, mock_validate, mock_upload, full_env, tmp_path):
        exe_file = tmp_path / "app.exe"
        exe_file.write_bytes(b"MZ")

        mock_validate.return_value = full_env
        mock_upload.return_value = "https://example.com/bucket/app.exe"

        with mock.patch.object(sys, "argv", ["upload_to_r2.py", "--file", str(exe_file)]):
            upload_to_r2.main()

        mock_upload.assert_called_once()

    def test_main_missing_file_arg(self, full_env):
        with mock.patch.object(sys, "argv", ["upload_to_r2.py"]):
            with pytest.raises(SystemExit) as exc_info:
                upload_to_r2.main()
            assert exc_info.value.code == 2  # argparse error

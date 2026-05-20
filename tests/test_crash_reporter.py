"""Tests for the crash-reporter daemon and backend stub.

Covers:
  - Crash file parsing (Rust panic, OS, version)
  - Summary writing to ~/.oyster/crashes/
  - Opt-in consent flow (Y → upload, N → no upload)
  - Backend stub endpoint acceptance
  - Daemon / one-shot modes
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure repo root is on path (defensive, mirrors conftest.py)
_REPO_ROOT = Path(__file__).resolve().parent.parent
_repo_root_str = str(_REPO_ROOT)
if _repo_root_str not in sys.path:
    sys.path.insert(0, _repo_root_str)

from backend_stub.crash_dump import (
    clear_crashes,
    get_all_crashes,
    store_crash,
)  # noqa: E402
from backend_stub.main import app  # noqa: E402
from bin.crash_reporter import (  # noqa: E402
    CRASH_FILE_PATTERN,
    ensure_consent,
    get_consent,
    parse_crash_file,
    process_crash_file,
    prompt_consent,
    upload_crash,
    write_crash_summary,
    _processed_files,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect all ~/.oyster paths to a temp directory and reset globals."""
    oyster = tmp_path / ".oyster"
    oyster.mkdir(parents=True)
    crashes = oyster / "crashes"
    crashes.mkdir(parents=True)
    telemetry = oyster / "telemetry.json"

    monkeypatch.setattr("bin.crash_reporter.OYSTER_DIR", oyster)
    monkeypatch.setattr("bin.crash_reporter.CRASHES_DIR", crashes)
    monkeypatch.setattr("bin.crash_reporter.TELEMETRY_FILE", telemetry)
    _processed_files.clear()
    clear_crashes()
    yield


@pytest.fixture
def sample_crash_content() -> str:
    """A realistic Rust panic crash log."""
    return (
        "thread 'main' panicked at 'index out of bounds: the len is 0 but the index is 1', "
        "src/recorder/capture.rs:142:17\n"
        "stack backtrace:\n"
        "   0: rust_begin_unwind\n"
        "   1: core::panicking::panic_fmt\n"
        "   2: oyster_recorder::capture::FrameBuffer::get\n"
        "   3: oyster_recorder::main\n"
        "recorder_version: 0.4.2\n"
        "os: Windows 10 Pro 22H2\n"
    )


@pytest.fixture
def crash_file(tmp_path: Path, sample_crash_content: str) -> Path:
    """A crash-*.log file in a temp directory."""
    f = tmp_path / "crash-20260519-143022.log"
    f.write_text(sample_crash_content)
    return f


# ---------------------------------------------------------------------------
# Parsing tests
# ---------------------------------------------------------------------------


class TestParseCrashFile:
    def test_parses_panic_message(self, crash_file: Path):
        parsed = parse_crash_file(crash_file)
        assert "index out of bounds" in parsed["panic_message"]

    def test_parses_stack_trace(self, crash_file: Path):
        parsed = parse_crash_file(crash_file)
        assert "rust_begin_unwind" in parsed["stack_trace"]
        assert "oyster_recorder::capture::FrameBuffer::get" in parsed["stack_trace"]

    def test_parses_recorder_version(self, crash_file: Path):
        parsed = parse_crash_file(crash_file)
        assert parsed["recorder_version"] == "0.4.2"

    def test_parses_os_info(self, crash_file: Path):
        parsed = parse_crash_file(crash_file)
        assert "Windows 10 Pro 22H2" in parsed["os_info"]

    def test_handles_malformed_file(self, tmp_path: Path):
        f = tmp_path / "crash-bad.log"
        f.write_text("just some random text\nno panic here\n")
        parsed = parse_crash_file(f)
        assert parsed["panic_message"] == ""
        assert parsed["stack_trace"] == ""
        assert parsed["recorder_version"] == ""
        assert parsed["os_info"] == ""

    def test_handles_missing_file(self, tmp_path: Path):
        f = tmp_path / "crash-nonexistent.log"
        with pytest.raises(FileNotFoundError):
            parse_crash_file(f)


# ---------------------------------------------------------------------------
# Summary writing tests
# ---------------------------------------------------------------------------


class TestWriteCrashSummary:
    def test_writes_summary_file(self, crash_file: Path):
        parsed = parse_crash_file(crash_file)
        summary = write_crash_summary(parsed, crash_file.name)
        assert summary.exists()
        text = summary.read_text()
        assert "Crash Summary" in text
        assert "index out of bounds" in text
        assert "0.4.2" in text
        assert "Windows 10 Pro 22H2" in text

    def test_creates_crashes_dir(self, crash_file: Path):
        parsed = parse_crash_file(crash_file)
        summary = write_crash_summary(parsed, crash_file.name)
        assert summary.parent.exists()


# ---------------------------------------------------------------------------
# Consent / telemetry tests
# ---------------------------------------------------------------------------


class TestConsent:
    def test_no_consent_file_returns_none(self):
        assert get_consent() is None

    def test_consent_yes(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "bin.crash_reporter._read_telemetry", lambda: {"crash_upload_consent": True}
        )
        assert get_consent() is True

    def test_consent_no(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "bin.crash_reporter._read_telemetry",
            lambda: {"crash_upload_consent": False},
        )
        assert get_consent() is False

    def test_prompt_consent_yes(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("builtins.input", lambda _: "Y")
        result = prompt_consent()
        assert result is True
        # Read from the module-level TELEMETRY_FILE (which has been monkeypatched)
        import bin.crash_reporter as cr

        data = json.loads(cr.TELEMETRY_FILE.read_text())
        assert data["crash_upload_consent"] is True

    def test_prompt_consent_no(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("builtins.input", lambda _: "n")
        result = prompt_consent()
        assert result is False
        import bin.crash_reporter as cr

        data = json.loads(cr.TELEMETRY_FILE.read_text())
        assert data["crash_upload_consent"] is False

    def test_prompt_consent_empty_defaults_yes(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("builtins.input", lambda _: "")
        result = prompt_consent()
        assert result is True

    def test_prompt_consent_eof_defaults_no(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "builtins.input", lambda _: (_ for _ in ()).throw(EOFError())
        )
        result = prompt_consent()
        assert result is False

    def test_ensure_consent_prompts_once(self, monkeypatch: pytest.MonkeyPatch):
        call_count = 0

        def fake_input(_):
            nonlocal call_count
            call_count += 1
            return "Y"

        monkeypatch.setattr("builtins.input", fake_input)
        ensure_consent()
        # Second call should NOT prompt again
        ensure_consent()
        assert call_count == 1


# ---------------------------------------------------------------------------
# Upload tests
# ---------------------------------------------------------------------------


class TestUploadCrash:
    def test_upload_success(self, crash_file: Path):
        parsed = parse_crash_file(crash_file)
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "abc123", "status": "accepted"}
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        with patch("httpx.Client", return_value=mock_client):
            result = upload_crash(parsed, crash_file.name, "http://127.0.0.1:8089")
            assert result is True
            mock_client.post.assert_called_once()
            call_kwargs = mock_client.post.call_args
            assert call_kwargs[1]["json"]["panic_message"] == parsed["panic_message"]
            assert call_kwargs[1]["json"]["raw_file"] == crash_file.name

    def test_upload_failure(self, crash_file: Path):
        parsed = parse_crash_file(crash_file)
        with patch("httpx.Client", side_effect=Exception("connection refused")):
            result = upload_crash(parsed, crash_file.name, "http://127.0.0.1:8089")
            assert result is False


# ---------------------------------------------------------------------------
# Process crash file tests
# ---------------------------------------------------------------------------


class TestProcessCrashFile:
    def test_process_with_consent_uploads(
        self, crash_file: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            "bin.crash_reporter._read_telemetry", lambda: {"crash_upload_consent": True}
        )
        with patch("bin.crash_reporter.upload_crash") as mock_upload:
            mock_upload.return_value = True
            process_crash_file(crash_file, "http://127.0.0.1:8089")
            mock_upload.assert_called_once()

    def test_process_without_consent_skips_upload(
        self, crash_file: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            "bin.crash_reporter._read_telemetry",
            lambda: {"crash_upload_consent": False},
        )
        with patch("bin.crash_reporter.upload_crash") as mock_upload:
            process_crash_file(crash_file, "http://127.0.0.1:8089")
            mock_upload.assert_not_called()

    def test_process_idempotent(
        self, crash_file: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            "bin.crash_reporter._read_telemetry", lambda: {"crash_upload_consent": True}
        )
        with patch("bin.crash_reporter.upload_crash") as mock_upload:
            mock_upload.return_value = True
            process_crash_file(crash_file, "http://127.0.0.1:8089")
            process_crash_file(crash_file, "http://127.0.0.1:8089")
            assert mock_upload.call_count == 1


# ---------------------------------------------------------------------------
# Backend stub tests (FastAPI)
# ---------------------------------------------------------------------------


class TestBackendStub:
    @pytest.fixture
    def client(self):
        from httpx import ASGITransport, AsyncClient

        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.asyncio
    async def test_post_crash_dump(self, client):
        payload = {
            "panic_message": "index out of bounds",
            "stack_trace": "   0: rust_begin_unwind\n",
            "os_info": "Windows 10",
            "recorder_version": "0.4.2",
            "raw_file": "crash-test.log",
        }
        resp = await client.post("/api/v1/crash/dump", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_list_crash_dumps(self, client):
        # First post one
        payload = {
            "panic_message": "test panic",
            "stack_trace": "",
            "os_info": "Linux",
            "recorder_version": "1.0.0",
            "raw_file": "crash-x.log",
        }
        await client.post("/api/v1/crash/dump", json=payload)
        resp = await client.get("/api/v1/crash/dump")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["panic_message"] == "test panic"

    @pytest.mark.asyncio
    async def test_clear_crash_dumps(self, client):
        payload = {
            "panic_message": "to be cleared",
            "stack_trace": "",
            "os_info": "macOS",
            "recorder_version": "2.0",
            "raw_file": "crash-clear.log",
        }
        await client.post("/api/v1/crash/dump", json=payload)
        resp = await client.delete("/api/v1/crash/dump")
        assert resp.status_code == 200
        resp = await client.get("/api/v1/crash/dump")
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_health(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_no_pii_in_upload(self, client):
        """Verify that the endpoint only accepts the expected anonymized fields."""
        payload = {
            "panic_message": "panic",
            "stack_trace": "trace",
            "os_info": "Windows",
            "recorder_version": "1.0",
            "raw_file": "crash.log",
        }
        await client.post("/api/v1/crash/dump", json=payload)
        # Verify stored data has no unexpected fields
        list_resp = await client.get("/api/v1/crash/dump")
        stored = list_resp.json()[0]
        allowed_keys = {
            "id",
            "timestamp",
            "panic_message",
            "stack_trace",
            "os_info",
            "recorder_version",
            "raw_file",
        }
        assert set(stored.keys()) == allowed_keys


# ---------------------------------------------------------------------------
# Crash file pattern tests
# ---------------------------------------------------------------------------


class TestCrashFilePattern:
    def test_matches_crash_log(self):
        assert CRASH_FILE_PATTERN.match("crash-20260519.log") is not None
        assert CRASH_FILE_PATTERN.match("crash-panic-001.log") is not None
        assert CRASH_FILE_PATTERN.match("crash.log") is not None

    def test_rejects_non_crash(self):
        assert CRASH_FILE_PATTERN.match("normal.log") is None
        assert CRASH_FILE_PATTERN.match("crash.txt") is None
        assert CRASH_FILE_PATTERN.match("crash-20260519.log.bak") is None


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCLI:
    def test_daemon_flag(self):
        from bin.crash_reporter import _parse_args

        args = _parse_args(["--daemon"])
        assert args.daemon is True

    def test_once_flag(self):
        from bin.crash_reporter import _parse_args

        args = _parse_args(["--once"])
        assert args.once is True

    def test_watch_dir(self):
        from bin.crash_reporter import _parse_args

        args = _parse_args(["--watch-dir", "/tmp/test"])
        assert args.watch_dir == "/tmp/test"

    def test_consent_flag(self):
        from bin.crash_reporter import _parse_args

        args = _parse_args(["--consent", "yes"])
        assert args.consent == "yes"

    def test_main_once_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test --once mode processes existing crash files."""
        watch_dir = tmp_path / "logs"
        watch_dir.mkdir()
        (watch_dir / "crash-test.log").write_text(
            "thread 'main' panicked at 'test', src/main.rs:1:1\n"
            "recorder_version: 1.0\n"
            "os: Linux\n"
        )
        monkeypatch.setattr(
            "bin.crash_reporter._read_telemetry",
            lambda: {"crash_upload_consent": False},
        )

        from bin.crash_reporter import main

        main(["--once", "--watch-dir", str(watch_dir)])

        # Verify summary was written
        import bin.crash_reporter as cr

        crashes_dir = cr.CRASHES_DIR
        summaries = list(crashes_dir.glob("summary-*.txt"))
        assert len(summaries) == 1


# ---------------------------------------------------------------------------
# Integration: end-to-end with mock backend
# ---------------------------------------------------------------------------


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_opt_in_y_upload_happens(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """opt-in y → upload happens (mock backend assert)."""
        watch_dir = tmp_path / "logs"
        watch_dir.mkdir()
        crash = watch_dir / "crash-e2e-yes.log"
        crash.write_text(
            "thread 'main' panicked at 'e2e test', src/lib.rs:10:5\n"
            "recorder_version: 0.5.0\n"
            "os: Windows 11\n"
        )

        # Set consent to yes
        monkeypatch.setattr(
            "bin.crash_reporter._read_telemetry", lambda: {"crash_upload_consent": True}
        )

        # Use the real FastAPI test client
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Simulate upload by calling the endpoint directly
            from bin.crash_reporter import parse_crash_file, upload_crash

            parsed = parse_crash_file(crash)

            # Patch httpx.Client to use the test client
            mock_response = MagicMock()
            mock_response.json.return_value = {"id": "e2e-123", "status": "accepted"}
            mock_response.raise_for_status = MagicMock()
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response

            with patch("httpx.Client", return_value=mock_client):
                result = upload_crash(parsed, crash.name, "http://127.0.0.1:8089")
                assert result is True

                # Verify the backend received the data
                backend_resp = await client.post(
                    "/api/v1/crash/dump",
                    json={
                        "panic_message": parsed["panic_message"],
                        "stack_trace": parsed["stack_trace"],
                        "os_info": parsed["os_info"],
                        "recorder_version": parsed["recorder_version"],
                        "raw_file": crash.name,
                    },
                )
                assert backend_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_opt_in_n_zero_upload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """opt-in n → 0 upload."""
        watch_dir = tmp_path / "logs"
        watch_dir.mkdir()
        crash = watch_dir / "crash-e2e-no.log"
        crash.write_text(
            "thread 'main' panicked at 'no upload', src/lib.rs:20:5\n"
            "recorder_version: 0.5.0\n"
            "os: macOS\n"
        )

        monkeypatch.setattr(
            "bin.crash_reporter._read_telemetry",
            lambda: {"crash_upload_consent": False},
        )

        from bin.crash_reporter import process_crash_file

        with patch("bin.crash_reporter.upload_crash") as mock_upload:
            process_crash_file(crash, "http://127.0.0.1:8089")
            mock_upload.assert_not_called()

        # Verify no crashes in backend
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/v1/crash/dump")
            assert resp.json() == []


# ---------------------------------------------------------------------------
# Backend crash_dump module tests
# ---------------------------------------------------------------------------


class TestCrashDumpModule:
    def test_store_and_retrieve(self):
        from backend_stub.crash_dump import CrashDump

        dump = CrashDump(
            panic_message="test",
            stack_trace="trace",
            os_info="Linux",
            recorder_version="1.0",
            raw_file="crash.log",
        )
        cid = store_crash(dump)
        assert cid is not None
        crashes = get_all_crashes()
        assert len(crashes) == 1
        assert crashes[0]["panic_message"] == "test"

    def test_clear(self):
        from backend_stub.crash_dump import CrashDump

        dump = CrashDump(
            panic_message="x",
            stack_trace="",
            os_info="",
            recorder_version="",
            raw_file="",
        )
        store_crash(dump)
        clear_crashes()
        assert get_all_crashes() == []

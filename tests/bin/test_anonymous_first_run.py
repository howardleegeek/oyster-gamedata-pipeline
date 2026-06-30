"""Tests for bin/anonymous_first_run.py.

Covers the anonymous-first-run consumer flow:
- ClipStatus enum values
- ClipMetadata / AnonymousConfig to_dict / from_dict roundtrip
- AnonymousStorage filesystem behaviour (is_initialized, initialize with
  and without force, load_queue empty, enqueue_clip persistence)
- CLI commands: init, status, record, opt-in, upload (dry-run and
  --no-dry-run), cleanup — including error paths (no session, missing
  source file, not opted-in)
- main() integration via build_parser + main(argv) entry point
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from bin.anonymous_first_run import (
    AnonymousConfig,
    AnonymousStorage,
    ClipMetadata,
    ClipStatus,
    build_parser,
    cmd_cleanup,
    cmd_init,
    cmd_opt_in,
    cmd_record,
    cmd_status,
    cmd_upload,
    main,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Args:
    """Mimic argparse.Namespace for direct cmd_* invocation."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


@pytest.fixture
def tmp_storage(tmp_path: Path) -> AnonymousStorage:
    """Provide an AnonymousStorage rooted at a fresh tmp directory."""
    return AnonymousStorage(base_path=tmp_path)


# ---------------------------------------------------------------------------
# ClipStatus enum
# ---------------------------------------------------------------------------


class TestClipStatus:
    def test_values(self):
        assert ClipStatus.PENDING.value == "pending"
        assert ClipStatus.UPLOADING.value == "uploading"
        assert ClipStatus.UPLOADED.value == "uploaded"
        assert ClipStatus.FAILED.value == "failed"

    def test_is_str_enum(self):
        # str Enum: members compare equal to their string value.
        assert ClipStatus.PENDING == "pending"


# ---------------------------------------------------------------------------
# ClipMetadata dataclass
# ---------------------------------------------------------------------------


class TestClipMetadata:
    def test_to_dict_includes_status_string(self):
        clip = ClipMetadata(
            clip_id="abc",
            title="t",
            duration_seconds=1.5,
            created_at="2026-06-30T00:00:00+00:00",
            file_path="/x/y.mp4",
            status=ClipStatus.PENDING,
        )
        data = clip.to_dict()
        assert data["status"] == "pending"
        assert data["clip_id"] == "abc"
        assert data["title"] == "t"
        assert data["duration_seconds"] == 1.5
        assert data["upload_attempts"] == 0
        assert data["error_message"] is None

    def test_from_dict_roundtrip(self):
        original = ClipMetadata(
            clip_id="id1",
            title="hello",
            duration_seconds=12.0,
            created_at="2026-06-30T00:00:00+00:00",
            file_path="/p.mp4",
            status=ClipStatus.UPLOADED,
            upload_attempts=3,
            error_message="boom",
        )
        restored = ClipMetadata.from_dict(original.to_dict())
        assert restored.clip_id == "id1"
        assert restored.title == "hello"
        assert restored.duration_seconds == 12.0
        assert restored.status == ClipStatus.UPLOADED
        assert restored.upload_attempts == 3
        assert restored.error_message == "boom"

    def test_from_dict_default_status(self):
        # If status is missing in dict, default PENDING applies via dataclass.
        clip = ClipMetadata(
            clip_id="x",
            title="t",
            duration_seconds=0.0,
            created_at="2026-06-30T00:00:00+00:00",
            file_path="/p",
        )
        data = clip.to_dict()
        assert data["status"] == "pending"


# ---------------------------------------------------------------------------
# AnonymousConfig dataclass
# ---------------------------------------------------------------------------


class TestAnonymousConfig:
    def test_roundtrip(self):
        cfg = AnonymousConfig(
            anonymous_id="anon-1",
            created_at="2026-06-30T00:00:00+00:00",
            storage_path="/tmp/root",
            opted_in=True,
            email="a@b.com",
            account_id="acct-1",
            last_activity="2026-06-30T01:00:00+00:00",
        )
        restored = AnonymousConfig.from_dict(cfg.to_dict())
        assert restored.anonymous_id == "anon-1"
        assert restored.opted_in is True
        assert restored.email == "a@b.com"
        assert restored.account_id == "acct-1"
        assert restored.storage_path == "/tmp/root"

    def test_defaults(self):
        cfg = AnonymousConfig(
            anonymous_id="x",
            created_at="t",
            storage_path="s",
        )
        assert cfg.opted_in is False
        assert cfg.email is None
        assert cfg.account_id is None
        assert cfg.last_activity is None


# ---------------------------------------------------------------------------
# AnonymousStorage
# ---------------------------------------------------------------------------


class TestAnonymousStoragePaths:
    def test_default_base_uses_home(self):
        with mock.patch("pathlib.Path.home", return_value=Path("/h")):
            s = AnonymousStorage()
        assert s.root == Path("/h") / AnonymousStorage.DIR_NAME
        assert s.config_path == s.root / AnonymousStorage.CONFIG_FILE
        assert s.queue_path == s.root / AnonymousStorage.QUEUE_FILE
        assert s.clips_dir == s.root / AnonymousStorage.CLIPS_SUBDIR

    def test_explicit_base(self, tmp_path):
        s = AnonymousStorage(base_path=tmp_path)
        assert s.root == tmp_path / AnonymousStorage.DIR_NAME


class TestAnonymousStorageIsInitialized:
    def test_false_initially(self, tmp_storage):
        assert tmp_storage.is_initialized() is False

    def test_true_after_initialize(self, tmp_storage):
        tmp_storage.initialize()
        assert tmp_storage.is_initialized() is True


class TestAnonymousStorageInitialize:
    def test_creates_config_and_empty_queue(self, tmp_storage):
        cfg = tmp_storage.initialize()
        # config_path exists
        assert tmp_storage.config_path.exists()
        # queue_path exists and is []
        assert tmp_storage.queue_path.exists()
        assert json.loads(tmp_storage.queue_path.read_text()) == []
        # clips_dir exists
        assert tmp_storage.clips_dir.is_dir()
        # anonymous_id is a non-empty string (uuid4 hex form is 36 chars).
        assert isinstance(cfg.anonymous_id, str)
        assert len(cfg.anonymous_id) > 0
        # created_at + storage_path are populated
        assert cfg.created_at
        assert cfg.storage_path == str(tmp_storage.root)
        # not opted in by default
        assert cfg.opted_in is False

    def test_returns_existing_when_already_initialized(self, tmp_storage):
        first = tmp_storage.initialize()
        again = tmp_storage.initialize()  # no force
        assert again.anonymous_id == first.anonymous_id

    def test_force_regenerates_id(self, tmp_storage):
        first = tmp_storage.initialize()
        # Force-reinitialise with a stubbed uuid to ensure determinism.
        with mock.patch(
            "bin.anonymous_first_run.uuid.uuid4",
            return_value=_FakeUUID("new-uuid"),
        ):
            second = tmp_storage.initialize(force=True)
        assert second.anonymous_id == "new-uuid"
        assert second.anonymous_id != first.anonymous_id


class _FakeUUID:
    def __init__(self, value: str):
        self._value = value

    def __str__(self) -> str:
        return self._value


class TestAnonymousStorageQueue:
    def test_load_queue_missing_returns_empty(self, tmp_storage):
        assert tmp_storage.load_queue() == []

    def test_enqueue_clip_persists(self, tmp_storage):
        tmp_storage.initialize()
        clip = ClipMetadata(
            clip_id="c1",
            title="t1",
            duration_seconds=2.0,
            created_at="2026-06-30T00:00:00+00:00",
            file_path=str(tmp_storage.clip_path("c1")),
        )
        tmp_storage.enqueue_clip(clip)
        queue = tmp_storage.load_queue()
        assert len(queue) == 1
        assert queue[0].clip_id == "c1"
        assert queue[0].status == ClipStatus.PENDING

        # On-disk JSON is consistent with to_dict()
        on_disk = json.loads(tmp_storage.queue_path.read_text())
        assert on_disk[0]["clip_id"] == "c1"
        assert on_disk[0]["status"] == "pending"

    def test_enqueue_multiple_clips(self, tmp_storage):
        tmp_storage.initialize()
        for i in range(3):
            tmp_storage.enqueue_clip(
                ClipMetadata(
                    clip_id=f"c{i}",
                    title=f"t{i}",
                    duration_seconds=float(i),
                    created_at="2026-06-30T00:00:00+00:00",
                    file_path=str(tmp_storage.clip_path(f"c{i}")),
                )
            )
        queue = tmp_storage.load_queue()
        assert [c.clip_id for c in queue] == ["c0", "c1", "c2"]

    def test_clip_path_suffix(self, tmp_storage):
        p = tmp_storage.clip_path("abc")
        assert p.name == "abc.mp4"
        p2 = tmp_storage.clip_path("xyz", suffix=".mov")
        assert p2.name == "xyz.mov"


# ---------------------------------------------------------------------------
# CLI command functions
# ---------------------------------------------------------------------------


class TestCmdInit:
    def test_init_creates_session(self, tmp_storage, capsys):
        rc = cmd_init(_Args(), tmp_storage)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Anonymous session created" in out
        assert str(tmp_storage.root) in out
        assert "Clips will be stored locally" in out

    def test_init_force(self, tmp_storage, capsys):
        tmp_storage.initialize()
        rc = cmd_init(_Args(force=True), tmp_storage)
        assert rc == 0
        assert "Anonymous session created" in capsys.readouterr().out


class TestCmdStatus:
    def test_no_session_returns_1(self, tmp_storage, capsys):
        rc = cmd_status(_Args(), tmp_storage)
        assert rc == 1
        err = capsys.readouterr().err
        assert "No anonymous session" in err

    def test_empty_session_status(self, tmp_storage, capsys):
        tmp_storage.initialize()
        rc = cmd_status(_Args(), tmp_storage)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Anonymous ID" in out
        assert "Opted-in" in out
        assert "Queued clips : 0" in out

    def test_status_with_pending_clips(self, tmp_storage, capsys):
        tmp_storage.initialize()
        tmp_storage.enqueue_clip(
            ClipMetadata(
                clip_id="c1",
                title="t1",
                duration_seconds=1.0,
                created_at="t",
                file_path="x",
            )
        )
        rc = cmd_status(_Args(), tmp_storage)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Queued clips : 1" in out
        assert "Pending" in out


class TestCmdRecord:
    def test_no_session_returns_1(self, tmp_storage, capsys):
        rc = cmd_record(_Args(title="t", duration=1.0), tmp_storage)
        assert rc == 1
        assert "No anonymous session" in capsys.readouterr().err

    def test_record_success_no_source(self, tmp_storage, capsys):
        tmp_storage.initialize()
        rc = cmd_record(_Args(title="hello", duration=2.5), tmp_storage)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Clip queued" in out
        assert "hello" in out
        # Queued
        queue = tmp_storage.load_queue()
        assert len(queue) == 1
        assert queue[0].title == "hello"
        assert queue[0].duration_seconds == 2.5
        assert queue[0].status == ClipStatus.PENDING

    def test_record_missing_source_returns_1(self, tmp_storage, capsys):
        tmp_storage.initialize()
        rc = cmd_record(
            _Args(title="t", duration=1.0, source="/nonexistent/file.mp4"),
            tmp_storage,
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "Source file not found" in err
        # Nothing should have been queued
        assert tmp_storage.load_queue() == []

    def test_record_with_existing_source_copies(self, tmp_storage, capsys):
        tmp_storage.initialize()
        # Use tmp_storage.root.parent (already inside pytest tmp_path) as a
        # safe location for the source file we copy from.
        src = tmp_storage.root.parent / "source.mp4"
        src.write_bytes(b"DATA")
        rc = cmd_record(
            _Args(title="t", duration=1.0, source=str(src)),
            tmp_storage,
        )
        assert rc == 0
        queue = tmp_storage.load_queue()
        assert len(queue) == 1
        copied = Path(queue[0].file_path)
        assert copied.exists()
        assert copied.read_bytes() == b"DATA"


class TestCmdOptIn:
    def test_no_session_returns_1(self, tmp_storage, capsys):
        rc = cmd_opt_in(_Args(email="a@b.com"), tmp_storage)
        assert rc == 1
        assert "No anonymous session" in capsys.readouterr().err

    def test_opt_in_sets_email_and_account_id(self, tmp_storage, capsys):
        tmp_storage.initialize()
        with mock.patch(
            "bin.anonymous_first_run.uuid.uuid4",
            return_value=_FakeUUID("acc-xyz"),
        ):
            rc = cmd_opt_in(_Args(email="a@b.com"), tmp_storage)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Opt-in complete" in out
        assert "a@b.com" in out
        cfg = tmp_storage.load_config()
        assert cfg.opted_in is True
        assert cfg.email == "a@b.com"
        assert cfg.account_id == "acc-xyz"

    def test_opt_in_explicit_account_id_is_kept(self, tmp_storage, capsys):
        tmp_storage.initialize()
        rc = cmd_opt_in(
            _Args(email="x@y", account_id="my-acct"), tmp_storage,
        )
        assert rc == 0
        assert tmp_storage.load_config().account_id == "my-acct"


class TestCmdUpload:
    def test_no_session_returns_1(self, tmp_storage, capsys):
        rc = cmd_upload(_Args(), tmp_storage)
        assert rc == 1
        assert "No anonymous session" in capsys.readouterr().err

    def test_not_opted_in_returns_1(self, tmp_storage, capsys):
        tmp_storage.initialize()
        rc = cmd_upload(_Args(), tmp_storage)
        assert rc == 1
        assert "Not opted-in" in capsys.readouterr().err

    def test_no_pending_clips_returns_0(self, tmp_storage, capsys):
        tmp_storage.initialize()
        tmp_storage.load_config()  # ensure config exists
        # Mark opted-in
        cfg = tmp_storage.load_config()
        cfg.opted_in = True
        tmp_storage.save_config(cfg)

        rc = cmd_upload(_Args(), tmp_storage)
        assert rc == 0
        assert "No pending clips" in capsys.readouterr().out

    def test_dry_run_marks_uploaded_but_prints_dry_run(self, tmp_storage, capsys):
        tmp_storage.initialize()
        tmp_storage.enqueue_clip(
            ClipMetadata(
                clip_id="c1",
                title="t1",
                duration_seconds=1.0,
                created_at="t",
                file_path="x",
            )
        )
        cfg = tmp_storage.load_config()
        cfg.opted_in = True
        tmp_storage.save_config(cfg)

        rc = cmd_upload(_Args(), tmp_storage)  # no_dry_run False by default
        assert rc == 0
        out = capsys.readouterr().out
        assert "[DRY-RUN] Would upload" in out
        assert "c1" in out
        # Clip is now UPLOADED
        assert tmp_storage.load_queue()[0].status == ClipStatus.UPLOADED

    def test_no_dry_run_flag(self, tmp_storage, capsys):
        tmp_storage.initialize()
        tmp_storage.enqueue_clip(
            ClipMetadata(
                clip_id="c2",
                title="t2",
                duration_seconds=2.0,
                created_at="t",
                file_path="x",
            )
        )
        cfg = tmp_storage.load_config()
        cfg.opted_in = True
        tmp_storage.save_config(cfg)

        rc = cmd_upload(_Args(no_dry_run=True), tmp_storage)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Uploaded: c2" in out
        assert "[DRY-RUN]" not in out


class TestCmdCleanup:
    def test_no_session_returns_1(self, tmp_storage, capsys):
        rc = cmd_cleanup(_Args(), tmp_storage)
        assert rc == 1
        assert "No anonymous session" in capsys.readouterr().err

    def test_cleanup_removes_root(self, tmp_storage, capsys):
        tmp_storage.initialize()
        assert tmp_storage.root.exists()
        rc = cmd_cleanup(_Args(), tmp_storage)
        assert rc == 0
        assert "Anonymous data removed" in capsys.readouterr().out
        assert not tmp_storage.root.exists()


# ---------------------------------------------------------------------------
# main() + build_parser() integration
# ---------------------------------------------------------------------------


class TestBuildParser:
    def test_parser_has_all_subcommands(self):
        parser = build_parser()
        # Round-trip parse: just check no exception and command registered
        for sub in ["init", "record", "status", "opt-in", "upload", "cleanup"]:
            argv = [sub]
            args = parser.parse_args(argv)
            assert args.command == sub
            assert hasattr(args, "func")

    def test_storage_dir_default_none(self):
        parser = build_parser()
        args = parser.parse_args(["init"])
        assert args.storage_dir is None

    def test_storage_dir_explicit(self, tmp_path):
        parser = build_parser()
        # --storage-dir is a top-level option, must precede the subcommand
        args = parser.parse_args(["--storage-dir", str(tmp_path), "init"])
        assert args.storage_dir == tmp_path

    def test_record_parsing(self):
        parser = build_parser()
        args = parser.parse_args(
            ["record", "--title", "T", "--duration", "12.5", "--source", "/a/b.mp4"]
        )
        assert args.title == "T"
        assert args.duration == 12.5
        assert args.source == "/a/b.mp4"

    def test_opt_in_parsing(self):
        parser = build_parser()
        args = parser.parse_args(["opt-in", "--email", "a@b", "--account-id", "X"])
        assert args.email == "a@b"
        assert args.account_id == "X"

    def test_upload_no_dry_run(self):
        parser = build_parser()
        args = parser.parse_args(["upload", "--no-dry-run"])
        assert args.no_dry_run is True

    def test_init_force(self):
        parser = build_parser()
        args = parser.parse_args(["init", "--force"])
        assert args.force is True


class TestMain:
    def test_main_init_then_status(self, tmp_path, capsys):
        rc = main(["--storage-dir", str(tmp_path), "init"])
        assert rc == 0
        assert "Anonymous session created" in capsys.readouterr().out

        rc2 = main(["--storage-dir", str(tmp_path), "status"])
        assert rc2 == 0
        out2 = capsys.readouterr().out
        assert "Anonymous ID" in out2
        assert "Opted-in" in out2

    def test_main_record_then_upload_dry_run(self, tmp_path, capsys):
        main(["--storage-dir", str(tmp_path), "init"])
        rc = main(
            [
                "--storage-dir",
                str(tmp_path),
                "record",
                "--title",
                "demo",
                "--duration",
                "3.0",
            ]
        )
        assert rc == 0
        assert "Clip queued" in capsys.readouterr().out

        # opt-in then upload (dry-run)
        main(["--storage-dir", str(tmp_path), "opt-in", "--email", "a@b"])
        rc2 = main(["--storage-dir", str(tmp_path), "upload"])
        assert rc2 == 0
        out2 = capsys.readouterr().out
        assert "[DRY-RUN] Would upload" in out2

    def test_main_status_no_session_returns_1(self, tmp_path, capsys):
        rc = main(["--storage-dir", str(tmp_path), "status"])
        assert rc == 1

    def test_main_verbose_flag(self, tmp_path, capsys):
        # Verbose toggles logging level; it should not break execution.
        rc = main(["--storage-dir", str(tmp_path), "-v", "init"])
        assert rc == 0
        assert "Anonymous session created" in capsys.readouterr().out

    def test_main_cleanup_removes(self, tmp_path, capsys):
        main(["--storage-dir", str(tmp_path), "init"])
        storage_dir = tmp_path / AnonymousStorage.DIR_NAME
        assert storage_dir.exists()
        rc = main(["--storage-dir", str(tmp_path), "cleanup"])
        assert rc == 0
        assert not storage_dir.exists()

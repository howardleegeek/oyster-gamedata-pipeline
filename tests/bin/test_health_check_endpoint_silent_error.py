"""Regression tests for bin/health_check_endpoint.py silent-error surfacing.

Verifies that the two except-blocks (json/OSError in get_last_clip_at, and
OSError in get_queue_depth) no longer swallow exceptions silently — they
must log a warning and still return their documented fallback value.
"""
import json
import logging
import os
import stat
from pathlib import Path

import pytest

from bin.health_check_endpoint import get_last_clip_at, get_queue_depth


def test_get_last_clip_at_corrupt_json_logs_warning(tmp_path, caplog):
    state = tmp_path / "state.json"
    state.write_text("this is not json {{{")
    with caplog.at_level(logging.WARNING, logger="bin.health_check_endpoint"):
        result = get_last_clip_at(state)
    assert result is None
    assert any("failed to read state file" in rec.message for rec in caplog.records), (
        f"expected warning logged, got: {[r.message for r in caplog.records]}"
    )


def test_get_last_clip_at_unreadable_logs_warning(tmp_path, caplog):
    state = tmp_path / "state.json"
    state.write_text('{"last_clip_at": 1.0}')
    # Make file unreadable to trigger OSError on read_text()
    os.chmod(state, 0o000)
    try:
        with caplog.at_level(logging.WARNING, logger="bin.health_check_endpoint"):
            try:
                result = get_last_clip_at(state)
            except OSError:
                # On some platforms (root, or fs semantics) the file is still
                # readable — skip rather than fail.
                pytest.skip("filesystem did not honor chmod 0o000")
        assert result is None
        assert any("failed to read state file" in rec.message for rec in caplog.records), (
            f"expected warning logged, got: {[r.message for r in caplog.records]}"
        )
    finally:
        os.chmod(state, stat.S_IRUSR | stat.S_IWUSR)


def test_get_queue_depth_iterdir_error_logs_warning(tmp_path, caplog, monkeypatch):
    qdir = tmp_path / "queue"
    qdir.mkdir()

    def boom(_self):
        raise OSError("simulated iterdir failure")

    monkeypatch.setattr(Path, "iterdir", boom)
    with caplog.at_level(logging.WARNING, logger="bin.health_check_endpoint"):
        result = get_queue_depth(qdir)
    assert result == 0
    assert any("failed to read queue dir" in rec.message for rec in caplog.records), (
        f"expected warning logged, got: {[r.message for r in caplog.records]}"
    )


def test_get_last_clip_at_missing_returns_none_without_log(tmp_path, caplog):
    """Missing file is not an error — must return None silently."""
    state = tmp_path / "does_not_exist.json"
    with caplog.at_level(logging.WARNING, logger="bin.health_check_endpoint"):
        result = get_last_clip_at(state)
    assert result is None
    assert not any("failed to read state file" in rec.message for rec in caplog.records)


def test_get_queue_depth_missing_dir_returns_zero_without_log(tmp_path, caplog):
    """Missing dir is not an error — must return 0 silently."""
    qdir = tmp_path / "no_such_queue"
    with caplog.at_level(logging.WARNING, logger="bin.health_check_endpoint"):
        result = get_queue_depth(qdir)
    assert result == 0
    assert not any("failed to read queue dir" in rec.message for rec in caplog.records)


def test_get_last_clip_at_valid_returns_value(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"last_clip_at": 12345.6}))
    assert get_last_clip_at(state) == 12345.6

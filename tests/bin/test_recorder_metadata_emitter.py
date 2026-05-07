"""Tests for bin/recorder_metadata_emitter.py."""

from __future__ import annotations

import json
import re
import uuid

from bin import recorder_metadata_emitter as md

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")


class TestUtcNowIso:
    def test_format(self):
        assert ISO_RE.match(md.utc_now_iso())


class TestDeviceId:
    def test_deterministic(self):
        first = md.device_id("host-x")
        second = md.device_id("host-x")
        assert first == second

    def test_length(self):
        assert len(md.device_id("anything")) == md.DEVICE_ID_LENGTH

    def test_distinct_for_distinct_hosts(self):
        assert md.device_id("alpha") != md.device_id("beta")

    def test_only_hex_chars(self):
        assert re.match(r"^[0-9a-f]+$", md.device_id("any-host"))


class TestSessionId:
    def test_is_uuid4(self):
        sid = md.session_id()
        # uuid.UUID raises if malformed; round-trip confirms uuid4.
        u = uuid.UUID(sid)
        assert u.version == 4


class TestBuildMetadata:
    def test_required_keys_present(self):
        payload = md.build_metadata(hostname="test-host")
        for key in ("timestamp", "location", "device_id", "session_id"):
            assert key in payload
        assert payload["location"] == "anonymous"

    def test_overrides_respected(self):
        payload = md.build_metadata(
            hostname="abc",
            timestamp="2026-01-01T00:00:00+00:00",
            session="00000000-0000-4000-8000-000000000000",
        )
        assert payload["timestamp"] == "2026-01-01T00:00:00+00:00"
        assert payload["session_id"] == "00000000-0000-4000-8000-000000000000"


class TestWriteMetadata:
    def test_writes_metadata_json(self, tmp_path):
        out = md.write_metadata(
            tmp_path,
            metadata={"timestamp": "x", "location": "y", "device_id": "z", "session_id": "s"},
        )
        assert out == tmp_path / "metadata.json"
        loaded = json.loads(out.read_text())
        assert loaded["device_id"] == "z"


class TestMain:
    def test_exit_1_on_missing_dir(self, tmp_path):
        missing = tmp_path / "no-such"
        assert md.main(["--clip-dir", str(missing)]) == 1

    def test_exit_0_writes_file(self, tmp_path):
        rc = md.main(["--clip-dir", str(tmp_path)])
        assert rc == 0
        loaded = json.loads((tmp_path / "metadata.json").read_text())
        assert loaded["location"] == "anonymous"
        assert ISO_RE.match(loaded["timestamp"])

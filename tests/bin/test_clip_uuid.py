#!/usr/bin/env python3
"""Tests for bin/clip_uuid.py — Per-clip UUID4 generator + injection helpers.

Closes audit gap G280 / C6: every recorded clip needs a globally unique
identifier so the ingest pipeline can deduplicate / cross-reference clips
captured on different machines without collision.

Covers:
- new_clip_uuid (32-hex length, no dashes, uniqueness across many calls)
- _write_marker (creates a dotfile named after the UUID inside clip_dir)
- inject_uuid (generates and stamps, idempotency on existing key, custom
  UUID passthrough, FileNotFoundError when clip_dir missing,
  NotADirectoryError when clip_dir is a file)
- _cli (subcommand "new" prints a UUID; subcommand "inject" round-trips
  systeminfo.json; "inject" with missing systeminfo → exit 2;
  "inject" with non-object JSON → exit 2; missing subcommand → SystemExit
  from argparse)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from bin.clip_uuid import (  # noqa: E402
    MARKER_PREFIX,
    SYSTEMINFO_KEY,
    _cli,
    _write_marker,
    inject_uuid,
    new_clip_uuid,
)

_UUID4_HEX_RE = re.compile(r"^[0-9a-f]{32}$")


# ---------------------------------------------------------------------------
# new_clip_uuid
# ---------------------------------------------------------------------------


class TestNewClipUuid:
    """Tests for the stdlib uuid.uuid4 wrapper."""

    def test_returns_32_char_hex(self):
        """Result must be 32 lowercase hex chars with no dashes."""
        u = new_clip_uuid()
        assert _UUID4_HEX_RE.match(u), f"unexpected format: {u!r}"

    def test_no_dashes(self):
        """Result must be a flat hex string (no '-' separators)."""
        u = new_clip_uuid()
        assert "-" not in u

    def test_unique_across_many_calls(self):
        """100 successive calls produce 100 distinct UUIDs (UUID4 entropy)."""
        uuids = {new_clip_uuid() for _ in range(100)}
        assert len(uuids) == 100


# ---------------------------------------------------------------------------
# _write_marker
# ---------------------------------------------------------------------------


class TestWriteMarker:
    """Tests for the side-channel marker file writer."""

    def test_creates_dotfile_with_uuid_in_name(self, tmp_path: Path):
        clip_dir = tmp_path / "clip"
        clip_dir.mkdir()
        u = "abc123" + "0" * 26  # 32 chars
        marker = _write_marker(clip_dir, u)
        assert marker.name == f"{MARKER_PREFIX}{u}"
        assert marker.exists()
        assert marker.parent == clip_dir

    def test_marker_is_empty(self, tmp_path: Path):
        """Marker file carries its identity in the filename, not its content."""
        clip_dir = tmp_path / "clip"
        clip_dir.mkdir()
        marker = _write_marker(clip_dir, "f" * 32)
        assert marker.read_bytes() == b""


# ---------------------------------------------------------------------------
# inject_uuid
# ---------------------------------------------------------------------------


class TestInjectUuid:
    """Tests for the systeminfo dict + marker-file injector."""

    def test_generates_and_stamps(self, tmp_path: Path):
        clip_dir = tmp_path / "clip"
        clip_dir.mkdir()
        sysinfo: dict = {"hostname": "alice-pc"}
        result = inject_uuid(sysinfo, clip_dir)
        assert result == sysinfo[SYSTEMINFO_KEY]
        assert _UUID4_HEX_RE.match(result)
        # Marker file written with the same UUID
        marker = clip_dir / f"{MARKER_PREFIX}{result}"
        assert marker.exists()

    def test_idempotent_on_existing_key(self, tmp_path: Path):
        """If systeminfo already has a non-empty string, preserve it."""
        clip_dir = tmp_path / "clip"
        clip_dir.mkdir()
        existing = "deadbeef" + "0" * 24  # 32 hex chars
        sysinfo: dict = {SYSTEMINFO_KEY: existing, "hostname": "bob-pc"}
        result = inject_uuid(sysinfo, clip_dir)
        assert result == existing
        assert sysinfo[SYSTEMINFO_KEY] == existing
        # Marker also uses the preserved UUID
        assert (clip_dir / f"{MARKER_PREFIX}{existing}").exists()

    def test_idempotent_empty_existing_is_overwritten(self, tmp_path: Path):
        """An empty-string existing key is treated as missing → new UUID minted."""
        clip_dir = tmp_path / "clip"
        clip_dir.mkdir()
        sysinfo: dict = {SYSTEMINFO_KEY: ""}
        result = inject_uuid(sysinfo, clip_dir)
        assert result  # non-empty
        assert _UUID4_HEX_RE.match(result)
        assert sysinfo[SYSTEMINFO_KEY] == result

    def test_custom_uuid_passthrough(self, tmp_path: Path):
        """Passing clip_uuid= uses that exact value (no generation)."""
        clip_dir = tmp_path / "clip"
        clip_dir.mkdir()
        chosen = "0123456789abcdef0123456789abcdef"
        sysinfo: dict = {}
        result = inject_uuid(sysinfo, clip_dir, clip_uuid=chosen)
        assert result == chosen
        assert sysinfo[SYSTEMINFO_KEY] == chosen
        assert (clip_dir / f"{MARKER_PREFIX}{chosen}").exists()

    def test_clip_dir_accepts_string(self, tmp_path: Path):
        """clip_dir may be a string (forward-compat with shutil.copytree usage)."""
        clip_dir = tmp_path / "clip"
        clip_dir.mkdir()
        sysinfo: dict = {}
        result = inject_uuid(sysinfo, str(clip_dir))
        assert result == sysinfo[SYSTEMINFO_KEY]
        assert (clip_dir / f"{MARKER_PREFIX}{result}").exists()

    def test_missing_clip_dir_raises(self, tmp_path: Path):
        sysinfo: dict = {}
        with pytest.raises(FileNotFoundError):
            inject_uuid(sysinfo, tmp_path / "nope")

    def test_clip_dir_is_file_raises(self, tmp_path: Path):
        not_a_dir = tmp_path / "iamafile"
        not_a_dir.write_text("hello", encoding="utf-8")
        sysinfo: dict = {}
        with pytest.raises(NotADirectoryError):
            inject_uuid(sysinfo, not_a_dir)


# ---------------------------------------------------------------------------
# _cli
# ---------------------------------------------------------------------------


class TestCli:
    """Tests for the argparse-driven CLI entry point."""

    def test_new_subcommand_prints_uuid(self, capsys):
        rc = _cli(["new"])
        assert rc == 0
        out = capsys.readouterr().out.strip()
        assert _UUID4_HEX_RE.match(out), f"unexpected new output: {out!r}"

    def test_inject_round_trip(self, tmp_path: Path, capsys):
        clip_dir = tmp_path / "clip"
        clip_dir.mkdir()
        sysinfo_path = tmp_path / "systeminfo.json"
        sysinfo_path.write_text(
            json.dumps({"hostname": "alice-pc", "os": "linux"}),
            encoding="utf-8",
        )
        rc = _cli(
            [
                "inject",
                "--clip-dir",
                str(clip_dir),
                "--systeminfo",
                str(sysinfo_path),
            ]
        )
        assert rc == 0
        # CLI prints the chosen UUID on stdout
        printed = capsys.readouterr().out.strip()
        assert _UUID4_HEX_RE.match(printed)
        # systeminfo.json was rewritten in place with the key
        reloaded = json.loads(sysinfo_path.read_text(encoding="utf-8"))
        assert reloaded[SYSTEMINFO_KEY] == printed
        # Marker file exists
        assert (clip_dir / f"{MARKER_PREFIX}{printed}").exists()
        # Pre-existing keys preserved
        assert reloaded["hostname"] == "alice-pc"
        assert reloaded["os"] == "linux"

    def test_inject_with_explicit_uuid(self, tmp_path: Path, capsys):
        clip_dir = tmp_path / "clip"
        clip_dir.mkdir()
        sysinfo_path = tmp_path / "systeminfo.json"
        sysinfo_path.write_text("{}", encoding="utf-8")
        chosen = "fedcba9876543210fedcba9876543210"
        rc = _cli(
            [
                "inject",
                "--clip-dir",
                str(clip_dir),
                "--systeminfo",
                str(sysinfo_path),
                "--uuid",
                chosen,
            ]
        )
        assert rc == 0
        assert capsys.readouterr().out.strip() == chosen
        reloaded = json.loads(sysinfo_path.read_text(encoding="utf-8"))
        assert reloaded[SYSTEMINFO_KEY] == chosen
        assert (clip_dir / f"{MARKER_PREFIX}{chosen}").exists()

    def test_inject_missing_systeminfo_exits_2(self, tmp_path: Path, capsys):
        clip_dir = tmp_path / "clip"
        clip_dir.mkdir()
        sysinfo_path = tmp_path / "does_not_exist.json"
        rc = _cli(
            [
                "inject",
                "--clip-dir",
                str(clip_dir),
                "--systeminfo",
                str(sysinfo_path),
            ]
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "missing" in err.lower()

    def test_inject_non_object_json_exits_2(self, tmp_path: Path, capsys):
        clip_dir = tmp_path / "clip"
        clip_dir.mkdir()
        sysinfo_path = tmp_path / "systeminfo.json"
        sysinfo_path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        rc = _cli(
            [
                "inject",
                "--clip-dir",
                str(clip_dir),
                "--systeminfo",
                str(sysinfo_path),
            ]
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "object" in err.lower()

    def test_missing_subcommand_exits_via_argparse(self, capsys):
        """argparse with required subparsers raises SystemExit on no cmd."""
        with pytest.raises(SystemExit):
            _cli([])

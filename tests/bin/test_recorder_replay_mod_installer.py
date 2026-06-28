#!/usr/bin/env python3
"""Tests for bin/recorder_replay_mod_installer.py (spec G264)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

from bin.recorder_replay_mod_installer import (  # noqa: E402
    DEFAULT_INDEX_URL,
    JAR_NAME_TEMPLATE,
    USAGE_DOC_PATH,
    ReplayModRelease,
    detect_minecraft_dir,
    detect_minecraft_version,
    install,
    main,
    resolve_release,
    write_usage_doc,
)


def test_detect_minecraft_dir_returns_path() -> None:
    """Always returns a Path (may not exist)."""
    p = detect_minecraft_dir()
    assert isinstance(p, Path)


def test_detect_minecraft_version_missing(tmp_path: Path) -> None:
    """Missing launcher_profiles.json -> None."""
    assert detect_minecraft_version(tmp_path) is None


def test_detect_minecraft_version_picks_latest(tmp_path: Path) -> None:
    """Picks the profile with the latest lastUsed timestamp."""
    payload = {
        "profiles": {
            "old": {"lastUsed": "2020-01-01T00:00:00.000Z", "lastVersionId": "1.16.5"},
            "new": {"lastUsed": "2024-06-01T00:00:00.000Z", "lastVersionId": "1.20.1"},
        }
    }
    (tmp_path / "launcher_profiles.json").write_text(json.dumps(payload))
    assert detect_minecraft_version(tmp_path) == "1.20.1"


def test_detect_minecraft_version_malformed(tmp_path: Path) -> None:
    """Malformed JSON -> None (no exception)."""
    (tmp_path / "launcher_profiles.json").write_text("not json")
    assert detect_minecraft_version(tmp_path) is None


def test_resolve_release_picks_highest_mod_version() -> None:
    index = [
        {"mc_version": "1.20.1", "mod_version": "2.6.10", "url": "u1"},
        {"mc_version": "1.20.1", "mod_version": "2.6.20", "url": "u2", "sha256": "abc"},
        {"mc_version": "1.19.4", "mod_version": "2.6.30", "url": "u3"},
    ]
    rel = resolve_release("1.20.1", index)
    assert rel.mod_version == "2.6.20"
    assert rel.download_url == "u2"
    assert rel.sha256 == "abc"


def test_resolve_release_no_match() -> None:
    with pytest.raises(LookupError):
        resolve_release("9.9.9", [])


def test_write_usage_doc_emits_md(tmp_path: Path) -> None:
    rel = ReplayModRelease("1.20.1", "2.6.20", "https://example/test.jar")
    doc = write_usage_doc(tmp_path, rel, tmp_path / ".minecraft" / "mods")
    assert doc == tmp_path / USAGE_DOC_PATH
    text = doc.read_text(encoding="utf-8")
    assert "Replay Mod" in text
    assert "1.20.1" in text
    assert "2.6.20" in text


def test_install_dry_run(tmp_path: Path) -> None:
    """Dry-run only emits the doc and skips network."""
    summary = install(
        repo_root=tmp_path,
        mc_version="1.20.1",
        dry_run=True,
        mods_dir=tmp_path / "mods",
    )
    assert summary["dry_run"] is True
    assert summary["mc_version"] == "1.20.1"
    assert (tmp_path / USAGE_DOC_PATH).is_file()


def test_install_no_version_raises(tmp_path: Path) -> None:
    with (
        mock.patch(
            "bin.recorder_replay_mod_installer.detect_minecraft_version", return_value=None
        ),
        pytest.raises(LookupError),
    ):
        install(repo_root=tmp_path, dry_run=True)


def test_install_full_with_mocks(tmp_path: Path) -> None:
    """Mocked end-to-end: index fetch + jar download + doc emit."""
    fake_index = [{"mc_version": "1.20.1", "mod_version": "2.6.20", "url": "u"}]

    fake_jar = (
        tmp_path / "mods" / JAR_NAME_TEMPLATE.format(mc_version="1.20.1", mod_version="2.6.20")
    )

    def _fake_download(release, mods_dir, **kw):
        mods_dir.mkdir(parents=True, exist_ok=True)
        fake_jar.write_bytes(b"jar")
        return fake_jar

    with (
        mock.patch(
            "bin.recorder_replay_mod_installer.fetch_release_index", return_value=fake_index
        ),
        mock.patch("bin.recorder_replay_mod_installer.download_jar", side_effect=_fake_download),
    ):
        summary = install(
            repo_root=tmp_path,
            mc_version="1.20.1",
            mods_dir=tmp_path / "mods",
        )

    assert summary["dry_run"] is False
    assert summary["mod_version"] == "2.6.20"
    assert summary["jar_path"] == str(fake_jar)
    assert (tmp_path / USAGE_DOC_PATH).is_file()


def test_main_dry_run_returns_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        [
            "--mc-version",
            "1.20.1",
            "--repo-root",
            str(tmp_path),
            "--mods-dir",
            str(tmp_path / "mods"),
            "--dry-run",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True


def test_default_index_url_is_https() -> None:
    assert DEFAULT_INDEX_URL.startswith("https://")

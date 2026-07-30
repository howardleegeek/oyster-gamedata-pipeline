"""CLI integration tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oyster_gamedata_sdk.cli import main as cli_main


def test_inspect_synthetic(minimal_clip: Path, capsys):
    rc = cli_main(["inspect", str(minimal_clip)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "test.exe" in captured.out
    assert "1920x1080" in captured.out


def test_summary_json(minimal_clip: Path, capsys):
    rc = cli_main(["summary", str(minimal_clip), "--json"])
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["systeminfo"]["resolution"] == [1920, 1080]


def test_validate_json_file(minimal_clip: Path, tmp_path: Path):
    out = tmp_path / "report.json"
    rc = cli_main(["validate", str(minimal_clip), "--json", "-o", str(out)])
    # exit code may be 0 or 1 depending on whether real lint runs; both fine
    assert rc in (0, 1)
    assert out.is_file()
    payload = json.loads(out.read_text())
    assert "summary" in payload


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli_main(["--version"])
    assert exc.value.code == 0


def test_missing_path_returns_2(tmp_path: Path):
    rc = cli_main(["inspect", str(tmp_path / "nope.tar.gz")])
    assert rc == 2

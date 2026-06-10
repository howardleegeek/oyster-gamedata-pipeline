from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bin import prd_compliance_audit as audit

REPO_ROOT = Path(__file__).resolve().parents[1]


def _item(id_: str, status: str) -> dict:
    return {"id": id_, "status": status, "evidence": f"{id_} {status}"}


def test_critical_fail_forces_low_score_and_fail() -> None:
    items = [_item("A1", "FAIL")] + [_item(f"M{i}", "PASS") for i in range(1, 86)]

    summary = audit.summarize_audit_items(items)

    assert summary["verdict"] == "FAIL"
    assert summary["score_percent"] == 0.0
    assert summary["score_10"] == 0.0
    assert summary["proportional_score_percent"] > 98.0
    assert summary["critical_failed"][0]["id"] == "A1"
    assert items[0]["critical"] is True


def test_minor_only_fail_keeps_proportional_score() -> None:
    items = [_item("A1", "PASS"), _item("minor-check", "FAIL")]

    summary = audit.summarize_audit_items(items)

    assert summary["verdict"] == "FAIL"
    assert summary["score_percent"] == 50.0
    assert summary["critical_failed"] == []


def test_skip_on_critical_counts_as_fail_and_forces_low_score() -> None:
    items = [_item("A1", "SKIP"), _item("minor-check", "PASS")]

    summary = audit.summarize_audit_items(items)

    assert summary["verdict"] == "FAIL"
    assert summary["score_percent"] == 0.0
    assert summary["critical_failed"][0]["status"] == "SKIP"


def test_complete_critical_fixture_scores_high() -> None:
    items = [_item(id_, "PASS") for id_ in sorted(audit.CRITICAL_CHECK_IDS)]
    items.append(_item("minor-check", "PASS_STRICT"))

    summary = audit.summarize_audit_items(items)

    assert summary["verdict"] == "PASS"
    assert summary["score_percent"] == 100.0
    assert summary["score_10"] == 10.0
    assert summary["critical_failed"] == []


def test_audit_missing_required_artifacts_emits_fail_and_low_score(tmp_path: Path) -> None:
    session = tmp_path / "missing-artifacts"
    session.mkdir()

    proc = subprocess.run(
        [sys.executable, "bin/prd_compliance_audit.py", str(session), "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(proc.stdout)

    assert proc.returncode == 1
    assert report["verdict"] == "FAIL"
    assert report["score_percent"] == 0.0
    assert {item["id"] for item in report["critical_failed"]} >= {"A1", "A2", "A3", "A4"}


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_audit_frozen_video_emits_fail_and_low_score(tmp_path: Path) -> None:
    session = tmp_path / "frozen-video"
    session.mkdir()
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:r=10:d=2",
            "-frames:v",
            "20",
            "-c:v",
            "mpeg4",
            str(session / "video.mp4"),
        ],
        check=True,
        capture_output=True,
    )

    proc = subprocess.run(
        [sys.executable, "bin/prd_compliance_audit.py", str(session), "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(proc.stdout)

    assert proc.returncode == 1
    assert report["verdict"] == "FAIL"
    assert report["score_percent"] == 0.0
    assert any(item["id"] == "B8" for item in report["critical_failed"])

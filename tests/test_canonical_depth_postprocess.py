from __future__ import annotations

import json
import pathlib
import subprocess
from typing import Any

from bin import canonical_pipeline


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _make_gate_session(tmp_path: pathlib.Path, kind: str) -> pathlib.Path:
    sess = tmp_path / f"session_{kind}"
    depth = sess / "depth"
    depth.mkdir(parents=True)
    (depth / ".source").write_text(f"kind: {kind}\n")
    (sess / "input_latency.json").write_text(json.dumps({"latencies": [12.0]}))
    (sess / "audio.flac").write_bytes(b"a" * (64 * 1024))
    (sess / "action_camera.json").write_text(json.dumps([{} for _ in range(9000)]))
    (sess / "MANIFEST.json").write_text(json.dumps({"files": {}}))
    return sess


def _fake_gate_subprocess_run(cmd: list[str], **_: Any) -> _Proc:
    joined = " ".join(map(str, cmd))
    if "prd_compliance_audit.py" in joined:
        return _Proc(json.dumps({"items": []}))
    if "sync_tolerance_gate.py" in joined:
        return _Proc(json.dumps({"verdict": "PASS_STRICT"}))
    if "video_quality_gate.py" in joined:
        return _Proc(json.dumps({"verdict": "PASS"}))
    if "video_artifact_scanner.py" in joined:
        return _Proc(json.dumps({"verdict": "PASS"}))
    return _Proc(returncode=1)


def test_strict_upload_gate_accepts_monocular_da_v2(tmp_path, monkeypatch) -> None:
    sess = _make_gate_session(tmp_path, "monocular_da_v2")
    monkeypatch.setattr(canonical_pipeline.subprocess, "run", _fake_gate_subprocess_run)

    allowed, blocked = canonical_pipeline.step12_upload_gate_strict(sess)

    assert allowed is True
    assert blocked == []


def test_strict_upload_gate_blocks_missing_depth_source(tmp_path, monkeypatch) -> None:
    sess = _make_gate_session(tmp_path, "missing")
    monkeypatch.setattr(canonical_pipeline.subprocess, "run", _fake_gate_subprocess_run)

    allowed, blocked = canonical_pipeline.step12_upload_gate_strict(sess)

    assert allowed is False
    assert any(reason.startswith("G2: depth source = 'missing'") for reason in blocked)
    assert all("depth_zbuffer_capture.diff" not in reason for reason in blocked)


def test_step8_depth_writes_monocular_source_after_local_onnx_run(
    tmp_path,
    monkeypatch,
) -> None:
    sess = tmp_path / "session"
    frames = sess / "frames_for_depth"
    frames.mkdir(parents=True)
    (frames / "000000.png").write_bytes(b"not decoded in this unit test")

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
        calls.append(cmd)
        assert "run_da_v2_depth_onnx.py" in " ".join(cmd)
        depth = sess / "depth"
        depth.mkdir(parents=True, exist_ok=True)
        (depth / "000000.exr").write_bytes(b"exr")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.delenv("OYSTER_DEPTH_ENDPOINT", raising=False)
    monkeypatch.delenv("MODAL_TOKEN", raising=False)
    monkeypatch.setattr(canonical_pipeline, "run", fake_run)

    canonical_pipeline.step8_depth(sess)

    assert calls
    assert "kind: monocular_da_v2\n" in (sess / "depth" / ".source").read_text()


def test_step8_depth_prefers_reachable_remote_endpoint(tmp_path, monkeypatch) -> None:
    sess = tmp_path / "session"
    frames = sess / "frames_for_depth"
    frames.mkdir(parents=True)
    (frames / "000000.png").write_bytes(b"not decoded in this unit test")

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
        calls.append(cmd)
        assert "run_da_v2_depth_remote.py" in " ".join(cmd)
        depth = sess / "depth"
        depth.mkdir(parents=True, exist_ok=True)
        (depth / "000000.exr").write_bytes(b"exr")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setenv("OYSTER_DEPTH_ENDPOINT", "https://depth.example.test")
    monkeypatch.setattr(canonical_pipeline, "_depth_endpoint_reachable", lambda *_: True)
    monkeypatch.setattr(canonical_pipeline, "run", fake_run)

    canonical_pipeline.step8_depth(sess)

    assert calls
    assert "--endpoint" in calls[0]
    assert "kind: monocular_da_v2\n" in (sess / "depth" / ".source").read_text()

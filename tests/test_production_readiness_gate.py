"""Tests for the machine-checkable production readiness gate."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.production_readiness_gate import GateConfig, evaluate_gate, main


def _strict_report(
    *,
    signed: bool = True,
    no_gui: bool = False,
    strict: bool = True,
    rows: int = 60,
    video_bytes: int = 204800,
    manifest_bytes: int = 512,
    uploads_delta: int = 1,
    sessions_delta: int = 0,
) -> dict:
    return {
        "no_gui_preflight": no_gui,
        "installer": {
            "authenticode_status": "Valid" if signed else "NotSigned",
        },
        "admin_state": {
            "require_upload_delta": True,
            "delta": {
                "uploads": uploads_delta,
                "sessions": sessions_delta,
            },
        },
        "real_session": {
            "strict": strict,
            "game_state": {"rows": rows},
            "video": {"size_bytes": video_bytes},
            "manifest": {"size_bytes": manifest_bytes},
        },
    }


def _write_report(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "real-session-report.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _config(report: Path | None, **overrides) -> GateConfig:
    values = {
        "mode": "production",
        "backend_url": "https://api.oyster.test",
        "expected_release_tag": "v0.11.11",
        "real_session_report": report,
        "oauth_provider": "google",
        "storage_provider": "r2",
        "payout_provider": "stripe",
    }
    values.update(overrides)
    return GateConfig(**values)


def test_internal_mode_warns_but_passes_without_production_evidence() -> None:
    report = evaluate_gate(
        _config(
            None,
            mode="internal",
            backend_url="http://136.109.41.170:8081",
            oauth_provider="mock",
            storage_provider="local",
            payout_provider="simulator",
        )
    )

    assert report.passed is True
    assert "backend-url-https" in report.summary()
    assert "[WARN]" in report.summary()


def test_production_mode_passes_with_https_signed_strict_evidence_and_real_providers(
    tmp_path: Path,
) -> None:
    report_path = _write_report(tmp_path, _strict_report())

    report = evaluate_gate(_config(report_path))

    assert report.passed is True
    summary = report.summary()
    assert "installer-authenticode-valid" in summary
    assert "backend-upload-or-session-delta" in summary


def test_production_mode_fails_without_https_or_real_providers(tmp_path: Path) -> None:
    report_path = _write_report(tmp_path, _strict_report())

    report = evaluate_gate(
        _config(
            report_path,
            backend_url="http://136.109.41.170:8081",
            oauth_provider="mock",
            storage_provider="local",
            payout_provider="simulator",
        )
    )

    assert report.passed is False
    summary = report.summary()
    assert "[FAIL] backend-url-https" in summary
    assert "[FAIL] oauth-provider-real" in summary
    assert "[FAIL] storage-provider-real" in summary
    assert "[FAIL] payout-provider-real" in summary


def test_production_mode_fails_without_real_session_report() -> None:
    report = evaluate_gate(_config(None))

    assert report.passed is False
    assert "[FAIL] strict-real-session-report-present" in report.summary()


def test_production_mode_fails_for_no_gui_or_unsigned_or_missing_artifacts(tmp_path: Path) -> None:
    report_path = _write_report(
        tmp_path,
        _strict_report(
            signed=False,
            no_gui=True,
            rows=0,
            video_bytes=0,
            manifest_bytes=0,
            uploads_delta=0,
            sessions_delta=0,
        ),
    )

    report = evaluate_gate(_config(report_path))

    assert report.passed is False
    summary = report.summary()
    assert "[FAIL] no-gui-preflight-disabled" in summary
    assert "[FAIL] installer-authenticode-valid" in summary
    assert "[FAIL] fresh-game-state-rows" in summary
    assert "[FAIL] fresh-video-bytes" in summary
    assert "[FAIL] fresh-manifest-present" in summary
    assert "[FAIL] backend-upload-or-session-delta" in summary


def test_cli_exits_nonzero_for_production_blockers(tmp_path: Path, capsys) -> None:
    report_path = _write_report(tmp_path, _strict_report(signed=False))

    code = main(
        [
            "--mode",
            "production",
            "--backend-url",
            "https://api.oyster.test",
            "--expected-release-tag",
            "v0.11.11",
            "--real-session-report",
            str(report_path),
            "--oauth-provider",
            "google",
            "--storage-provider",
            "r2",
            "--payout-provider",
            "stripe",
        ]
    )

    assert code == 1
    assert "installer-authenticode-valid" in capsys.readouterr().out

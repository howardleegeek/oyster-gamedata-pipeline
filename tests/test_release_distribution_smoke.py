"""Tests for the latest-release distribution smoke workflow."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_release_smoke_script_checks_installer_asset() -> None:
    script = (REPO_ROOT / "scripts" / "verify_latest_release_assets.sh").read_text()

    assert "OysterRecorder-[Ss]etup-" in script
    assert "SHA256SUMS.txt" in script
    assert "curl -fsSIL -L" in script
    assert "## Windows installer" in script
    assert "SmartScreen" in script
    assert "sha256:" in script


def test_release_smoke_workflow_runs_on_schedule_and_manual_dispatch() -> None:
    workflow = (REPO_ROOT / ".github/workflows/release-distribution-smoke.yml").read_text()

    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "contents: read" in workflow
    assert "bash scripts/verify_latest_release_assets.sh" in workflow

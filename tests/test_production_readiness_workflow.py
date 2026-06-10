"""Contract tests for the Production Readiness Gate workflow."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production-readiness-gate.yml"


def test_production_readiness_workflow_runs_internal_gate_on_push() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "name: Production Readiness Gate" in text
    assert "push:" in text
    assert "workflow_dispatch:" in text
    assert "GATE_MODE: ${{ inputs.mode || 'internal' }}" in text
    assert (
        "BACKEND_URL: ${{ inputs.backend_url || vars.BACKEND_SMOKE_URL || 'https://oyster-backend-6qup7rrx2q-uc.a.run.app' }}"
        in text
    )
    assert "python3 scripts/production_readiness_gate.py" in text
    assert '--mode "$GATE_MODE"' in text
    assert '--backend-url "$BACKEND_URL"' in text


def test_production_readiness_workflow_can_accept_strict_report_path() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "real_session_report_path" in text
    assert "REAL_SESSION_REPORT_PATH" in text
    assert 'args+=(--real-session-report "$REAL_SESSION_REPORT_PATH")' in text


def test_production_readiness_workflow_can_accept_installer_signature_status() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "installer_authenticode_status" in text
    assert "INSTALLER_AUTHENTICODE_STATUS" in text
    assert '--installer-authenticode-status "$INSTALLER_AUTHENTICODE_STATUS"' in text


def test_production_readiness_workflow_uploads_machine_readable_report() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "--json-output production-readiness-report.json" in text
    assert "actions/upload-artifact@v4" in text
    assert "production-readiness-report" in text

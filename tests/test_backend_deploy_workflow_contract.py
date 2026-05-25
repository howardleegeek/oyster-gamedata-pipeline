"""Contract tests for backend deploy and remote smoke workflows.

These tests make the current public backend blocker explicit without needing
Fly credentials or a deployed backend.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
DEPLOY_WORKFLOW = WORKFLOWS / "deploy-backend-fly.yml"
SMOKE_WORKFLOW = WORKFLOWS / "backend-remote-smoke.yml"
AUTO_RELEASE_WORKFLOW = WORKFLOWS / "auto-release.yml"


def _load_workflow(path: Path) -> dict:
    assert path.exists(), f"missing workflow file: {path}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path} must parse as a YAML mapping"
    return data


def _on_block(workflow: dict) -> dict:
    # PyYAML still treats the GitHub Actions key "on" as boolean True.
    on = workflow.get("on", workflow.get(True))
    assert isinstance(on, dict), f"workflow has no trigger mapping: {workflow.keys()}"
    return on


def _all_step_text(workflow: dict) -> str:
    chunks: list[str] = []
    for job in workflow.get("jobs", {}).values():
        if isinstance(job, dict):
            chunks.extend(str(value) for value in job.get("env", {}).values())
            for step in job.get("steps", []):
                if isinstance(step, dict):
                    chunks.extend(str(value) for value in step.values())
    return "\n".join(chunks)


def test_deploy_workflow_requires_fly_token_and_fails_closed() -> None:
    workflow = _load_workflow(DEPLOY_WORKFLOW)
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    step_text = _all_step_text(workflow)

    assert "${{ secrets.FLY_API_TOKEN }}" in text
    assert "Missing repo secret FLY_API_TOKEN" in step_text
    assert "exit 1" in step_text


def test_deploy_workflow_uses_remote_fly_deploy_and_verifies_backend() -> None:
    workflow = _load_workflow(DEPLOY_WORKFLOW)
    step_text = _all_step_text(workflow)

    assert "flyctl deploy backend_stub" in step_text
    assert "--config backend_stub/fly.toml" in step_text
    assert "--remote-only" in step_text
    assert "scripts/verify_deployed_backend.py" in step_text
    assert "Resolve latest recorder release metadata" in step_text
    assert "Configure recorder release metadata" in step_text
    assert "flyctl secrets set" in step_text
    assert "OYSTER_RECORDER_RELEASE_TAG" in step_text
    assert "OYSTER_RECORDER_DOWNLOAD_URL" in step_text
    assert "OYSTER_RECORDER_SHA256" in step_text
    assert "--expected-recorder-tag" in step_text
    assert "bin/remote_recorder_backend_e2e.py" in step_text
    assert '--backend-url "$BACKEND_URL"' in step_text


def test_remote_smoke_supports_manual_dispatch_and_scheduled_guard() -> None:
    workflow = _load_workflow(SMOKE_WORKFLOW)
    on = _on_block(workflow)
    smoke_job = workflow["jobs"]["smoke"]

    assert "release" in on
    assert on["release"]["types"] == ["published"]
    assert "workflow_dispatch" in on
    assert "schedule" in on
    assert smoke_job["if"] == (
        "${{ github.event_name == 'workflow_dispatch' || github.event_name == 'release' || vars.BACKEND_SMOKE_URL != '' }}"
    )
    smoke_text = SMOKE_WORKFLOW.read_text(encoding="utf-8")
    assert "default: https://136-109-41-170.sslip.io" in smoke_text
    assert (
        "${{ inputs.backend_url || vars.BACKEND_SMOKE_URL || 'https://136-109-41-170.sslip.io' }}"
        in smoke_text
    )


def test_remote_smoke_runs_the_same_backend_verifier() -> None:
    workflow = _load_workflow(SMOKE_WORKFLOW)
    step_text = _all_step_text(workflow)

    assert "scripts/verify_deployed_backend.py" in step_text
    assert '--url "$BACKEND_URL"' in step_text
    assert "--verbose" in step_text
    assert "Resolve latest recorder release tag" in step_text
    assert "RELEASE_EVENT_TAG" in step_text
    assert "--expected-recorder-tag" in step_text
    assert "bin/remote_recorder_backend_e2e.py" in step_text
    assert '--backend-url "$BACKEND_URL"' in step_text


def test_auto_release_syncs_and_verifies_gcp_backend_appcast() -> None:
    workflow = _load_workflow(AUTO_RELEASE_WORKFLOW)
    step_text = _all_step_text(workflow)
    raw_text = AUTO_RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "scripts/auto_release.sh" in step_text
    assert "Install backend smoke dependencies" in raw_text
    assert "GCP_BACKEND_SSH_KEY" in raw_text
    assert "Host gamedata-backend" in step_text
    assert "scripts/sync_gcp_backend_release.sh" in step_text
    assert "RUN_E2E" in step_text
    assert "Verify backend appcast matches release" in raw_text
    assert "scripts/verify_deployed_backend.py" in step_text
    assert '--expected-recorder-tag "$RECORDER_RELEASE_TAG"' in step_text

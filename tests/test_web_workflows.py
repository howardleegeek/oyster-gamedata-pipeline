"""Contract tests for the Vercel auto-deploy workflows.

Verifies that .github/workflows/deploy-web-tester.yml and
.github/workflows/deploy-web-buyer.yml exist and meet the deployment
contract:

  * triggers ONLY on the matching web-{role}/ paths (path filter present)
  * does NOT trigger on the sibling portal's paths (no cross-pollination)
  * uses Node.js 20
  * sets timeout-minutes (so a hung deploy can't burn 6h of runner time)
  * references VERCEL_TOKEN somewhere in the workflow (proper Vercel CLI
    usage — the iron-law check that we are wired to deploy a real Vercel
    site, not a stub)

This is a contract test, not an integration test — we don't actually run
GitHub Actions. We parse the YAML and assert structural invariants.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
TESTER_WF = WORKFLOWS_DIR / "deploy-web-tester.yml"
BUYER_WF = WORKFLOWS_DIR / "deploy-web-buyer.yml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tester_workflow() -> dict:
    """Parse deploy-web-tester.yml as YAML.

    PyYAML interprets the bare key ``on:`` as the boolean ``True`` (YAML 1.1
    quirk), so we accept either ``"on"`` or ``True`` as the trigger key.
    """
    assert TESTER_WF.exists(), f"missing workflow file: {TESTER_WF}"
    return yaml.safe_load(TESTER_WF.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def buyer_workflow() -> dict:
    assert BUYER_WF.exists(), f"missing workflow file: {BUYER_WF}"
    return yaml.safe_load(BUYER_WF.read_text(encoding="utf-8"))


def _on_block(wf: dict) -> dict:
    """Return the ``on:`` block, accommodating PyYAML's True-key quirk."""
    if "on" in wf:
        return wf["on"]
    if True in wf:
        return wf[True]
    raise AssertionError(f"workflow has no on: block; keys={list(wf.keys())}")


def _all_runs(wf: dict) -> list[str]:
    """Concatenate every step's run: block + every env value across the workflow.

    Used for substring checks (e.g. is VERCEL_TOKEN referenced anywhere?).
    Includes the raw YAML text so checks catch references in env: blocks too.
    """
    parts: list[str] = []
    # Top-level env (some workflows put creds here).
    for v in (wf.get("env") or {}).values():
        if isinstance(v, str):
            parts.append(v)
    for job in (wf.get("jobs") or {}).values():
        for v in (job.get("env") or {}).values():
            if isinstance(v, str):
                parts.append(v)
        for step in job.get("steps") or []:
            for v in (step.get("env") or {}).values():
                if isinstance(v, str):
                    parts.append(v)
            run = step.get("run")
            if isinstance(run, str):
                parts.append(run)
    return parts


def _raw_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# tester workflow contract
# ---------------------------------------------------------------------------


def test_tester_workflow_exists():
    assert TESTER_WF.is_file(), (
        f"deploy-web-tester.yml must exist at {TESTER_WF.relative_to(REPO_ROOT)}"
    )


def test_tester_workflow_path_filter_includes_web_tester(tester_workflow):
    on = _on_block(tester_workflow)
    push = on.get("push")
    assert push is not None, "push trigger missing"
    paths = push.get("paths")
    assert paths, "paths filter missing — workflow would run on every push"
    assert any(p.startswith("web-tester/") for p in paths), (
        f"web-tester/ path filter missing; got paths={paths}"
    )


def test_tester_workflow_does_not_trigger_on_buyer_paths(tester_workflow):
    """Tester deploy must not be wired to web-buyer/ paths."""
    on = _on_block(tester_workflow)
    paths = on["push"]["paths"]
    for p in paths:
        assert not p.startswith("web-buyer/"), (
            f"tester workflow should NOT trigger on web-buyer paths; found {p!r}"
        )


def test_tester_workflow_does_not_trigger_on_root_push(tester_workflow):
    """No bare wildcard like '**' or '*' that would catch root-level pushes."""
    on = _on_block(tester_workflow)
    paths = on["push"]["paths"]
    forbidden = {"**", "*", ".", "./**"}
    overlap = forbidden.intersection(paths)
    assert not overlap, (
        f"tester workflow has overly-broad paths {overlap!r} that match the repo root"
    )


def test_tester_workflow_uses_node_20(tester_workflow):
    found_node_20 = False
    for job in tester_workflow["jobs"].values():
        for step in job.get("steps") or []:
            uses = step.get("uses") or ""
            if "actions/setup-node" in uses:
                node_version = (step.get("with") or {}).get("node-version")
                # Compare as string to dodge int vs '20.x' mismatch.
                if str(node_version).strip() == "20":
                    found_node_20 = True
    assert found_node_20, "actions/setup-node@v* with node-version: 20 not found"


def test_tester_workflow_has_timeout_minutes(tester_workflow):
    for job_name, job in tester_workflow["jobs"].items():
        timeout = job.get("timeout-minutes")
        assert timeout is not None, (
            f"job {job_name!r} missing timeout-minutes — a hung deploy would burn "
            "the default 6h of runner time"
        )
        assert isinstance(timeout, int) and 1 <= timeout <= 60, (
            f"job {job_name!r} has unreasonable timeout-minutes={timeout!r}"
        )


def test_tester_workflow_references_vercel_token():
    """Iron-law check: the workflow must wire VERCEL_TOKEN through somewhere.

    Proper Vercel CLI usage requires VERCEL_TOKEN. We do a substring check
    on the raw YAML so we catch references in env: blocks, secrets:, run:
    blocks, etc. without having to model every possible YAML shape.
    """
    text = _raw_text(TESTER_WF)
    assert "VERCEL_TOKEN" in text, (
        "deploy-web-tester.yml does not reference VERCEL_TOKEN — "
        "Vercel CLI cannot deploy without it. See docs/VERCEL_DEPLOY_SETUP.md"
    )


# ---------------------------------------------------------------------------
# buyer workflow contract — mirrors tester
# ---------------------------------------------------------------------------


def test_buyer_workflow_exists():
    assert BUYER_WF.is_file(), (
        f"deploy-web-buyer.yml must exist at {BUYER_WF.relative_to(REPO_ROOT)}"
    )


def test_buyer_workflow_path_filter_includes_web_buyer(buyer_workflow):
    on = _on_block(buyer_workflow)
    push = on.get("push")
    assert push is not None, "push trigger missing"
    paths = push.get("paths")
    assert paths, "paths filter missing — workflow would run on every push"
    assert any(p.startswith("web-buyer/") for p in paths), (
        f"web-buyer/ path filter missing; got paths={paths}"
    )


def test_buyer_workflow_does_not_trigger_on_tester_paths(buyer_workflow):
    on = _on_block(buyer_workflow)
    paths = on["push"]["paths"]
    for p in paths:
        assert not p.startswith("web-tester/"), (
            f"buyer workflow should NOT trigger on web-tester paths; found {p!r}"
        )


def test_buyer_workflow_does_not_trigger_on_root_push(buyer_workflow):
    on = _on_block(buyer_workflow)
    paths = on["push"]["paths"]
    forbidden = {"**", "*", ".", "./**"}
    overlap = forbidden.intersection(paths)
    assert not overlap, (
        f"buyer workflow has overly-broad paths {overlap!r} that match the repo root"
    )


def test_buyer_workflow_uses_node_20(buyer_workflow):
    found_node_20 = False
    for job in buyer_workflow["jobs"].values():
        for step in job.get("steps") or []:
            uses = step.get("uses") or ""
            if "actions/setup-node" in uses:
                node_version = (step.get("with") or {}).get("node-version")
                if str(node_version).strip() == "20":
                    found_node_20 = True
    assert found_node_20, "actions/setup-node@v* with node-version: 20 not found"


def test_buyer_workflow_has_timeout_minutes(buyer_workflow):
    for job_name, job in buyer_workflow["jobs"].items():
        timeout = job.get("timeout-minutes")
        assert timeout is not None, (
            f"job {job_name!r} missing timeout-minutes — a hung deploy would burn "
            "the default 6h of runner time"
        )
        assert isinstance(timeout, int) and 1 <= timeout <= 60, (
            f"job {job_name!r} has unreasonable timeout-minutes={timeout!r}"
        )


def test_buyer_workflow_references_vercel_token():
    text = _raw_text(BUYER_WF)
    assert "VERCEL_TOKEN" in text, (
        "deploy-web-buyer.yml does not reference VERCEL_TOKEN — "
        "Vercel CLI cannot deploy without it. See docs/VERCEL_DEPLOY_SETUP.md"
    )


# ---------------------------------------------------------------------------
# Shared sanity — both workflows independent and well-formed
# ---------------------------------------------------------------------------


def test_workflows_target_distinct_projects(tester_workflow, buyer_workflow):
    """Tester and buyer must reference different VERCEL_PROJECT_ID secrets."""
    tester_text = _raw_text(TESTER_WF)
    buyer_text = _raw_text(BUYER_WF)
    assert "VERCEL_PROJECT_ID_TESTER" in tester_text, (
        "tester workflow should reference VERCEL_PROJECT_ID_TESTER (per-portal Vercel project)"
    )
    assert "VERCEL_PROJECT_ID_BUYER" in buyer_text, (
        "buyer workflow should reference VERCEL_PROJECT_ID_BUYER (per-portal Vercel project)"
    )
    assert "VERCEL_PROJECT_ID_BUYER" not in tester_text, (
        "tester workflow leaks VERCEL_PROJECT_ID_BUYER — would deploy to wrong Vercel project"
    )
    assert "VERCEL_PROJECT_ID_TESTER" not in buyer_text, (
        "buyer workflow leaks VERCEL_PROJECT_ID_TESTER — would deploy to wrong Vercel project"
    )


def test_workflows_have_deploy_hook_references():
    """Both workflows expose the deploy-hook secret name as the docs promise."""
    assert "VERCEL_TESTER_DEPLOY_HOOK" in _raw_text(TESTER_WF)
    assert "VERCEL_BUYER_DEPLOY_HOOK" in _raw_text(BUYER_WF)


def test_workflows_run_on_push_to_main(tester_workflow, buyer_workflow):
    for wf, name in [(tester_workflow, "tester"), (buyer_workflow, "buyer")]:
        on = _on_block(wf)
        branches = on["push"].get("branches")
        assert branches and "main" in branches, (
            f"{name} workflow does not target main branch; got branches={branches!r}"
        )

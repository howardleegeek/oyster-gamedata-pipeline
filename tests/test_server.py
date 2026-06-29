"""Tests for ``oyster_agent_runner.server`` — FastAPI HTTP API.

These tests exercise the auth gate, each endpoint's happy path, and the
request-validation surface. The runner is mocked end-to-end (via the
``runner_callable`` injection seam on ``create_app``) so no Anthropic
tokens are spent and no real game env is spawned.

FastAPI is a soft optional dependency. If it isn't installed in the active
env, every test in this module is skipped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# Hard-skip the entire module if fastapi isn't installed. Same posture as
# tests that gate on optional cloud SDKs.
fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient


# --- Fixtures ---------------------------------------------------------------


_TEST_TOKEN = "test-token-123"


def _phase1_task_payload() -> dict[str, Any]:
    """Mirror the shape of ``tasks/MC-tutorial-001.json`` exactly.

    Keep this in sync with `tests/test_quote.py::_phase1_task_payload` —
    the build_quote helper strips Phase-1 extras the same way `run-mc` does.
    """
    return {
        "task_id": "test-server-task",
        "natural_language_instruction": "Mine one log.",
        "success_criteria": ["wall placed"],
        "max_steps": 50,
        "target_hours": 0.1,
        "environment": "minecraft",
        "required_provider_model": "claude-sonnet-4-5",
        "world_seed": 42,
        "spawn_position": None,
        "max_minutes": 5,
        "thinking_budget_tokens": 16000,
        "model_required": "claude-sonnet-4-5",
    }


@pytest.fixture
def fake_runner() -> Any:
    """A no-op runner stub. Records its inputs so tests can introspect."""
    calls: list[dict[str, Any]] = []

    def _runner(task_payload: dict[str, Any], provider: str, output_dir: Path) -> tuple[str, str]:
        calls.append({"task": task_payload, "provider": provider, "output_dir": str(output_dir)})
        # Synthesize a manifest path under the requested dir so the buyer
        # can poll there once the run finishes.
        traj_id = task_payload.get("task_id", "fake-id")
        manifest = Path(output_dir) / traj_id / "manifest.json"
        return traj_id, str(manifest)

    _runner.calls = calls  # type: ignore[attr-defined]
    return _runner


@pytest.fixture
def client(fake_runner: Any) -> TestClient:
    from oyster_agent_runner.server import create_app

    app = create_app(api_token=_TEST_TOKEN, runner_callable=fake_runner)
    return TestClient(app)


@pytest.fixture
def auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {_TEST_TOKEN}"}


# --- /healthz ---------------------------------------------------------------


def test_healthz_returns_ok_without_auth() -> None:
    """`/healthz` must answer 200 with no auth — platform liveness checks."""
    from oyster_agent_runner.server import create_app

    app = create_app(api_token=_TEST_TOKEN)
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


# --- Server refuses to launch without a token ------------------------------


def test_create_app_refuses_to_start_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Security default: no token configured → cannot start."""
    from oyster_agent_runner.server import create_app

    monkeypatch.delenv("OYSTER_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        create_app()
    assert "OYSTER_API_TOKEN" in str(excinfo.value)


# --- Auth gate --------------------------------------------------------------


def test_protected_endpoint_missing_auth_returns_401(client: TestClient) -> None:
    """No `Authorization` header → 401 from any /v1 endpoint."""
    response = client.get("/v1/providers")

    assert response.status_code == 401
    assert "Bearer" in response.headers.get("WWW-Authenticate", "")


def test_protected_endpoint_wrong_auth_returns_401(client: TestClient) -> None:
    """Wrong token → 401 (constant-time match isn't enforced — adversaries
    deserve a clear signal that the token doesn't match, not a timing oracle)."""
    response = client.get("/v1/providers", headers={"Authorization": "Bearer not-the-right-token"})

    assert response.status_code == 401


def test_protected_endpoint_wrong_scheme_returns_401(client: TestClient) -> None:
    """Non-Bearer scheme (Basic, etc.) → 401 — we only accept Bearer."""
    response = client.get("/v1/providers", headers={"Authorization": "Basic dGVzdDp0ZXN0"})

    assert response.status_code == 401


# --- /v1/providers ----------------------------------------------------------


def test_list_providers_returns_registry_shape(
    client: TestClient, auth_header: dict[str, str]
) -> None:
    """Mirrors `oyster-agent list-providers --json` — list of {key, status, description}."""
    response = client.get("/v1/providers", headers=auth_header)

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) >= 5  # mock + claude + openai + claude-vision + openai-vision (at least)
    keys = {row["key"] for row in payload}
    assert "mock" in keys
    assert "claude" in keys
    # Every row carries the three-field shape
    for row in payload:
        assert set(row.keys()) >= {"key", "status", "description"}


# --- /v1/run-task -----------------------------------------------------------


def test_run_task_happy_path_returns_202_and_location(
    client: TestClient,
    auth_header: dict[str, str],
    fake_runner: Any,
    tmp_path: Path,
) -> None:
    """Successful dispatch → 202 + Location header + JSON body with ids."""
    body = {
        "task": _phase1_task_payload(),
        "provider": "mock",
        "output_dir": str(tmp_path),
    }
    response = client.post("/v1/run-task", headers=auth_header, json=body)

    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["trajectory_id"] == "test-server-task"
    assert payload["manifest_path"].endswith("manifest.json")
    # Location header is REQUIRED for async pickup.
    assert "Location" in response.headers
    assert response.headers["Location"] == payload["manifest_path"]

    # The mock runner saw the request — sanity check the seam.
    assert len(fake_runner.calls) == 1
    assert fake_runner.calls[0]["provider"] == "mock"


def test_run_task_invalid_body_returns_422(client: TestClient, auth_header: dict[str, str]) -> None:
    """Missing required `task` field → FastAPI 422 (request validation)."""
    response = client.post(
        "/v1/run-task",
        headers=auth_header,
        json={"provider": "mock", "output_dir": "/tmp"},
    )

    assert response.status_code == 422


def test_run_task_no_auth_returns_401(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/v1/run-task",
        json={
            "task": _phase1_task_payload(),
            "provider": "mock",
            "output_dir": str(tmp_path),
        },
    )
    assert response.status_code == 401


# --- /v1/replay -------------------------------------------------------------


def _write_phase1_bundle(tmp_path: Path) -> Path:
    """Spin up a minimal Phase 1 bundle by running the runner with a mock env.

    Returns the path to manifest.json.
    """
    from datetime import datetime, timezone

    UTC = timezone.utc

    from oyster_agent_runner.environments.base import MockEnvironment
    from oyster_agent_runner.minecraft_streams import MinecraftStreamWriter
    from oyster_agent_runner.providers.base import MockLLMProvider
    from oyster_agent_runner.runner import AgentRunner, RunnerConfig
    from oyster_agent_runner.schema import AgentTask, TrajectoryEvent

    bundle_dir = tmp_path / "bundle"
    task = AgentTask(
        task_id="server-replay-001",
        natural_language_instruction="noop",
        max_steps=3,
        environment="mock",
        required_provider_model="mock",
    )
    runner = AgentRunner(RunnerConfig(write_frames=False))
    result = runner.run(
        task,
        MockEnvironment(done_after_steps=3),
        MockLLMProvider(canned_action={"op": "noop"}),
        bundle_dir,
    )
    # Demux the trajectory into the 3-stream format + manifest, mirroring
    # what the `run-mc` CLI does.
    trajectory_path = Path(result.trajectory_path)
    with MinecraftStreamWriter(bundle_dir) as streams:
        with trajectory_path.open(encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                event = TrajectoryEvent.model_validate(payload)
                streams.write(event)
        streams.finalize_manifest(
            task_id=task.task_id,
            model="mock",
            provider="mock",
            environment="mock",
            anchor_utc=datetime.now(UTC),
            success=result.success,
            termination_reason=result.termination_reason,
            total_steps=result.total_steps,
            wall_clock_sec=result.wall_clock_sec,
            thinking_budget_tokens=None,
        )
    return bundle_dir / "manifest.json"


def test_replay_check_mode_returns_consistency_report(
    client: TestClient, auth_header: dict[str, str], tmp_path: Path
) -> None:
    """`mode=check` returns a ConsistencyReport-shaped dict."""
    manifest = _write_phase1_bundle(tmp_path)
    response = client.post(
        "/v1/replay",
        headers=auth_header,
        json={"manifest_path": str(manifest), "mode": "check"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "ok" in payload
    assert "step_count" in payload
    assert "issues" in payload
    assert isinstance(payload["issues"], list)


def test_replay_re_execute_mode_returns_drift_report(
    client: TestClient, auth_header: dict[str, str], tmp_path: Path
) -> None:
    """`mode=re-execute` returns a ReplayDriftReport-shaped dict."""
    manifest = _write_phase1_bundle(tmp_path)
    response = client.post(
        "/v1/replay",
        headers=auth_header,
        json={"manifest_path": str(manifest), "mode": "re-execute"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    # ReplayDriftReport has `ok` as a @property — server.py manually adds it.
    assert "ok" in payload
    assert "steps_executed" in payload
    assert "steps_diverged" in payload


def test_replay_invalid_mode_returns_400(
    client: TestClient, auth_header: dict[str, str], tmp_path: Path
) -> None:
    """Unknown `mode` is rejected at the application level (400, not 422)."""
    manifest = _write_phase1_bundle(tmp_path)
    response = client.post(
        "/v1/replay",
        headers=auth_header,
        json={"manifest_path": str(manifest), "mode": "bogus"},
    )

    assert response.status_code == 400


def test_replay_missing_manifest_returns_404(
    client: TestClient, auth_header: dict[str, str], tmp_path: Path
) -> None:
    response = client.post(
        "/v1/replay",
        headers=auth_header,
        json={"manifest_path": str(tmp_path / "does_not_exist.json"), "mode": "check"},
    )

    assert response.status_code == 404


# --- /v1/quote --------------------------------------------------------------


def test_quote_happy_path(client: TestClient, auth_header: dict[str, str]) -> None:
    """`/v1/quote` returns the same shape as `oyster-agent quote --json`."""
    body = {
        "task": _phase1_task_payload(),
        "provider": "claude-thinking",
        "steps": 50,
        "thinking_budget": 16_000,
    }
    response = client.post("/v1/quote", headers=auth_header, json=body)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["steps"] == 50
    assert payload["provider_key"] == "claude-thinking"
    assert payload["per_step"]["thinking_tokens"] == 16_000
    assert payload["trajectory"]["cost_low_usd"] > 0
    # Sales-grade math sanity: at 50 steps + 16K thinking we land near the
    # runbook's $13 claim. Use the same loose ±$2 envelope as test_quote.py.
    assert 12.0 <= payload["trajectory"]["cost_low_usd"] <= 16.0


def test_quote_unknown_provider_returns_400(
    client: TestClient, auth_header: dict[str, str]
) -> None:
    body = {
        "task": _phase1_task_payload(),
        "provider": "bogus-provider",
        "steps": 10,
        "thinking_budget": 0,
    }
    response = client.post("/v1/quote", headers=auth_header, json=body)

    assert response.status_code == 400


def test_quote_negative_steps_returns_422(client: TestClient, auth_header: dict[str, str]) -> None:
    """Pydantic `ge=0` constraint catches bad input before it hits build_quote."""
    body = {
        "task": _phase1_task_payload(),
        "provider": "claude-thinking",
        "steps": -1,
        "thinking_budget": 0,
    }
    response = client.post("/v1/quote", headers=auth_header, json=body)

    assert response.status_code == 422

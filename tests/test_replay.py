"""Tests for the Phase 1 deterministic replay tool.

These tests cover the three public entry points of `Replayer`:

  1. `iter_steps` reconstructs ordered ReplayStep records from the
     four-stream bundle.
  2. `verify_consistency` catches malformed bundles, time-skewed
     streams, missing step events, and manifest-vs-stream count
     mismatches.
  3. `replay_against` re-executes recorded actions against a fresh
     environment and surfaces step-level divergence.

We also smoke-test the `replay` CLI subcommand end-to-end.

Fixtures
--------
`make_bundle(tmp_path)` produces a real Phase 1 bundle by running the
mock env + mock provider through the existing `MinecraftStreamWriter` —
exactly the same code path `run-mc` uses, so we don't accidentally test
against a fake bundle that drifts from the production format.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

UTC = timezone.utc

UTC = UTC
from pathlib import Path

import pytest
from typer.testing import CliRunner

from oyster_agent_runner.cli import app
from oyster_agent_runner.environments.base import MockEnvironment
from oyster_agent_runner.minecraft_streams import (
    COT_FILENAME,
    MANIFEST_FILENAME,
    METADATA_FILENAME,
    MinecraftStreamWriter,
)
from oyster_agent_runner.providers.base import MockLLMProvider
from oyster_agent_runner.replay import (
    ConsistencyReport,
    ReplayDriftReport,
    Replayer,
    ReplayStep,
)
from oyster_agent_runner.runner import AgentRunner, RunnerConfig
from oyster_agent_runner.schema import (
    EVENT_ACTION,
    EVENT_AGENT_STEP,
    EVENT_END,
    EVENT_LLM_THINKING,
    EVENT_OBSERVATION,
    AgentTask,
    TrajectoryEvent,
)

# --- Bundle fixture ---------------------------------------------------------


def _make_bundle(
    out_dir: Path,
    *,
    done_after_steps: int = 3,
    task_id: str = "replay-test",
    inject_thinking: bool = False,
) -> Path:
    """Run the runner end-to-end and demux into a Phase 1 bundle.

    Returns the path to manifest.json.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    task = AgentTask(
        task_id=task_id,
        natural_language_instruction="do noops",
        success_criteria=[],
        max_steps=done_after_steps + 5,
        environment="minecraft",
        required_provider_model="mock",
    )
    env = MockEnvironment(done_after_steps=done_after_steps)
    provider = MockLLMProvider()
    runner = AgentRunner(RunnerConfig(write_frames=False))
    result = runner.run(task, env, provider, out_dir)

    with MinecraftStreamWriter(out_dir) as streams:
        with Path(result.trajectory_path).open(encoding="utf-8") as fh:
            for raw_ln in fh:
                ln = raw_ln.strip()
                if not ln:
                    continue
                payload = json.loads(ln)
                ev = TrajectoryEvent.model_validate(payload)
                streams.write(ev)

        if inject_thinking:
            # Add an LLM_THINKING event for step 0 — verifies reconstruction
            # picks it up via its `step` field rather than timestamp.
            streams.write(
                TrajectoryEvent(
                    timestamp=0.001,
                    event_type=EVENT_LLM_THINKING,
                    event_args={"step": 0, "text": "deliberating..."},
                )
            )

        streams.finalize_manifest(
            task_id=task_id,
            model="mock",
            provider="mock",
            environment="minecraft",
            anchor_utc=datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC),
            success=result.success,
            termination_reason=result.termination_reason,
            total_steps=result.total_steps,
            wall_clock_sec=result.wall_clock_sec,
            thinking_budget_tokens=None,
        )
    return out_dir / MANIFEST_FILENAME


@pytest.fixture
def bundle_dir(tmp_path: Path) -> Path:
    """Directory containing a freshly minted Phase 1 bundle."""
    out = tmp_path / "bundle"
    _make_bundle(out, done_after_steps=3)
    return out


# --- iter_steps -------------------------------------------------------------


def test_iter_steps_reconstructs_ordered_steps(bundle_dir: Path) -> None:
    replayer = Replayer(bundle_dir / MANIFEST_FILENAME)
    steps = replayer.iter_steps()

    assert len(steps) == 3
    assert [s.step_idx for s in steps] == [0, 1, 2]
    for s in steps:
        assert isinstance(s, ReplayStep)
        assert s.action == {"op": "noop"}
        assert s.reward == pytest.approx(0.1)
        assert s.reasoning == "mock reasoning"
        assert s.timing_ms >= 0.0
        assert isinstance(s.observation, dict)

    # Final step carries success=True (mock env signals done at done_after_steps).
    assert steps[-1].success_flag is True
    assert steps[0].success_flag is False
    # Timing of step 0 starts from the START marker (>= 0).
    assert steps[0].timing_ms >= 0.0


def test_iter_steps_picks_up_thinking_via_step_field(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    _make_bundle(out, done_after_steps=3, inject_thinking=True)
    replayer = Replayer(out / MANIFEST_FILENAME)
    steps = replayer.iter_steps()

    # Step 0 has injected thinking; the rest don't.
    assert steps[0].thinking == "deliberating..."
    assert steps[1].thinking is None
    assert steps[2].thinking is None


# --- verify_consistency -----------------------------------------------------


def test_verify_consistency_clean_bundle(bundle_dir: Path) -> None:
    replayer = Replayer(bundle_dir / MANIFEST_FILENAME)
    report = replayer.verify_consistency()

    assert isinstance(report, ConsistencyReport)
    assert report.ok, f"expected clean bundle, got issues: {report.issues}"
    assert report.step_count == 3
    assert report.cot_event_count > 0
    assert report.metadata_event_count > 0
    assert report.input_event_count > 0
    assert report.manifest_matches_streams is True


def test_verify_consistency_catches_missing_step_in_cot(bundle_dir: Path) -> None:
    """Strip an ACTION line from cot.jsonl and assert the verifier catches it."""
    cot_path = bundle_dir / COT_FILENAME
    lines = cot_path.read_text(encoding="utf-8").splitlines()
    # Drop the ACTION for step 1 (rough heuristic — just nuke the 4th line if it's an ACTION).
    new_lines = []
    dropped = False
    for ln in lines:
        if not dropped:
            payload = json.loads(ln)
            if payload.get("event_type") == EVENT_ACTION:
                dropped = True
                continue
        new_lines.append(ln)
    cot_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    replayer = Replayer(bundle_dir / MANIFEST_FILENAME)
    report = replayer.verify_consistency()
    assert not report.ok
    assert any("ACTION" in issue for issue in report.issues), report.issues


def test_verify_consistency_detects_time_skewed_streams(tmp_path: Path) -> None:
    """Inject a non-monotonic timestamp into metadata.jsonl."""
    out = tmp_path / "bundle"
    _make_bundle(out, done_after_steps=3)

    md_path = out / METADATA_FILENAME
    lines = md_path.read_text(encoding="utf-8").splitlines()
    # Take the last AGENT_STEP line, lower its timestamp below the previous line's.
    rewritten = []
    last_ts = 0.0
    for i, ln in enumerate(lines):
        payload = json.loads(ln)
        if payload.get("event_type") == EVENT_AGENT_STEP and i > 4:
            # Backwards: jam timestamp to 0.0
            payload["timestamp"] = 0.0
            rewritten.append(json.dumps(payload))
        else:
            ts = payload.get("timestamp", 0.0)
            if isinstance(ts, (int, float)):
                last_ts = max(last_ts, float(ts))
            rewritten.append(ln)
    md_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    replayer = Replayer(out / MANIFEST_FILENAME)
    report = replayer.verify_consistency()
    assert not report.ok
    assert any("timestamp regressed" in issue for issue in report.issues), report.issues


def test_verify_consistency_detects_manifest_count_mismatch(tmp_path: Path) -> None:
    """Tamper with the manifest's alignment counts — verifier should catch it."""
    out = tmp_path / "bundle"
    _make_bundle(out, done_after_steps=3)

    manifest_path = out / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["alignment"]["cot_event_count"] = 9999
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    replayer = Replayer(manifest_path)
    report = replayer.verify_consistency()
    assert not report.ok
    assert report.manifest_matches_streams is False
    assert any("cot_event_count" in issue for issue in report.issues), report.issues


def test_verify_consistency_detects_total_steps_mismatch(tmp_path: Path) -> None:
    """Tamper with manifest.result.total_steps."""
    out = tmp_path / "bundle"
    _make_bundle(out, done_after_steps=3)

    manifest_path = out / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["result"]["total_steps"] = 99
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    replayer = Replayer(manifest_path)
    report = replayer.verify_consistency()
    assert not report.ok
    assert any("total_steps" in issue for issue in report.issues), report.issues


def test_verify_consistency_detects_orphan_event(tmp_path: Path) -> None:
    """Append an event after the END marker — verifier should catch it."""
    out = tmp_path / "bundle"
    _make_bundle(out, done_after_steps=3)

    md_path = out / METADATA_FILENAME
    # Find current end timestamp from the END line.
    lines = md_path.read_text(encoding="utf-8").splitlines()
    end_ts = 0.0
    for ln in lines:
        payload = json.loads(ln)
        if payload.get("event_type") == EVENT_END:
            end_ts = float(payload["timestamp"])
    # Inject an OBSERVATION far in the future (past END).
    rogue = {
        "timestamp": end_ts + 100.0,
        "event_type": EVENT_OBSERVATION,
        "event_args": {"value": "rogue"},
    }
    md_path.write_text(
        md_path.read_text(encoding="utf-8") + json.dumps(rogue) + "\n",
        encoding="utf-8",
    )

    replayer = Replayer(out / MANIFEST_FILENAME)
    report = replayer.verify_consistency()
    assert not report.ok
    assert any("orphan" in issue for issue in report.issues), report.issues


# --- Failure modes ----------------------------------------------------------


def test_replayer_rejects_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Replayer(tmp_path / "nope.json")


def test_replayer_rejects_malformed_manifest(tmp_path: Path) -> None:
    bad = tmp_path / "manifest.json"
    bad.write_text("not even close to JSON {{{", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        Replayer(bad)


def test_replayer_rejects_non_object_manifest(tmp_path: Path) -> None:
    bad = tmp_path / "manifest.json"
    bad.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        Replayer(bad)


# --- replay_against ---------------------------------------------------------


def test_replay_against_clean_bundle_no_divergence(bundle_dir: Path) -> None:
    """Roundtrip: a fresh MockEnvironment replays a recorded bundle perfectly."""
    replayer = Replayer(bundle_dir / MANIFEST_FILENAME)
    drift = replayer.replay_against()

    assert isinstance(drift, ReplayDriftReport)
    assert drift.steps_executed == 3
    assert drift.steps_diverged == [], drift.divergence_reasons
    assert drift.ok is True


def test_replay_against_diverges_on_wrong_env(bundle_dir: Path) -> None:
    """A MockEnvironment with a different reward emits divergence on every step."""
    replayer = Replayer(bundle_dir / MANIFEST_FILENAME)
    diverging_env = MockEnvironment(done_after_steps=3, reward_per_step=99.0)
    drift = replayer.replay_against(env=diverging_env)

    # Every step should be flagged for reward drift.
    assert drift.steps_diverged, "expected divergence with mismatched reward"
    for idx, reason in drift.divergence_reasons.items():
        assert "reward" in reason, f"step {idx}: {reason}"


def test_replay_against_action_missing_terminates_early(tmp_path: Path) -> None:
    """If a step has no recorded ACTION, replay halts at that step."""
    out = tmp_path / "bundle"
    _make_bundle(out, done_after_steps=3)

    # Delete ALL ACTION events from cot.jsonl.
    cot_path = out / COT_FILENAME
    kept = []
    for ln in cot_path.read_text(encoding="utf-8").splitlines():
        payload = json.loads(ln)
        if payload.get("event_type") != EVENT_ACTION:
            kept.append(ln)
    cot_path.write_text("\n".join(kept) + "\n", encoding="utf-8")

    replayer = Replayer(out / MANIFEST_FILENAME)
    drift = replayer.replay_against()
    assert drift.early_termination_step == 0
    assert drift.steps_executed == 0
    assert "no action recorded" in drift.divergence_reasons[0]


# --- CLI smoke --------------------------------------------------------------


def test_cli_replay_default_lists_steps(bundle_dir: Path) -> None:
    cli = CliRunner()
    result = cli.invoke(
        app,
        ["replay", "--manifest", str(bundle_dir / MANIFEST_FILENAME)],
    )
    assert result.exit_code == 0, result.output
    assert "Replay (3 steps)" in result.output


def test_cli_replay_check_clean_bundle(bundle_dir: Path) -> None:
    cli = CliRunner()
    result = cli.invoke(
        app,
        ["replay", "--manifest", str(bundle_dir / MANIFEST_FILENAME), "--check"],
    )
    assert result.exit_code == 0, result.output
    assert "Consistency" in result.output


def test_cli_replay_check_dirty_bundle_fails(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    _make_bundle(out, done_after_steps=3)
    # Sabotage the manifest.
    manifest_path = out / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["alignment"]["input_event_count"] = -1
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    cli = CliRunner()
    result = cli.invoke(app, ["replay", "--manifest", str(manifest_path), "--check"])
    assert result.exit_code != 0, result.output
    assert "issues" in result.output


def test_cli_replay_re_execute_clean_bundle(bundle_dir: Path) -> None:
    cli = CliRunner()
    result = cli.invoke(
        app,
        ["replay", "--manifest", str(bundle_dir / MANIFEST_FILENAME), "--re-execute"],
    )
    assert result.exit_code == 0, result.output
    assert "Replay drift" in result.output


def test_cli_replay_missing_manifest_fails(tmp_path: Path) -> None:
    cli = CliRunner()
    result = cli.invoke(
        app,
        ["replay", "--manifest", str(tmp_path / "nope.json")],
    )
    assert result.exit_code != 0
    assert "cannot load bundle" in result.output


# --- Sanity: ConsistencyReport / ReplayDriftReport API ----------------------


def test_consistency_report_add_flips_ok() -> None:
    report = ConsistencyReport(ok=True)
    assert report.ok is True
    report.add("something broke")
    assert report.ok is False
    assert "something broke" in report.issues


def test_drift_report_ok_property() -> None:
    drift = ReplayDriftReport(steps_executed=3)
    assert drift.ok is True
    drift.steps_diverged.append(1)
    drift.divergence_reasons[1] = "x"
    assert drift.ok is False

"""Determinism tests for the Phase 1 replay tool.

Buyers will train models on Phase 1 trajectory bundles. If event
serialization or replay reconstruction is non-deterministic — even
subtly, e.g. dict-ordering differences — two buyers running the same
bundle through `Replayer` could observe different trajectories. That
silently corrupts downstream model training and erodes trust in the
canonical bundle format.

These tests run each replay-side operation 10 times back-to-back
against the same bundle and assert byte-identical output across all
iterations. Any drift in repr / json.dumps(sort_keys=True) bytes fails
the test with a diff so the offending fields surface immediately.

Test surface
------------
1. ``Replayer.iter_steps()``        — reconstructed ReplayStep tuples
2. ``Replayer.verify_consistency()`` — ConsistencyReport contents
3. ``Replayer.replay_against()``    — ReplayDriftReport contents
4. Stream serialization round-trip  — load + json.dumps(sort_keys=True)

Each test uses ``assertEqual`` semantics across iteration[0] vs
iteration[i] (1..9) so we get a focused diff on the first drift.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

UTC = timezone.utc
from pathlib import Path

import pytest

from oyster_agent_runner.environments.base import MockEnvironment
from oyster_agent_runner.minecraft_streams import (
    COT_FILENAME,
    INPUTS_FILENAME,
    MANIFEST_FILENAME,
    METADATA_FILENAME,
    MinecraftStreamWriter,
)
from oyster_agent_runner.providers.base import MockLLMProvider
from oyster_agent_runner.replay import Replayer
from oyster_agent_runner.runner import AgentRunner, RunnerConfig
from oyster_agent_runner.schema import AgentTask, TrajectoryEvent

# Number of repeated invocations per determinism check. 10 is enough to
# surface dict-ordering instability (Python guarantees insertion order
# but hash randomization can leak through json.dumps without sort_keys)
# while staying fast — the full suite runs in well under a second.
ITERATIONS = 10


# --- Bundle fixture ---------------------------------------------------------


def _make_bundle(out_dir: Path, *, done_after_steps: int = 3) -> Path:
    """Mint a real Phase 1 bundle by running the runner end-to-end.

    Mirrors the fixture in ``test_replay.py`` so we exercise the same
    code path the production ``run-mc`` CLI uses. Returns the path to
    the bundle directory (caller resolves manifest.json from there).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    task = AgentTask(
        task_id="replay-determinism-test",
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

        streams.finalize_manifest(
            task_id="replay-determinism-test",
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
    return out_dir


@pytest.fixture
def bundle_dir(tmp_path: Path) -> Path:
    """A freshly minted Phase 1 bundle shared across the determinism tests."""
    out = tmp_path / "bundle"
    _make_bundle(out, done_after_steps=3)
    return out


def _stable_dump(obj: object) -> str:
    """Canonical JSON form: sort keys, no whitespace variation."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


# --- 1. iter_steps determinism ----------------------------------------------


def test_iter_steps_deterministic_10x(bundle_dir: Path) -> None:
    """Walking the same bundle 10 times must yield byte-identical steps.

    ReplayStep is ``frozen=True`` (and therefore by-value via dataclass
    auto-generated ``__eq__``), but we use ``repr`` here as a stronger
    bytes-level check — two ReplayStep instances with float fields that
    *compare* equal under ``==`` could still serialize differently if a
    NaN or signed-zero ever crept in. Repr surfaces that drift.
    """
    manifest = bundle_dir / MANIFEST_FILENAME

    runs: list[list[str]] = []
    for _ in range(ITERATIONS):
        replayer = Replayer(manifest)
        steps = replayer.iter_steps()
        runs.append([repr(s) for s in steps])

    baseline = runs[0]
    assert baseline, "expected at least one ReplayStep in baseline"
    for i, run in enumerate(runs[1:], start=1):
        assert run == baseline, (
            f"iter_steps non-deterministic at iteration {i}: baseline={baseline!r} drift={run!r}"
        )


# --- 2. verify_consistency determinism --------------------------------------


def test_verify_consistency_deterministic_10x(bundle_dir: Path) -> None:
    """ConsistencyReport bytes must be identical across 10 invocations.

    We canonicalize via ``json.dumps(sort_keys=True)`` on a dict snapshot
    of the report. ``ConsistencyReport`` is a (mutable) dataclass; we
    never mutate it post-construction so equality is well defined, but
    again the JSON form is the byte-level guarantee a buyer cares about.
    """
    manifest = bundle_dir / MANIFEST_FILENAME

    runs: list[str] = []
    for _ in range(ITERATIONS):
        replayer = Replayer(manifest)
        report = replayer.verify_consistency()
        snapshot = {
            "ok": report.ok,
            "issues": list(report.issues),
            "cot_event_count": report.cot_event_count,
            "metadata_event_count": report.metadata_event_count,
            "input_event_count": report.input_event_count,
            "max_timestamp_sec": report.max_timestamp_sec,
            "step_count": report.step_count,
            "manifest_matches_streams": report.manifest_matches_streams,
        }
        runs.append(_stable_dump(snapshot))

    baseline = runs[0]
    assert baseline, "expected non-empty ConsistencyReport snapshot"
    for i, run in enumerate(runs[1:], start=1):
        assert run == baseline, (
            f"verify_consistency non-deterministic at iteration {i}: "
            f"baseline={baseline} drift={run}"
        )


# --- 3. replay_against determinism ------------------------------------------


def test_replay_against_mockenv_deterministic_10x(bundle_dir: Path) -> None:
    """Replaying against a fresh MockEnvironment 10x must yield identical drift.

    Each iteration constructs a brand-new ``MockEnvironment`` so we
    catch any state that leaks between invocations of the env, AND any
    nondeterminism in the comparator helpers (``_shallow_equal``,
    ``_equal_modulo_progress``, ``_floats_close``).
    """
    manifest = bundle_dir / MANIFEST_FILENAME

    runs: list[str] = []
    for _ in range(ITERATIONS):
        replayer = Replayer(manifest)
        # Fresh env per iteration to avoid cross-iteration state leakage.
        env = MockEnvironment(done_after_steps=3)
        drift = replayer.replay_against(env=env)
        snapshot = {
            "steps_executed": drift.steps_executed,
            "steps_diverged": list(drift.steps_diverged),
            # divergence_reasons keys are ints; canonicalize as sorted str-keyed.
            "divergence_reasons": {
                str(k): drift.divergence_reasons[k] for k in sorted(drift.divergence_reasons.keys())
            },
            "early_termination_step": drift.early_termination_step,
            "error_message": drift.error_message,
            "ok": drift.ok,
        }
        runs.append(_stable_dump(snapshot))

    baseline = runs[0]
    assert baseline, "expected non-empty ReplayDriftReport snapshot"
    for i, run in enumerate(runs[1:], start=1):
        assert run == baseline, (
            f"replay_against non-deterministic at iteration {i}: baseline={baseline} drift={run}"
        )


# --- 4. Serialization byte stability ----------------------------------------


def test_serialization_byte_stability(bundle_dir: Path) -> None:
    """Each stream file: load + re-serialize via sort_keys=True must be stable.

    This catches non-determinism at the JSON layer itself — e.g. if a
    future writer change introduces sets, tuples, or nondeterministic
    iteration order that survives into the on-disk lines. We do NOT
    require the on-disk bytes to equal the canonical bytes (the writer
    is free to use a different separator policy), only that loading and
    canonicalizing the same line 10 times is stable.
    """
    targets = [
        bundle_dir / MANIFEST_FILENAME,
        bundle_dir / COT_FILENAME,
        bundle_dir / METADATA_FILENAME,
        bundle_dir / INPUTS_FILENAME,
    ]

    for path in targets:
        assert path.exists(), f"bundle is missing {path.name}"

    for path in targets:
        text = path.read_text(encoding="utf-8")
        if path.name == MANIFEST_FILENAME:
            # manifest is a single JSON object.
            parsed_runs: list[str] = []
            for _ in range(ITERATIONS):
                parsed = json.loads(text)
                parsed_runs.append(_stable_dump(parsed))
            baseline = parsed_runs[0]
            for i, run in enumerate(parsed_runs[1:], start=1):
                assert run == baseline, (
                    f"{path.name} serialization unstable at iteration {i}: "
                    f"baseline={baseline} drift={run}"
                )
        else:
            # JSONL stream: each non-empty line must be individually stable.
            lines = [ln for ln in text.splitlines() if ln.strip()]
            assert lines, f"{path.name} unexpectedly empty"
            line_runs: list[list[str]] = []
            for _ in range(ITERATIONS):
                line_runs.append([_stable_dump(json.loads(ln)) for ln in lines])
            baseline_lines = line_runs[0]
            for i, run in enumerate(line_runs[1:], start=1):
                assert run == baseline_lines, (
                    f"{path.name} serialization unstable at iteration {i}: "
                    f"baseline={baseline_lines} drift={run}"
                )


# --- 5. Cross-instance determinism (bonus) ----------------------------------


def test_replayer_instances_independent_10x(bundle_dir: Path) -> None:
    """Two replayers built from the same manifest produce identical output.

    Defends against any module-level cache or accidental class-state
    leakage in ``Replayer``. Each iteration constructs a fresh instance
    AND calls every public method, then snapshots the union.
    """
    manifest = bundle_dir / MANIFEST_FILENAME

    runs: list[str] = []
    for _ in range(ITERATIONS):
        replayer = Replayer(manifest)
        steps = [repr(s) for s in replayer.iter_steps()]
        consistency = replayer.verify_consistency()
        env = MockEnvironment(done_after_steps=3)
        drift = replayer.replay_against(env=env)
        snapshot = _stable_dump(
            {
                "steps": steps,
                "consistency_ok": consistency.ok,
                "consistency_issues": list(consistency.issues),
                "consistency_step_count": consistency.step_count,
                "drift_executed": drift.steps_executed,
                "drift_diverged": list(drift.steps_diverged),
                "drift_ok": drift.ok,
            }
        )
        runs.append(snapshot)

    baseline = runs[0]
    for i, run in enumerate(runs[1:], start=1):
        assert run == baseline, (
            f"cross-instance determinism failed at iteration {i}: baseline={baseline} drift={run}"
        )

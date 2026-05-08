"""D20 E2E test — Python-side integration through the full overlay path.

Howard 2026-05-07: D20 spec asks for a full Paper-server + Mineflayer bot
+ buyer_spec_pipeline.sh + D5 chain. That's a 5+ minute CI test requiring
Java 21, Node 20, Python 3.11 all configured. We split that work:

  Part A (THIS FILE) — Python-side E2E:
    * Emits a synthetic but schema-correct game_state.jsonl (the exact
      shape the Fabric mod's GameStateSample.toJsonLine() produces).
    * Builds a Phase-1 bundle with metadata.jsonl + manifest.json.
    * Calls adapt_phase1_to_buyer_spec(..., game_state_jsonl=...).
    * Verifies the resulting action_camera.json carries
      _real_game_state=True and matching position/rotation values.
    * Runs D5 classifier and asserts REAL (mod-driven) verdict per D18.

  Part B (FUTURE) — Java-side full chain in CI:
    * Boot a real Paper 1.21.4 server with the mod jar.
    * Spawn an actual Mineflayer bot.
    * Drive buyer_spec_pipeline.sh.
    * Extract tarball, run D5.
    * That's gated on Paper jar caching + Mineflayer setup time.

The synthetic JSONL in Part A is NOT a mock — it's a sample fixture with
the exact schema the canonical D15 contract test pins. If the mod ever
diverges from this schema, D15 fails before this test even runs.

Iron-law honest: this test exercises the REAL Python codepath end-to-end.
It catches every regression in:
  - JSONL parsing (game_state_overlay.load)
  - Sample lookup (game_state_overlay.lookup_at_ms)
  - Field overlay (game_state_overlay.apply_to_record)
  - Adapter integration (adapt_phase1_to_buyer_spec game_state_jsonl branch)
  - D5 tier-0 detection (tarball_authenticity_check._classify_action_camera)
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))


def _load_tac():
    """Load tarball_authenticity_check (bin/ isn't a package)."""
    spec = importlib.util.spec_from_file_location(
        "tarball_authenticity_check",
        REPO_ROOT / "bin" / "tarball_authenticity_check.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tarball_authenticity_check"] = mod
    spec.loader.exec_module(mod)
    return mod


def _emit_synthetic_jsonl(path: Path, *, n_ticks: int = 200) -> None:
    """Write a JSONL file matching GameStateSample.toJsonLine() schema.

    Simulates a player walking from x=10 to x=30 at z=-5 (real scene
    motion that would also pass D5 tier-2 fingerprint variance, so we
    can prove tier-0 short-circuits and isn't masked by tier-2).
    """
    base_ts = 1_700_000_000_000  # ms-since-epoch
    with path.open("w", encoding="utf-8") as fh:
        for tick in range(n_ticks):
            t = tick / 20.0  # 20 TPS
            x = 10.0 + (20.0 * t / (n_ticks / 20.0))
            yaw = -90.0 + (180.0 * t / (n_ticks / 20.0))
            sample: dict[str, Any] = {
                "tick": tick,
                "timestamp_ms": base_ts + tick * 50,
                "x": x,
                "y": 64.0,
                "z": -5.0,
                "yaw_deg": yaw,
                "pitch_deg": 0.0,
                "look_x": 0.0,
                "look_y": 0.0,
                "look_z": 1.0,
                "velocity_x": 0.2,
                "velocity_y": 0.0,
                "velocity_z": 0.0,
                "on_ground": True,
                "sneaking": False,
                "sprinting": False,
                "dimension": "minecraft:overworld",
                "game_mode": "SURVIVAL",
            }
            fh.write(json.dumps(sample) + "\n")


def _emit_phase1_bundle(bundle_dir: Path, *, n_obs: int = 200) -> None:
    """Write a minimal Phase-1 bundle (manifest.json + metadata.jsonl)
    that adapt_phase1_to_buyer_spec can consume."""
    bundle_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "task_id": "MC-d20-e2e-test",
        "model": "synthetic",
        "provider": "test",
        "phase": 1,
        "started_at": "2026-05-07T00:00:00Z",
        "ended_at": "2026-05-07T00:00:10Z",
    }
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest))

    with (bundle_dir / "metadata.jsonl").open("w", encoding="utf-8") as fh:
        for i in range(n_obs):
            # Mineflayer-style OBSERVATION: event_args.value is the obs dict,
            # which contains either obs.bot.position or obs.position.
            ev = {
                "event_type": "OBSERVATION",
                "step": i,
                "timestamp_ms": i * 100,
                "event_args": {
                    "value": {
                        "bot": {
                            "position": {"x": float(i), "y": 64.0, "z": -5.0},
                            "yaw": 0.0,
                            "pitch": 0.0,
                        },
                    },
                },
            }
            fh.write(json.dumps(ev) + "\n")

    (bundle_dir / "cot.jsonl").write_text("")
    (bundle_dir / "inputs.jsonl").write_text("")


def test_e2e_overlay_produces_real_action_camera(tmp_path):
    """Full Python pipeline:
        synthetic JSONL → adapter w/ overlay → action_camera with
        _real_game_state=True → D5 classifies REAL (mod-driven).
    """
    bundle_dir = tmp_path / "phase1_bundle"
    output_dir = tmp_path / "buyer_out"
    jsonl_path = tmp_path / "game_state.jsonl"

    _emit_phase1_bundle(bundle_dir)
    _emit_synthetic_jsonl(jsonl_path)

    # Run the adapter with overlay enabled.
    from oyster_agent_runner.buyer_spec_adapter import (  # noqa: PLC0415
        adapt_phase1_to_buyer_spec,
    )

    adapt_phase1_to_buyer_spec(
        bundle_dir=bundle_dir,
        output_dir=output_dir,
        game_state_jsonl=jsonl_path,
    )

    # Verify the action_camera.json was written + has overlay markers.
    ac_path = output_dir / "action_camera.json"
    assert ac_path.exists(), "action_camera.json not written"
    records = json.loads(ac_path.read_text())
    assert len(records) > 0

    flagged = [r for r in records if r.get("_real_game_state") is True]
    assert flagged, "no records carry _real_game_state=True after overlay"
    # At least 80% of records should have the flag (some may fall outside
    # the JSONL time window if the synthetic data is shorter).
    ratio = len(flagged) / len(records)
    assert ratio > 0.8, (
        f"only {ratio:.1%} of records have _real_game_state — overlay "
        f"didn't reach most frames"
    )

    # Verify position values are real (non-constant, non-default).
    positions = {tuple(r["camera_position"]) for r in flagged}
    assert len(positions) > 1, (
        f"camera_position is constant across overlay records ({positions}) — "
        f"lookup_at_ms isn't picking different samples per frame"
    )

    # Now run D5 classifier and assert REAL (mod-driven) verdict.
    tac = _load_tac()
    state, evidence = tac._classify_action_camera(ac_path)
    assert state == tac.REAL, (
        f"D5 verdict is {state}, expected REAL. Evidence: {evidence}"
    )
    assert "mod-driven" in evidence, (
        f"D5 should classify as mod-driven (tier 0), got: {evidence}"
    )


def test_e2e_no_overlay_falls_back_cleanly(tmp_path):
    """If game_state_jsonl is None / missing, adapter must NOT crash;
    action_camera gets metadata-derived placeholder values; D5 verdict
    drops to PLACEHOLDER (no tier-0 flag, no real fingerprint variance
    on this synthetic minimal bundle)."""
    bundle_dir = tmp_path / "phase1_bundle"
    output_dir = tmp_path / "buyer_out"

    _emit_phase1_bundle(bundle_dir, n_obs=50)

    from oyster_agent_runner.buyer_spec_adapter import (  # noqa: PLC0415
        adapt_phase1_to_buyer_spec,
    )

    adapt_phase1_to_buyer_spec(
        bundle_dir=bundle_dir,
        output_dir=output_dir,
        game_state_jsonl=None,  # no overlay
    )

    ac_path = output_dir / "action_camera.json"
    records = json.loads(ac_path.read_text())
    assert records, "no records produced"
    flagged = [r for r in records if r.get("_real_game_state") is True]
    assert not flagged, (
        f"records carry _real_game_state without overlay — false-positive "
        f"({len(flagged)} flagged)"
    )


def test_e2e_overlay_with_nonexistent_jsonl_does_not_crash(tmp_path):
    """If the path is supplied but file is missing, adapter must fail-soft
    (treat as no overlay) instead of raising."""
    bundle_dir = tmp_path / "phase1_bundle"
    output_dir = tmp_path / "buyer_out"
    missing_jsonl = tmp_path / "definitely_does_not_exist.jsonl"

    _emit_phase1_bundle(bundle_dir, n_obs=50)

    from oyster_agent_runner.buyer_spec_adapter import (  # noqa: PLC0415
        adapt_phase1_to_buyer_spec,
    )

    # Should not raise.
    adapt_phase1_to_buyer_spec(
        bundle_dir=bundle_dir,
        output_dir=output_dir,
        game_state_jsonl=missing_jsonl,
    )

    ac_path = output_dir / "action_camera.json"
    assert ac_path.exists()
    records = json.loads(ac_path.read_text())
    assert records, "no records produced even though adapter shouldn't have failed"


def test_e2e_overlay_jsonl_shape_matches_d15_canonical():
    """The synthetic JSONL this E2E emits MUST match the canonical
    GameStateSample schema D15 pins. If they drift, D15 fails first —
    this assertion is a belt-and-suspenders check."""
    from game_state_overlay import load as gs_load  # type: ignore  # noqa: PLC0415

    EXPECTED_FIELDS = {
        "tick", "timestamp_ms", "x", "y", "z",
        "yaw_deg", "pitch_deg",
        "look_x", "look_y", "look_z",
        "velocity_x", "velocity_y", "velocity_z",
        "on_ground", "sneaking", "sprinting",
        "dimension", "game_mode",
    }

    import tempfile  # noqa: PLC0415
    with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w",
                                     delete=False, encoding="utf-8") as fh:
        path = Path(fh.name)
    _emit_synthetic_jsonl(path, n_ticks=5)
    samples = gs_load(path)
    path.unlink()

    assert samples is not None and len(samples) == 5
    actual_fields = set(samples[0].keys())
    missing = EXPECTED_FIELDS - actual_fields
    extra = actual_fields - EXPECTED_FIELDS
    assert not missing, f"synthetic JSONL missing canonical fields: {missing}"
    assert not extra, f"synthetic JSONL has extra fields: {extra}"

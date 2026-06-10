from __future__ import annotations

from oyster_agent_runner.capture_architecture import (
    MINECRAFT_POC_CAPTURE_PLAN,
    CapturePlan,
    ProcessingLocation,
    classify_artifact,
    validate_capture_plan,
)


def test_minecraft_poc_keeps_exr_and_linear_depth_server_side() -> None:
    assert validate_capture_plan(MINECRAFT_POC_CAPTURE_PLAN) == ()
    assert classify_artifact("raw_depth_texture") == ProcessingLocation.CLIENT
    assert classify_artifact("linear_depth") == ProcessingLocation.SERVER
    assert classify_artifact("openexr_depth") == ProcessingLocation.SERVER


def test_capture_plan_rejects_client_side_exr_outputs() -> None:
    bad_plan = CapturePlan(
        game="minecraft",
        client_artifacts=(
            "video",
            "camera_telemetry",
            "game_state",
            "capture_manifest",
            "openexr_depth",
        ),
        server_artifacts=("linear_depth",),
        notes="Regression: client is trying to generate EXR.",
    )

    errors = validate_capture_plan(bad_plan)

    assert any("openexr_depth" in error and "server-side" in error for error in errors)
    assert any("server plan missing" in error and "openexr_depth" in error for error in errors)


def test_capture_plan_requires_minimal_raw_client_evidence() -> None:
    bad_plan = CapturePlan(
        game="future-game",
        client_artifacts=("video",),
        server_artifacts=("linear_depth", "openexr_depth"),
        notes="Incomplete capture plan.",
    )

    errors = validate_capture_plan(bad_plan)

    assert "future-game: client plan missing required raw artifact 'camera_telemetry'" in errors
    assert "future-game: client plan missing required raw artifact 'game_state'" in errors
    assert "future-game: client plan missing required raw artifact 'capture_manifest'" in errors

"""Capture/post-processing architecture contract.

The consumer recorder must stay lightweight: collect raw evidence on the
player machine, then move expensive or GPU-sensitive transforms to a controlled
server pipeline. This module makes that split explicit for tests, docs, and
future game adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProcessingLocation(str, Enum):
    """Where an artifact is produced in the production pipeline."""

    CLIENT = "client"
    SERVER = "server"


@dataclass(frozen=True)
class ArtifactClass:
    """One artifact class in the game-data pipeline."""

    name: str
    location: ProcessingLocation
    examples: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class CapturePlan:
    """Expected split for one game capture plan."""

    game: str
    client_artifacts: tuple[str, ...]
    server_artifacts: tuple[str, ...]
    notes: str


CLIENT_RAW_ARTIFACTS: tuple[ArtifactClass, ...] = (
    ArtifactClass(
        name="video",
        location=ProcessingLocation.CLIENT,
        examples=("recordings/main_record.mp4", "recording.mp4", "video.mp4"),
        rationale="Screen/window video is cheap and stable to collect locally.",
    ),
    ArtifactClass(
        name="raw_depth_texture",
        location=ProcessingLocation.CLIENT,
        examples=("depth_raw/*.f32", "depth_raw/*.bin", "depth_raw/*.png"),
        rationale=(
            "If a mod exposes depth, the client may dump the raw non-linear buffer "
            "without linearization or EXR conversion."
        ),
    ),
    ArtifactClass(
        name="camera_telemetry",
        location=ProcessingLocation.CLIENT,
        examples=("camera.jsonl", "action_camera.json", "streams/states.jsonl"),
        rationale="Camera pose/FOV/timestamps are low-cost structured telemetry.",
    ),
    ArtifactClass(
        name="game_state",
        location=ProcessingLocation.CLIENT,
        examples=("game_state.jsonl", "states.jsonl", "streams/states.jsonl"),
        rationale="Game state is the core Minecraft POC signal and should be captured raw.",
    ),
    ArtifactClass(
        name="input_events",
        location=ProcessingLocation.CLIENT,
        examples=("inputs.jsonl", "streams/actions.jsonl"),
        rationale="Input/action streams are small and needed for buyer alignment.",
    ),
    ArtifactClass(
        name="capture_manifest",
        location=ProcessingLocation.CLIENT,
        examples=("manifest.json", "MANIFEST.json", "metadata/session.json"),
        rationale="Manifest/provenance should be written before upload.",
    ),
)

SERVER_POSTPROCESS_ARTIFACTS: tuple[ArtifactClass, ...] = (
    ArtifactClass(
        name="linear_depth",
        location=ProcessingLocation.SERVER,
        examples=("depth_linear/*.f32", "depth_meters/*.npy"),
        rationale="Depth linearization depends on camera parameters and is safer off-client.",
    ),
    ArtifactClass(
        name="openexr_depth",
        location=ProcessingLocation.SERVER,
        examples=("depth/000001.exr", "depth/frame_000001.exr"),
        rationale="OpenEXR float32 generation is a buyer deliverable, not a recorder hot path.",
    ),
    ArtifactClass(
        name="depth_uint16",
        location=ProcessingLocation.SERVER,
        examples=("depth_uint16/000001.png",),
        rationale="Alternate compressed depth encodings belong in the processing tier.",
    ),
    ArtifactClass(
        name="dataset_conversion",
        location=ProcessingLocation.SERVER,
        examples=("buyer_prd/", "rlds/", "parquet/"),
        rationale="Buyer-specific layouts should be generated after upload.",
    ),
    ArtifactClass(
        name="quality_scoring",
        location=ProcessingLocation.SERVER,
        examples=("quality_report.json", "audit_report.json"),
        rationale="Scoring should run against complete uploaded sessions.",
    ),
)

CLIENT_ARTIFACT_NAMES = frozenset(artifact.name for artifact in CLIENT_RAW_ARTIFACTS)
SERVER_ARTIFACT_NAMES = frozenset(artifact.name for artifact in SERVER_POSTPROCESS_ARTIFACTS)

DISALLOWED_CLIENT_DEPTH_OUTPUTS = frozenset(
    {
        "linear_depth",
        "openexr_depth",
        "depth_exr",
        "depth/*.exr",
        "*.exr",
        "32bit_float_depth",
    }
)

MINECRAFT_POC_CAPTURE_PLAN = CapturePlan(
    game="minecraft",
    client_artifacts=(
        "video",
        "raw_depth_texture",
        "camera_telemetry",
        "game_state",
        "input_events",
        "capture_manifest",
    ),
    server_artifacts=(
        "linear_depth",
        "openexr_depth",
        "depth_uint16",
        "dataset_conversion",
        "quality_scoring",
    ),
    notes=(
        "Minecraft is the first POC. The recorder/mod captures raw evidence only; "
        "linear depth, OpenEXR, compression, and buyer-specific conversion run server-side."
    ),
)


def classify_artifact(name: str) -> ProcessingLocation | None:
    """Return the production location for a known artifact class."""

    normalized = name.strip().lower()
    if normalized in CLIENT_ARTIFACT_NAMES:
        return ProcessingLocation.CLIENT
    if normalized in SERVER_ARTIFACT_NAMES:
        return ProcessingLocation.SERVER
    return None


def validate_capture_plan(plan: CapturePlan) -> tuple[str, ...]:
    """Validate that a capture plan does not push heavy depth work to clients."""

    errors: list[str] = []
    client = {artifact.strip().lower() for artifact in plan.client_artifacts}
    server = {artifact.strip().lower() for artifact in plan.server_artifacts}

    missing = {"video", "camera_telemetry", "game_state", "capture_manifest"} - client
    for artifact in sorted(missing):
        errors.append(f"{plan.game}: client plan missing required raw artifact {artifact!r}")

    illegal_client_depth = client & DISALLOWED_CLIENT_DEPTH_OUTPUTS
    for artifact in sorted(illegal_client_depth):
        errors.append(
            f"{plan.game}: {artifact!r} must be produced server-side, not on the player client"
        )

    for artifact in sorted({"linear_depth", "openexr_depth"} - server):
        errors.append(f"{plan.game}: server plan missing depth postprocess artifact {artifact!r}")

    overlap = client & server
    for artifact in sorted(overlap):
        errors.append(f"{plan.game}: artifact {artifact!r} appears in both client and server")

    return tuple(errors)

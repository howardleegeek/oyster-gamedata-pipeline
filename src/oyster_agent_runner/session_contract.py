"""Recorder-to-pipeline session bundle contracts.

The GameData product currently has two active producers: the Windows recorder
and the Python buyer-spec pipeline. This module keeps the file-layout handoff
explicit so adapters, validators, and release smokes do not drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class SessionLayout(str, Enum):
    """Known on-disk session layouts."""

    LEM = "lem"
    LEGACY_PIPELINE = "legacy_pipeline"
    PHASE1_AGENT = "phase1_agent"
    BUYER_PRD = "buyer_prd"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SessionContract:
    """Required and optional paths for one session layout."""

    layout: SessionLayout
    required_paths: tuple[Path, ...]
    optional_paths: tuple[Path, ...]
    description: str


@dataclass(frozen=True)
class SessionContractResult:
    """Validation result for a concrete session directory."""

    root: Path
    layout: SessionLayout
    missing_required: tuple[str, ...]
    present_required: tuple[str, ...]
    optional_present: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return self.layout != SessionLayout.UNKNOWN and not self.missing_required


CONTRACTS: dict[SessionLayout, SessionContract] = {
    SessionLayout.LEM: SessionContract(
        layout=SessionLayout.LEM,
        required_paths=(
            Path("recordings/main_record.mp4"),
            Path("streams/states.jsonl"),
        ),
        optional_paths=(
            Path("streams/actions.jsonl"),
            Path("metadata/session.json"),
            Path("MANIFEST.json"),
            Path("metadata.json"),
        ),
        description="Windows recorder raw session layout.",
    ),
    SessionLayout.LEGACY_PIPELINE: SessionContract(
        layout=SessionLayout.LEGACY_PIPELINE,
        required_paths=(
            Path("recording.mp4"),
            Path("game_state.jsonl"),
        ),
        optional_paths=(
            Path("inputs.jsonl"),
            Path("manifest.json"),
            Path("MANIFEST.json"),
            Path("metadata.json"),
        ),
        description="Pipeline validator working layout consumed by canonical_pipeline.py.",
    ),
    SessionLayout.PHASE1_AGENT: SessionContract(
        layout=SessionLayout.PHASE1_AGENT,
        required_paths=(
            Path("manifest.json"),
            Path("cot.jsonl"),
            Path("metadata.jsonl"),
            Path("inputs.jsonl"),
        ),
        optional_paths=(
            Path("trajectory.jsonl"),
            Path("video.mp4"),
            Path("depth"),
        ),
        description="Agent bundle layout consumed by buyer_spec_adapter.py.",
    ),
    SessionLayout.BUYER_PRD: SessionContract(
        layout=SessionLayout.BUYER_PRD,
        required_paths=(
            Path("video.mp4"),
            Path("systeminfo.json"),
            Path("action_camera.json"),
            Path("gameinfo.xlsx"),
            Path("depth"),
        ),
        optional_paths=(
            Path("MANIFEST.json"),
            Path("manifest.json"),
            Path("frames.jsonl"),
            Path("audio.flac"),
        ),
        description="Buyer-facing PRD deliverable bundle.",
    ),
}

DETECTION_PRIORITY: tuple[SessionLayout, ...] = (
    SessionLayout.LEM,
    SessionLayout.LEGACY_PIPELINE,
    SessionLayout.PHASE1_AGENT,
    SessionLayout.BUYER_PRD,
)


def contract_for(layout: SessionLayout | str) -> SessionContract:
    """Return the contract for a known layout."""

    resolved = SessionLayout(layout)
    if resolved not in CONTRACTS:
        raise ValueError(f"unknown session layout: {layout}")
    return CONTRACTS[resolved]


def required_paths_for(layout: SessionLayout | str) -> tuple[Path, ...]:
    """Return required paths for a known layout."""

    return contract_for(layout).required_paths


def _path_exists(root: Path, relative_path: Path) -> bool:
    return (root / relative_path).exists()


def validate_session_contract(
    root: Path | str,
    layout: SessionLayout | str,
) -> SessionContractResult:
    """Validate a session directory against one explicit layout."""

    root_path = Path(root)
    contract = contract_for(layout)
    present_required = tuple(
        str(path) for path in contract.required_paths if _path_exists(root_path, path)
    )
    missing_required = tuple(
        str(path) for path in contract.required_paths if not _path_exists(root_path, path)
    )
    optional_present = tuple(
        str(path) for path in contract.optional_paths if _path_exists(root_path, path)
    )
    return SessionContractResult(
        root=root_path,
        layout=contract.layout,
        missing_required=missing_required,
        present_required=present_required,
        optional_present=optional_present,
    )


def detect_session_layout(root: Path | str) -> SessionContractResult:
    """Detect the first complete known layout in priority order."""

    root_path = Path(root)
    if not root_path.is_dir():
        return SessionContractResult(
            root=root_path,
            layout=SessionLayout.UNKNOWN,
            missing_required=(),
            present_required=(),
            optional_present=(),
        )

    best_partial: SessionContractResult | None = None
    for layout in DETECTION_PRIORITY:
        result = validate_session_contract(root_path, layout)
        if result.is_valid:
            return result
        if result.present_required and (
            best_partial is None
            or len(result.present_required) > len(best_partial.present_required)
        ):
            best_partial = result

    if best_partial is not None:
        return best_partial

    return SessionContractResult(
        root=root_path,
        layout=SessionLayout.UNKNOWN,
        missing_required=(),
        present_required=(),
        optional_present=(),
    )


def is_complete_layout(root: Path | str, layout: SessionLayout | str) -> bool:
    """Return True when a directory satisfies one explicit layout."""

    return validate_session_contract(root, layout).is_valid

"""Oyster GameData SDK — buyer-facing Python SDK for the buyer-spec v1 tarball.

This package gives the AI-training customer a typed, lazy, dependency-light
interface to ingest, validate, and iterate over delivered tarballs without
re-implementing schema logic.

Public surface (stable):

    from oyster_gamedata_sdk import Tarball, LintReport, ActionCameraFrame

    tar = Tarball.from_path("vendor-001_batch-A_clip-00042_v1.tar.gz")
    print(tar.systeminfo.width, tar.systeminfo.height)
    for frame in tar.action_camera:
        print(frame.frame, frame.camera_position)
    report = tar.validate()
    if report.passed:
        print(report.summary())

The SDK works fully offline: no Supabase / Vercel / API credentials required.
"""

from __future__ import annotations

from .errors import (
    GameDataSDKError,
    TarballNotFoundError,
    TarballStructureError,
    SchemaValidationError,
    DependencyMissingError,
)
from .schema import (
    ActionCameraFrame,
    CameraIntrinsics,
    Systeminfo,
    Gameinfo,
    Vector3,
    Vector4,
    MapBounds,
)
from .tarball import Tarball, MetadataSummary
from .lint_report import LintReport, LintResult

__all__ = [
    # Errors
    "GameDataSDKError",
    "TarballNotFoundError",
    "TarballStructureError",
    "SchemaValidationError",
    "DependencyMissingError",
    # Schema
    "ActionCameraFrame",
    "CameraIntrinsics",
    "Systeminfo",
    "Gameinfo",
    "Vector3",
    "Vector4",
    "MapBounds",
    # Core
    "Tarball",
    "MetadataSummary",
    "LintReport",
    "LintResult",
]

__version__ = "0.1.0"

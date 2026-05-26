"""Release asset channel contracts.

The project has two successful historical release surfaces:

* current consumer releases, which carry the small OysterRecorder setup exe;
* rc19 bundled recorder releases, which carry a large offline bundle plus
  Minecraft mod jars.

Keep those surfaces explicit. The appcast must point only at the consumer
installer channel; the bundled channel is a recovery/reference path until it
passes the same consumer installer gates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Pattern


class ReleaseChannel(str, Enum):
    """Mutually exclusive distribution channels."""

    CONSUMER_INSTALLER = "consumer_installer"
    BUNDLED_RECORDER = "bundled_recorder"
    SOURCE_CANDIDATE = "source_candidate"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ReleaseChannelContract:
    """Asset contract for one release channel."""

    channel: ReleaseChannel
    description: str
    asset_patterns: tuple[str, ...]
    required_companion_assets: tuple[str, ...]
    known_good_anchor: str
    appcast_allowed: bool
    promotion_gates: tuple[str, ...]

    @property
    def compiled_patterns(self) -> tuple[Pattern[str], ...]:
        return tuple(re.compile(pattern) for pattern in self.asset_patterns)

    def matches_asset(self, name: str) -> bool:
        return any(pattern.match(name) for pattern in self.compiled_patterns)


@dataclass(frozen=True)
class FallbackStep:
    """One ordered recovery path when a release surface fails validation."""

    name: str
    channel: ReleaseChannel
    trigger: str
    action: str
    gate: str


CURRENT_CONSUMER_TAG = "v0.11.14"
CURRENT_CONSUMER_INSTALLER = "OysterRecorder-setup-v2.6.0.exe"
CURRENT_CONSUMER_SHA256 = "c31c3c6d59ab661f839e4b07ec73d3196d3729faef638650db92a50f6f002eb9"
BUNDLED_REFERENCE_TAG = "recorder-v0.28.0-rc19.0.3"
RECORDER_SOURCE_PIN = "e171f20cf27aeaea1ac2f1b63434d7e2a1e09f61"


CHANNELS: dict[ReleaseChannel, ReleaseChannelContract] = {
    ReleaseChannel.CONSUMER_INSTALLER: ReleaseChannelContract(
        channel=ReleaseChannel.CONSUMER_INSTALLER,
        description="Public auto-update and friend-download Windows installer channel.",
        asset_patterns=(r"^OysterRecorder-[Ss]etup-.*\.exe$",),
        required_companion_assets=("SHA256SUMS.txt",),
        known_good_anchor=CURRENT_CONSUMER_TAG,
        appcast_allowed=True,
        promotion_gates=(
            "release asset HEAD 200",
            "SHA256SUMS.txt matches installer digest",
            "backend appcast points to the same tag and sha256",
            "Release Distribution Smoke success",
            "Backend Remote Smoke success",
            "Windows Installer Smoke success",
        ),
    ),
    ReleaseChannel.BUNDLED_RECORDER: ReleaseChannelContract(
        channel=ReleaseChannel.BUNDLED_RECORDER,
        description="Offline/reference recorder bundle with Minecraft runtime assets and mod jars.",
        asset_patterns=(
            r"^GameDataRecorder-Setup-recorder-v.*\.exe$",
            r"^OysterRecorder-onedir\.zip$",
            r"^OysterRecorder\.exe$",
            r"^oyster-recorder-mod-.*\.jar$",
            r"^SHA-256-manifest\.txt$",
        ),
        required_companion_assets=("SHA-256-manifest.txt",),
        known_good_anchor=BUNDLED_REFERENCE_TAG,
        appcast_allowed=False,
        promotion_gates=(
            "large bundle size sanity gate passes",
            "SHA-256-manifest.txt covers all user-facing assets",
            "Minecraft mod jar matrix is present for supported versions",
            "clean Windows install and gameplay smoke passes",
            "consumer installer naming/version contract is updated before appcast promotion",
        ),
    ),
    ReleaseChannel.SOURCE_CANDIDATE: ReleaseChannelContract(
        channel=ReleaseChannel.SOURCE_CANDIDATE,
        description="Pinned source used to build the next recorder candidate.",
        asset_patterns=(),
        required_companion_assets=(),
        known_good_anchor=RECORDER_SOURCE_PIN,
        appcast_allowed=False,
        promotion_gates=(
            "recorder source submodule is pinned and reproducible",
            "Windows build produces an installer artifact",
            "installer artifact passes release asset and Windows installer smokes",
            "appcast sync runs only after a real GitHub release carries the artifact",
        ),
    ),
}


FALLBACK_ORDER: tuple[FallbackStep, ...] = (
    FallbackStep(
        name="latest_consumer_release",
        channel=ReleaseChannel.CONSUMER_INSTALLER,
        trigger="normal consumer/internal distribution",
        action=f"use {CURRENT_CONSUMER_TAG} and its appcast-pinned installer",
        gate="all three release/backend/windows smokes green",
    ),
    FallbackStep(
        name="previous_known_good_consumer",
        channel=ReleaseChannel.CONSUMER_INSTALLER,
        trigger="latest tag asset, checksum, or appcast smoke fails",
        action="roll appcast and internal download link back to the last green v0.x consumer tag",
        gate="same installer/SHA/appcast/windows smoke gates pass on the rollback tag",
    ),
    FallbackStep(
        name="bundled_recorder_reference",
        channel=ReleaseChannel.BUNDLED_RECORDER,
        trigger="consumer installer path cannot prove capture fidelity or offline MC assets are needed",
        action=f"use {BUNDLED_REFERENCE_TAG} as a QA/reference bundle, not as public appcast",
        gate="bundle manifest, mod jar matrix, and clean Windows gameplay smoke pass",
    ),
    FallbackStep(
        name="source_rebuild_candidate",
        channel=ReleaseChannel.SOURCE_CANDIDATE,
        trigger="both published consumer and bundled channels are stale for the required fix",
        action="build from the pinned recorder source and publish only after smokes pass",
        gate="new GitHub release carries installer and checksum, then appcast sync verifies it",
    ),
)


def contract_for_channel(channel: ReleaseChannel | str) -> ReleaseChannelContract:
    """Return the contract for one known release channel."""

    resolved = ReleaseChannel(channel)
    if resolved not in CHANNELS:
        raise ValueError(f"unknown release channel: {channel}")
    return CHANNELS[resolved]


def classify_asset_name(name: str) -> ReleaseChannel:
    """Classify a release asset name into one mutually exclusive channel."""

    matches = [
        channel
        for channel, contract in CHANNELS.items()
        if contract.asset_patterns and contract.matches_asset(name)
    ]
    if not matches:
        return ReleaseChannel.UNKNOWN
    if len(matches) > 1:
        joined = ", ".join(channel.value for channel in matches)
        raise ValueError(f"release asset {name!r} matches multiple channels: {joined}")
    return matches[0]


def appcast_channel() -> ReleaseChannelContract:
    """Return the only channel allowed to feed the public updater appcast."""

    return CHANNELS[ReleaseChannel.CONSUMER_INSTALLER]


def fallback_order() -> tuple[FallbackStep, ...]:
    """Return the ordered MECE recovery path for release issues."""

    return FALLBACK_ORDER

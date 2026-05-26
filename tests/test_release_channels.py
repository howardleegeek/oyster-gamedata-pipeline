from __future__ import annotations

from oyster_agent_runner.release_channels import (
    BUNDLED_REFERENCE_TAG,
    CHANNELS,
    CURRENT_CONSUMER_INSTALLER,
    CURRENT_CONSUMER_SHA256,
    CURRENT_CONSUMER_TAG,
    RECORDER_SOURCE_PIN,
    ReleaseChannel,
    appcast_channel,
    classify_asset_name,
    fallback_order,
)


def test_release_asset_classification_is_mece_for_known_assets() -> None:
    samples = {
        CURRENT_CONSUMER_INSTALLER: ReleaseChannel.CONSUMER_INSTALLER,
        "OysterRecorder-Setup-v2.6.0.exe": ReleaseChannel.CONSUMER_INSTALLER,
        "GameDataRecorder-Setup-recorder-v0.28.0-rc19.0.3.exe": (ReleaseChannel.BUNDLED_RECORDER),
        "OysterRecorder-onedir.zip": ReleaseChannel.BUNDLED_RECORDER,
        "OysterRecorder.exe": ReleaseChannel.BUNDLED_RECORDER,
        "oyster-recorder-mod-0.1.0-real-game-state-mc1.21.5.jar": (ReleaseChannel.BUNDLED_RECORDER),
        "SHA-256-manifest.txt": ReleaseChannel.BUNDLED_RECORDER,
        "random-debug.zip": ReleaseChannel.UNKNOWN,
    }

    for asset_name, expected in samples.items():
        assert classify_asset_name(asset_name) == expected


def test_appcast_is_consumer_installer_only() -> None:
    contract = appcast_channel()

    assert contract.channel == ReleaseChannel.CONSUMER_INSTALLER
    assert contract.appcast_allowed is True
    assert contract.known_good_anchor == CURRENT_CONSUMER_TAG
    assert contract.required_companion_assets == ("SHA256SUMS.txt",)
    assert "appcast" in " ".join(contract.promotion_gates)

    for channel, candidate in CHANNELS.items():
        if channel == ReleaseChannel.CONSUMER_INSTALLER:
            continue
        assert candidate.appcast_allowed is False


def test_known_anchors_capture_current_successful_lines() -> None:
    assert CURRENT_CONSUMER_TAG == "v0.11.15"
    assert CURRENT_CONSUMER_INSTALLER == "OysterRecorder-setup-v2.6.0.exe"
    assert len(CURRENT_CONSUMER_SHA256) == 64
    assert BUNDLED_REFERENCE_TAG == "recorder-v0.28.0-rc19.0.3"
    assert RECORDER_SOURCE_PIN.startswith("e171f20")


def test_fallback_order_is_explicit_and_ordered() -> None:
    steps = fallback_order()

    assert [step.name for step in steps] == [
        "latest_consumer_release",
        "previous_known_good_consumer",
        "bundled_recorder_reference",
        "source_rebuild_candidate",
    ]
    assert steps[0].channel == ReleaseChannel.CONSUMER_INSTALLER
    assert steps[1].channel == ReleaseChannel.CONSUMER_INSTALLER
    assert steps[2].channel == ReleaseChannel.BUNDLED_RECORDER
    assert steps[3].channel == ReleaseChannel.SOURCE_CANDIDATE
    assert "not as public appcast" in steps[2].action
    assert "new GitHub release" in steps[3].gate

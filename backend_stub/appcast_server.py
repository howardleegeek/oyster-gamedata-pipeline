"""S91v2 appcast server — serves XML for S15 updater."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Response

from oyster_agent_runner.release_channels import (
    CURRENT_CONSUMER_INSTALLER,
    CURRENT_CONSUMER_SHA256,
    CURRENT_CONSUMER_TAG,
)

router = APIRouter()


def _version_from_tag(tag: str) -> str:
    """Derive the bare semver from a consumer tag (recorder-v2.6.15 -> 2.6.15)."""

    return tag.removeprefix("recorder-").removeprefix("v")


# Single source of truth: the appcast defaults mirror the consumer contract in
# release_channels.py so the two can never drift (the historical drift to
# v0.16.0/v0.13.2 was the Backend Remote Smoke root cause).
DEFAULT_RECORDER_TAG = CURRENT_CONSUMER_TAG
DEFAULT_RECORDER_VERSION = _version_from_tag(CURRENT_CONSUMER_TAG)
DEFAULT_RECORDER_SHA256 = CURRENT_CONSUMER_SHA256
DEFAULT_INSTALLER_NAME = CURRENT_CONSUMER_INSTALLER
RELEASE_BASE_URL = "https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download"


def latest_release() -> dict[str, str]:
    tag = os.getenv("OYSTER_RECORDER_RELEASE_TAG", DEFAULT_RECORDER_TAG)
    version = os.getenv("OYSTER_RECORDER_RELEASE_VERSION", _version_from_tag(tag)).removeprefix("v")
    url = os.getenv(
        "OYSTER_RECORDER_DOWNLOAD_URL",
        f"{RELEASE_BASE_URL}/{tag}/{DEFAULT_INSTALLER_NAME}",
    )
    sha256 = os.getenv("OYSTER_RECORDER_SHA256", DEFAULT_RECORDER_SHA256)
    return {"version": version, "url": url, "sha256": sha256}


@router.get("/api/v1/updates/appcast.xml")
def appcast() -> Response:
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    latest = latest_release()
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" version="2.0">\n'
        "  <channel>\n"
        "    <title>Oyster Recorder Updates</title>\n"
        "    <item>\n"
        f"      <title>v{latest['version']}</title>\n"
        f"      <pubDate>{now}</pubDate>\n"
        f'      <enclosure url="{latest["url"]}" '
        f'sparkle:version="{latest["version"]}" sparkle:sha256="{latest["sha256"]}" '
        'type="application/octet-stream"/>\n'
        "    </item>\n"
        "  </channel>\n"
        "</rss>\n"
    )
    return Response(content=xml, media_type="application/xml")

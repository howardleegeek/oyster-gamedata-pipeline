"""S91v2 appcast server — serves XML for S15 updater."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Response

router = APIRouter()

DEFAULT_RECORDER_VERSION = "0.11.20"
DEFAULT_RECORDER_SHA256 = "7e99f1469513ab7dcbfda79798e88d82eb897d4c12108cb88f33af91559c29ac"
DEFAULT_INSTALLER_NAME = "OysterRecorder-setup-v2.6.0.exe"
RELEASE_BASE_URL = "https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download"


def latest_release() -> dict[str, str]:
    version = os.getenv("OYSTER_RECORDER_RELEASE_VERSION", DEFAULT_RECORDER_VERSION).removeprefix(
        "v"
    )
    tag = os.getenv("OYSTER_RECORDER_RELEASE_TAG", f"v{version}")
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
        f'      <title>v{latest["version"]}</title>\n'
        f"      <pubDate>{now}</pubDate>\n"
        f'      <enclosure url="{latest["url"]}" '
        f'sparkle:version="{latest["version"]}" sparkle:sha256="{latest["sha256"]}" '
        'type="application/octet-stream"/>\n'
        "    </item>\n"
        "  </channel>\n"
        "</rss>\n"
    )
    return Response(content=xml, media_type="application/xml")

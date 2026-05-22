"""S91v2 appcast server — serves XML for S15 updater."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Response

router = APIRouter()

LATEST = {
    "version": "0.8.10",
    "url": "https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download/v0.8.10/OysterRecorder-setup-v2.6.0.exe",
    "sha256": "bb1e3f12bc71fca9089e14fe3c40ca278af76fce042e4328bf2e8ab1d0d451e5",
}


@router.get("/api/v1/updates/appcast.xml")
def appcast() -> Response:
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" version="2.0">\n'
        "  <channel>\n"
        "    <title>Oyster Recorder Updates</title>\n"
        "    <item>\n"
        f'      <title>v{LATEST["version"]}</title>\n'
        f"      <pubDate>{now}</pubDate>\n"
        f'      <enclosure url="{LATEST["url"]}" '
        f'sparkle:version="{LATEST["version"]}" sparkle:sha256="{LATEST["sha256"]}" '
        'type="application/octet-stream"/>\n'
        "    </item>\n"
        "  </channel>\n"
        "</rss>\n"
    )
    return Response(content=xml, media_type="application/xml")

"""S91v2 appcast server — serves XML for S15 updater."""
from __future__ import annotations
from datetime import datetime, timezone
from fastapi import APIRouter, Response

router = APIRouter()

LATEST = {
    "version": "0.6.3",
    "url": "https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download/v0.6.3/OysterRecorder-setup-v0.6.3.exe",
    "sha256": "PLACEHOLDER_SHA256",
    "signature": "PLACEHOLDER_ED25519",
}


@router.get("/api/v1/updates/appcast.xml")
def appcast() -> Response:
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" version="2.0">\n'
        '  <channel>\n'
        '    <title>Oyster Recorder Updates</title>\n'
        '    <item>\n'
        f'      <title>v{LATEST["version"]}</title>\n'
        f'      <pubDate>{now}</pubDate>\n'
        f'      <enclosure url="{LATEST["url"]}" '
        f'sparkle:version="{LATEST["version"]}" sparkle:dsaSignature="{LATEST["signature"]}" type="application/octet-stream"/>\n'
        '    </item>\n'
        '  </channel>\n'
        '</rss>\n'
    )
    return Response(content=xml, media_type="application/xml")

---
task_id: S91v2-update-server-appcast
priority: 1
estimated_minutes: 20
modifies:
  - backend_stub/appcast_server.py
  - tests/test_appcast_server.py
executor: qwen3.6-plus
---

## 目标 — 必须实际 write_file

**Step 1**: read backend_stub/main.py to see FastAPI app structure.

**Step 2**: write_file('backend_stub/appcast_server.py') with EXACTLY this content:

```python
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
```

**Step 3**: write_file('tests/test_appcast_server.py') with:

```python
"""S91v2 tests."""
from fastapi.testclient import TestClient
from fastapi import FastAPI
from backend_stub.appcast_server import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_appcast_returns_xml() -> None:
    r = _client().get("/api/v1/updates/appcast.xml")
    assert r.status_code == 200
    assert "application/xml" in r.headers["content-type"]


def test_appcast_contains_version() -> None:
    r = _client().get("/api/v1/updates/appcast.xml")
    assert "v0.6.3" in r.text or "0.6.3" in r.text


def test_appcast_has_sparkle_namespace() -> None:
    r = _client().get("/api/v1/updates/appcast.xml")
    assert "sparkle" in r.text.lower()
```

**Step 4**: run pytest verify

run_cmd("python3 -m pytest tests/test_appcast_server.py -v")

必须 3 tests pass. 否则 read error + 修。

## 约束

- ≤ 12 turns
- 必须 2 个 write_file calls
- 不重写 backend_stub/main.py
- 直接 commit 到 branch `feat/S91v2-appcast-server`

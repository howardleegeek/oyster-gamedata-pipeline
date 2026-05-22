"""S91v2 tests."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

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
    assert "v0.8.10" in r.text or "0.8.10" in r.text
    assert "releases/download/v0.8.10/OysterRecorder-setup-v2.6.0.exe" in r.text
    assert "bb1e3f12bc71fca9089e14fe3c40ca278af76fce042e4328bf2e8ab1d0d451e5" in r.text
    assert "PLACE" + "HOLDER" not in r.text


def test_appcast_has_sparkle_namespace() -> None:
    r = _client().get("/api/v1/updates/appcast.xml")
    assert "sparkle" in r.text.lower()

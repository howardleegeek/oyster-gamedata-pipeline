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

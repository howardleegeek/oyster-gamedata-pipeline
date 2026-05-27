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
    assert "v0.12.0" in r.text or "0.12.0" in r.text
    assert "releases/download/v0.12.0/OysterRecorder-setup-v2.6.0.exe" in r.text
    assert "7e99f1469513ab7dcbfda79798e88d82eb897d4c12108cb88f33af91559c29ac" in r.text
    assert "PLACE" + "HOLDER" not in r.text


def test_appcast_has_sparkle_namespace() -> None:
    r = _client().get("/api/v1/updates/appcast.xml")
    assert "sparkle" in r.text.lower()


def test_appcast_supports_release_metadata_env_override(monkeypatch) -> None:
    expected_sha = "a" * 64
    monkeypatch.setenv("OYSTER_RECORDER_RELEASE_VERSION", "9.1.2")
    monkeypatch.setenv("OYSTER_RECORDER_RELEASE_TAG", "v9.1.2")
    monkeypatch.setenv("OYSTER_RECORDER_SHA256", expected_sha)

    r = _client().get("/api/v1/updates/appcast.xml")

    assert r.status_code == 200
    assert "v9.1.2" in r.text
    assert "releases/download/v9.1.2/OysterRecorder-setup-v2.6.0.exe" in r.text
    assert f'sparkle:sha256="{expected_sha}"' in r.text

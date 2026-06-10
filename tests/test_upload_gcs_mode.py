"""GCS direct-upload mode for /api/v1/upload/signed-url (scale path)."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client_with_gcs(monkeypatch) -> TestClient:
    monkeypatch.setenv("OYSTER_GCS_BUCKET", "oyster-gamedata-recordings")

    fake_blob = MagicMock()
    fake_blob.generate_signed_url.return_value = (
        "https://storage.googleapis.com/oyster-gamedata-recordings/"
        "uploads/abc.bin?X-Goog-Signature=sig"
    )
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_storage_client = MagicMock()
    fake_storage_client.bucket.return_value = fake_bucket

    gcs_mod = types.ModuleType("google.cloud.storage")
    gcs_mod.Client = MagicMock(return_value=fake_storage_client)
    cloud_mod = types.ModuleType("google.cloud")
    cloud_mod.storage = gcs_mod
    google_mod = sys.modules.get("google") or types.ModuleType("google")
    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud_mod)
    monkeypatch.setitem(sys.modules, "google.cloud.storage", gcs_mod)

    from backend_stub.main import create_app

    return TestClient(create_app()), fake_bucket


def test_signed_url_uses_gcs_when_bucket_configured(monkeypatch) -> None:
    client, fake_bucket = _client_with_gcs(monkeypatch)

    r = client.post(
        "/api/v1/upload/signed-url",
        json={"key": "uploads/abc.bin"},
        headers={"Authorization": "Bearer mock"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["key"] == "uploads/abc.bin"
    assert "storage.googleapis.com/oyster-gamedata-recordings" in body["url"]
    fake_bucket.blob.assert_called_with("uploads/abc.bin")


def test_signed_url_falls_back_to_memory_without_bucket(monkeypatch) -> None:
    monkeypatch.delenv("OYSTER_GCS_BUCKET", raising=False)
    from backend_stub.main import create_app

    client = TestClient(create_app())
    r = client.post(
        "/api/v1/upload/signed-url",
        json={"key": "uploads/x.bin"},
        headers={"Authorization": "Bearer mock"},
    )

    assert r.status_code == 200
    assert "/api/v1/upload/object/" in r.json()["url"]

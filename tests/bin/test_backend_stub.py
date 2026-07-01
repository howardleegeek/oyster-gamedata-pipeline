#!/usr/bin/env python3
"""
Tests for bin/backend_stub.py

Coverage:
- create_app: health endpoint, session CRUD, reset store
- main: CLI arguments, uvicorn startup
"""

import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

# Import the module under test
import bin.backend_stub as backend_stub


class TestCreateApp:
    """Tests for create_app function."""

    def test_health_endpoint(self):
        """Test health endpoint returns ok status."""
        app = backend_stub.create_app()
        client = TestClient(app)
        response = client.get("/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_create_session_without_id(self):
        """Test creating a session without providing session_id (auto-generates)."""
        backend_stub._reset_store()
        app = backend_stub.create_app()
        client = TestClient(app)
        
        response = client.post(
            "/v1/sessions",
            data={"game": "Minecraft", "pid": "12345", "window_title": "Game Window"},
        )
        assert response.status_code == 201
        data = response.json()
        assert "session_id" in data
        assert data["status"] == "received"
        assert data["video_size_bytes"] == 0

    def test_create_session_with_custom_id(self):
        """Test creating a session with a custom session_id."""
        backend_stub._reset_store()
        app = backend_stub.create_app()
        client = TestClient(app)
        
        response = client.post(
            "/v1/sessions",
            data={"session_id": "my-session-123", "game": "Terraria"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["session_id"] == "my-session-123"

    def test_create_session_with_metadata_json(self):
        """Test creating a session with metadata as JSON string."""
        backend_stub._reset_store()
        app = backend_stub.create_app()
        client = TestClient(app)
        
        metadata = json.dumps({"level": 10, "score": 5000})
        response = client.post(
            "/v1/sessions",
            data={
                "session_id": "meta-test",
                "metadata_json": metadata,
                "game": "Stardew Valley",
            },
        )
        assert response.status_code == 201
        data = response.json()
        # Check stored metadata
        stored = backend_stub._sessions["meta-test"]
        assert stored["metadata"]["level"] == 10
        assert stored["metadata"]["score"] == 5000
        assert stored["metadata"]["game"] == "Stardew Valley"

    def test_create_session_with_invalid_metadata_json(self):
        """Test that invalid JSON in metadata_json returns 400."""
        backend_stub._reset_store()
        app = backend_stub.create_app()
        client = TestClient(app)
        
        response = client.post(
            "/v1/sessions",
            data={"metadata_json": "not-valid-json"},
        )
        assert response.status_code == 400
        assert "Invalid metadata_json" in response.json()["detail"]

    def test_create_session_with_video(self):
        """Test creating a session with a video file."""
        backend_stub._reset_store()
        app = backend_stub.create_app()
        client = TestClient(app)
        
        video_content = b"fake video data"
        response = client.post(
            "/v1/sessions",
            files={"video": ("test.mp4", BytesIO(video_content), "video/mp4")},
            data={"session_id": "video-test", "game": "Minecraft"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["video_size_bytes"] == len(video_content)

    def test_list_sessions_empty(self):
        """Test listing sessions when store is empty."""
        backend_stub._reset_store()
        app = backend_stub.create_app()
        client = TestClient(app)
        
        response = client.get("/v1/sessions")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_sessions_with_data(self):
        """Test listing sessions after creating some."""
        backend_stub._reset_store()
        app = backend_stub.create_app()
        client = TestClient(app)
        
        # Create two sessions
        client.post("/v1/sessions", data={"session_id": "s1", "game": "Game1"})
        client.post("/v1/sessions", data={"session_id": "s2", "game": "Game2"})
        
        response = client.get("/v1/sessions")
        assert response.status_code == 200
        sessions = response.json()
        assert len(sessions) == 2

    def test_get_session_exists(self):
        """Test getting a specific session that exists."""
        backend_stub._reset_store()
        app = backend_stub.create_app()
        client = TestClient(app)
        
        # Create a session first
        client.post("/v1/sessions", data={"session_id": "get-test", "game": "Minecraft"})
        
        response = client.get("/v1/sessions/get-test")
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "get-test"
        assert data["metadata"]["game"] == "Minecraft"

    def test_get_session_not_found(self):
        """Test getting a session that doesn't exist returns 404."""
        backend_stub._reset_store()
        app = backend_stub.create_app()
        client = TestClient(app)
        
        response = client.get("/v1/sessions/nonexistent")
        assert response.status_code == 404
        assert "Session not found" in response.json()["detail"]

    def test_reset_store(self):
        """Test that reset_store clears all sessions."""
        backend_stub._reset_store()
        app = backend_stub.create_app()
        client = TestClient(app)
        
        # Create a session
        client.post("/v1/sessions", data={"session_id": "temp", "game": "Game"})
        
        # Reset
        backend_stub._reset_store()
        
        # List should be empty
        response = client.get("/v1/sessions")
        assert response.json() == []


class TestMain:
    """Tests for main CLI function."""

    def test_help_exits(self):
        """Test that --help exits with code 0."""
        with pytest.raises(SystemExit) as exc_info:
            backend_stub.main(["--help"])
        assert exc_info.value.code == 0




class TestSessionMetadata:
    """Additional tests for session metadata handling."""

    def test_metadata_form_fields_override_json(self):
        """Test that form field metadata overrides metadata_json."""
        backend_stub._reset_store()
        app = backend_stub.create_app()
        client = TestClient(app)
        
        metadata = json.dumps({"source": "json"})
        response = client.post(
            "/v1/sessions",
            data={
                "session_id": "override-test",
                "metadata_json": metadata,
                "game": "FromForm",
            },
        )
        assert response.status_code == 201
        stored = backend_stub._sessions["override-test"]
        # Form field should override JSON
        assert stored["metadata"]["game"] == "FromForm"
        assert stored["metadata"]["source"] == "json"

    def test_session_has_received_at_timestamp(self):
        """Test that sessions store received_at timestamp."""
        backend_stub._reset_store()
        app = backend_stub.create_app()
        client = TestClient(app)
        
        response = client.post("/v1/sessions", data={"session_id": "timestamp-test"})
        assert response.status_code == 201
        
        stored = backend_stub._sessions["timestamp-test"]
        assert "received_at" in stored
        assert stored["status"] == "received"

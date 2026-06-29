#!/usr/bin/env python3
"""
Tests for bin/prd_test_240_clip_cap.py

PRD p7 #2: Validate max 240 clips per scene — adapter stops at 241st.
"""

from bin.prd_test_240_clip_cap import (
    create_mock_scene,
    validate_clip_cap,
)


class TestCreateMockScene:
    """Tests for create_mock_scene function."""

    def test_create_empty_scene(self):
        """Test creating a scene with 0 clips."""
        scene = create_mock_scene(0)
        assert scene["scene_id"] == "test_scene_001"
        assert scene["clips"] == []

    def test_create_single_clip(self):
        """Test creating a scene with 1 clip."""
        scene = create_mock_scene(1)
        assert len(scene["clips"]) == 1
        assert scene["clips"][0]["id"] == "clip_0000"
        assert scene["clips"][0]["start"] == 0.0
        assert scene["clips"][0]["end"] == 10.0

    def test_create_multiple_clips(self):
        """Test creating a scene with multiple clips."""
        scene = create_mock_scene(5)
        assert len(scene["clips"]) == 5
        assert scene["clips"][0]["start"] == 0.0
        assert scene["clips"][4]["start"] == 40.0
        assert scene["clips"][4]["end"] == 50.0


class TestValidateClipCap:
    """Tests for validate_clip_cap function."""

    def test_at_exact_limit(self):
        """Test scene at exactly 240 clips is valid."""
        scene = create_mock_scene(240)
        result = validate_clip_cap(scene)
        assert result["valid"] is True
        assert result["clip_count"] == 240
        assert result["max_allowed"] == 240
        assert result["exceeded_by"] == 0
        assert result["stopped_at"] is None

    def test_below_limit(self):
        """Test scene below 240 clips is valid."""
        scene = create_mock_scene(200)
        result = validate_clip_cap(scene)
        assert result["valid"] is True
        assert result["clip_count"] == 200
        assert result["exceeded_by"] == 0

    def test_over_limit(self):
        """Test scene exceeding 240 clips is invalid."""
        scene = create_mock_scene(250)
        result = validate_clip_cap(scene)
        assert result["valid"] is False
        assert result["clip_count"] == 250
        assert result["exceeded_by"] == 10
        assert result["stopped_at"] == 241

    def test_way_over_limit(self):
        """Test scene far exceeding 240 clips is invalid."""
        scene = create_mock_scene(300)
        result = validate_clip_cap(scene)
        assert result["valid"] is False
        assert result["clip_count"] == 300
        assert result["exceeded_by"] == 60
        assert result["stopped_at"] == 241

    def test_empty_scene(self):
        """Test empty scene is valid."""
        scene = create_mock_scene(0)
        result = validate_clip_cap(scene)
        assert result["valid"] is True
        assert result["clip_count"] == 0

    def test_custom_max_clips(self):
        """Test with custom max clips value."""
        scene = create_mock_scene(50)
        result = validate_clip_cap(scene, max_clips=100)
        assert result["valid"] is True
        assert result["max_allowed"] == 100

    def test_custom_max_exceeded(self):
        """Test with custom max clips that is exceeded."""
        scene = create_mock_scene(150)
        result = validate_clip_cap(scene, max_clips=100)
        assert result["valid"] is False
        assert result["exceeded_by"] == 50
        assert result["stopped_at"] == 101

    def test_message_at_limit(self):
        """Test message when at exact limit."""
        scene = create_mock_scene(240)
        result = validate_clip_cap(scene)
        assert "240/240" in result["message"]
        assert "exceeded" not in result["message"]

    def test_message_over_limit(self):
        """Test message when over limit."""
        scene = create_mock_scene(250)
        result = validate_clip_cap(scene)
        assert "250/240" in result["message"]
        assert "exceeded by 10" in result["message"]

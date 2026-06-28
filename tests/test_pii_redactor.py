#!/usr/bin/env python3
"""
Tests for PII Redactor — screen-capture (OCR) redaction + text redaction.
"""

import importlib
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

# Add bin to path
sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def redactor_mod():
    """Import (or re-import) pii_redactor so tests get a fresh module."""
    mod = importlib.import_module("pii_redactor")
    importlib.reload(mod)
    return mod


@pytest.fixture
def mock_ocr_image_with_email():
    """
    Create a PIL image that contains an email address rendered as text.
    We use a simple bitmap approach so the test does not depend on
    system fonts.
    """
    img = Image.new("RGB", (400, 100), color="white")
    draw = ImageDraw.Draw(img)
    # Draw the email text — use default font (bitmap, always available)
    draw.text((10, 10), "user@example.com", fill="black")
    return img


@pytest.fixture
def mock_ocr_image_with_discord():
    """Create an image with a Discord tag."""
    img = Image.new("RGB", (400, 100), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), "CoolGamer#1234", fill="black")
    return img


@pytest.fixture
def mock_ocr_image_with_phone():
    """Create an image with a phone number."""
    img = Image.new("RGB", (400, 100), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), "555-123-4567", fill="black")
    return img


@pytest.fixture
def mock_ocr_image_clean():
    """Create an image with no PII."""
    img = Image.new("RGB", (400, 100), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), "Hello world no PII here", fill="black")
    return img


# ---------------------------------------------------------------------------
# Tests: PII regex detection
# ---------------------------------------------------------------------------


class TestPIIDetection:
    """Test the find_pii_in_text helper."""

    def test_detect_email(self, redactor_mod):
        text = "Contact me at alice@example.com for info"
        matches = redactor_mod.find_pii_in_text(text)
        assert any("alice@example.com" in m[0] for m in matches)

    def test_detect_phone(self, redactor_mod):
        text = "Call 555-123-4567 or (800) 555-0199"
        matches = redactor_mod.find_pii_in_text(text)
        assert len(matches) >= 1

    def test_detect_discord_tag(self, redactor_mod):
        text = "Add me @CoolGamer#1234 on Discord"
        matches = redactor_mod.find_pii_in_text(text)
        assert any("@CoolGamer#1234" in m[0] for m in matches)

    def test_detect_discord_name(self, redactor_mod):
        text = "Hey @friend let's play"
        matches = redactor_mod.find_pii_in_text(text)
        assert any("@friend" in m[0] for m in matches)

    def test_no_pii(self, redactor_mod):
        text = "The quick brown fox jumps over the lazy dog"
        matches = redactor_mod.find_pii_in_text(text)
        assert len(matches) == 0

    def test_multiple_pii(self, redactor_mod):
        text = "Email: test@test.com Phone: 555-867-5309 Discord: @user#0001"
        matches = redactor_mod.find_pii_in_text(text)
        assert len(matches) >= 3


# ---------------------------------------------------------------------------
# Tests: Frame redaction (redact_frame)
# ---------------------------------------------------------------------------


class TestFrameRedaction:
    """Test redact_frame on PIL images."""

    def test_redact_email_in_image(self, redactor_mod, mock_ocr_image_with_email):
        """When pytesseract is available, email region should be blacked out."""
        img = mock_ocr_image_with_email.copy()
        # Mock pytesseract to return the email text in a bounding box
        with mock.patch.object(redactor_mod, "_get_pytesseract") as mock_get:
            fake_pt = mock.MagicMock()
            fake_pt.Output.DICT = "dict"
            fake_pt.image_to_data.return_value = {
                "text": ["user@example.com"],
                "conf": [95],
                "left": [10],
                "top": [10],
                "width": [200],
                "height": [30],
            }
            mock_get.return_value = fake_pt

            result, count = redactor_mod.redact_frame(img)

            assert count >= 1
            # Verify the region is now black
            pixel = result.getpixel((110, 25))  # centre of the box
            assert pixel == (0, 0, 0), f"Expected black, got {pixel}"

    def test_redact_discord_in_image(self, redactor_mod, mock_ocr_image_with_discord):
        """Discord tag region should be blacked out."""
        img = mock_ocr_image_with_discord.copy()
        with mock.patch.object(redactor_mod, "_get_pytesseract") as mock_get:
            fake_pt = mock.MagicMock()
            fake_pt.Output.DICT = "dict"
            fake_pt.image_to_data.return_value = {
                "text": ["@CoolGamer#1234"],
                "conf": [90],
                "left": [10],
                "top": [10],
                "width": [180],
                "height": [30],
            }
            mock_get.return_value = fake_pt

            result, count = redactor_mod.redact_frame(img)

            assert count >= 1
            pixel = result.getpixel((100, 25))
            assert pixel == (0, 0, 0)

    def test_redact_phone_in_image(self, redactor_mod, mock_ocr_image_with_phone):
        """Phone number region should be blacked out."""
        img = mock_ocr_image_with_phone.copy()
        with mock.patch.object(redactor_mod, "_get_pytesseract") as mock_get:
            fake_pt = mock.MagicMock()
            fake_pt.Output.DICT = "dict"
            fake_pt.image_to_data.return_value = {
                "text": ["555-123-4567"],
                "conf": [88],
                "left": [10],
                "top": [10],
                "width": [150],
                "height": [30],
            }
            mock_get.return_value = fake_pt

            result, count = redactor_mod.redact_frame(img)

            assert count >= 1
            pixel = result.getpixel((85, 25))
            assert pixel == (0, 0, 0)

    def test_clean_image_no_redaction(self, redactor_mod, mock_ocr_image_clean):
        """Image with no PII should have zero redactions."""
        img = mock_ocr_image_clean.copy()
        with mock.patch.object(redactor_mod, "_get_pytesseract") as mock_get:
            fake_pt = mock.MagicMock()
            fake_pt.Output.DICT = "dict"
            fake_pt.image_to_data.return_value = {
                "text": ["Hello", "world", "no", "PII", "here"],
                "conf": [90, 90, 90, 90, 90],
                "left": [10, 60, 120, 180, 240],
                "top": [10, 10, 10, 10, 10],
                "width": [40, 40, 20, 30, 40],
                "height": [20, 20, 20, 20, 20],
            }
            mock_get.return_value = fake_pt

            result, count = redactor_mod.redact_frame(img)

            assert count == 0

    def test_redact_returns_same_image_object(self, redactor_mod, mock_ocr_image_clean):
        """redact_frame should modify and return the same image object."""
        img = mock_ocr_image_clean.copy()
        with mock.patch.object(redactor_mod, "_get_pytesseract", return_value=None):
            result, count = redactor_mod.redact_frame(img)
            assert result is img


# ---------------------------------------------------------------------------
# Tests: Graceful degradation when pytesseract is missing
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """Verify behaviour when pytesseract is unavailable."""

    def test_get_pytesseract_returns_none_when_missing(self, redactor_mod):
        """When pytesseract cannot be imported, _get_pytesseract returns None."""
        # Force reload to clear any cached module; simulate ImportError
        with mock.patch.dict(sys.modules, {"pytesseract": None}), mock.patch(
            "builtins.__import__", side_effect=ImportError("no module")
        ):
            # Reset the module-level cache
            redactor_mod._pytesseract = None
            redactor_mod._pytesseract_error = None
            result = redactor_mod._get_pytesseract()
            assert result is None

    def test_redact_frame_without_ocr(self, redactor_mod, mock_ocr_image_clean):
        """redact_frame should return 0 redactions when OCR is unavailable."""
        img = mock_ocr_image_clean.copy()
        with mock.patch.object(redactor_mod, "_get_pytesseract", return_value=None):
            result, count = redactor_mod.redact_frame(img)
            assert count == 0
            # Image should be unchanged (still white at centre)
            pixel = result.getpixel((200, 50))
            assert pixel == (255, 255, 255)

    def test_redact_rgb_directory_without_ocr(self, redactor_mod, tmp_path):
        """redact_rgb_directory should work (with 0 redactions) when OCR is missing."""
        rgb_dir = tmp_path / "rgb"
        rgb_dir.mkdir()
        # Create a dummy PNG
        img = Image.new("RGB", (100, 100), "white")
        img.save(rgb_dir / "frame_001.png")

        with mock.patch.object(redactor_mod, "_get_pytesseract", return_value=None):
            stats = redactor_mod.redact_rgb_directory(tmp_path)

        assert stats["frames_processed"] == 1
        assert stats["total_redactions"] == 0
        assert stats["ocr_available"] is False

    def test_redact_rgb_directory_no_rgb_folder(self, redactor_mod, tmp_path):
        """Should return empty stats when rgb/ directory doesn't exist."""
        stats = redactor_mod.redact_rgb_directory(tmp_path)
        assert stats["frames_processed"] == 0
        assert stats["total_redactions"] == 0


# ---------------------------------------------------------------------------
# Tests: redact_frame_file (file I/O)
# ---------------------------------------------------------------------------


class TestFrameFileRedaction:
    """Test redact_frame_file."""

    def test_redact_frame_file_overwrites(self, redactor_mod, tmp_path):
        """redact_frame_file should overwrite the original file."""
        img = Image.new("RGB", (200, 100), "white")
        draw = ImageDraw.Draw(img)
        draw.text((10, 10), "test@example.com", fill="black")
        frame_path = tmp_path / "test_frame.png"
        img.save(frame_path)

        with mock.patch.object(redactor_mod, "_get_pytesseract") as mock_get:
            fake_pt = mock.MagicMock()
            fake_pt.Output.DICT = "dict"
            fake_pt.image_to_data.return_value = {
                "text": ["test@example.com"],
                "conf": [95],
                "left": [10],
                "top": [10],
                "width": [180],
                "height": [30],
            }
            mock_get.return_value = fake_pt

            count = redactor_mod.redact_frame_file(frame_path)

        assert count >= 1
        # Verify file was modified
        result_img = Image.open(frame_path)
        pixel = result_img.getpixel((100, 25))
        assert pixel == (0, 0, 0)

    def test_redact_frame_file_output_path(self, redactor_mod, tmp_path):
        """redact_frame_file should write to output_path when given."""
        img = Image.new("RGB", (200, 100), "white")
        frame_path = tmp_path / "input.png"
        output_path = tmp_path / "output.png"
        img.save(frame_path)

        with mock.patch.object(redactor_mod, "_get_pytesseract", return_value=None):
            count = redactor_mod.redact_frame_file(frame_path, output_path=output_path)

        assert count == 0
        assert output_path.exists()
        assert frame_path.exists()  # original untouched


# ---------------------------------------------------------------------------
# Tests: redact_rgb_directory (integration)
# ---------------------------------------------------------------------------


class TestRgbDirectoryRedaction:
    """Test redact_rgb_directory end-to-end."""

    def test_processes_multiple_frames(self, redactor_mod, tmp_path):
        """Should process all image files in rgb/."""
        rgb_dir = tmp_path / "rgb"
        rgb_dir.mkdir()
        for i in range(3):
            img = Image.new("RGB", (100, 100), "white")
            img.save(rgb_dir / f"frame_{i:03d}.png")

        with mock.patch.object(redactor_mod, "_get_pytesseract", return_value=None):
            stats = redactor_mod.redact_rgb_directory(tmp_path)

        assert stats["frames_processed"] == 3
        assert stats["total_redactions"] == 0

    def test_ignores_non_image_files(self, redactor_mod, tmp_path):
        """Should skip non-image files in rgb/."""
        rgb_dir = tmp_path / "rgb"
        rgb_dir.mkdir()
        img = Image.new("RGB", (100, 100), "white")
        img.save(rgb_dir / "frame_001.png")
        (rgb_dir / "notes.txt").write_text("some notes")
        (rgb_dir / "data.json").write_text("{}")

        with mock.patch.object(redactor_mod, "_get_pytesseract", return_value=None):
            stats = redactor_mod.redact_rgb_directory(tmp_path)

        assert stats["frames_processed"] == 1

    def test_redaction_count_per_session_logged(self, redactor_mod, tmp_path):
        """redact_session should log frame redaction count."""
        session_dir = tmp_path / "session_001"
        session_dir.mkdir()
        rgb_dir = session_dir / "rgb"
        rgb_dir.mkdir()

        # Create a frame with PII
        img = Image.new("RGB", (400, 100), "white")
        draw = ImageDraw.Draw(img)
        draw.text((10, 10), "user@example.com", fill="black")
        img.save(rgb_dir / "frame_001.png")

        # Create game_state.jsonl so redact_session finds a player
        gs = session_dir / "game_state.jsonl"
        gs.write_text(json.dumps({"player": "TestPlayer", "ts": 0}) + "\n")

        with mock.patch.object(redactor_mod, "_get_pytesseract") as mock_get:
            fake_pt = mock.MagicMock()
            fake_pt.Output.DICT = "dict"
            fake_pt.image_to_data.return_value = {
                "text": ["user@example.com"],
                "conf": [95],
                "left": [10],
                "top": [10],
                "width": [200],
                "height": [30],
            }
            mock_get.return_value = fake_pt

            stats = redactor_mod.redact_session(session_dir)

        assert "frame_redactions" in stats
        assert stats["frame_redactions"]["total_redactions"] >= 1
        assert stats["frame_redactions"]["frames_processed"] == 1

        # Verify redaction log was written
        log_file = session_dir / "redaction_log.jsonl"
        assert log_file.exists()
        log_data = json.loads(log_file.read_text().strip())
        assert log_data["frame_redactions"] >= 1


# ---------------------------------------------------------------------------
# Tests: Existing text redaction (regression)
# ---------------------------------------------------------------------------


class TestTextRedaction:
    """Regression tests for existing text-based redaction."""

    def test_sha8(self, redactor_mod):
        assert len(redactor_mod.sha8("hello")) == 8

    def test_pseudonymize_username(self, redactor_mod):
        result = redactor_mod.pseudonymize_username("TestPlayer")
        assert result.startswith("player_")
        assert "TestPlayer" not in result

    def test_mask_ip(self, redactor_mod):
        assert redactor_mod.mask_ip("1.2.3.4") == "1.2.3.0"

    def test_redact_file_content_email(self, redactor_mod):
        content = "Email: alice@example.com"
        result = redactor_mod.redact_file_content(content, "", "")
        assert "alice@example.com" not in result
        assert "[email_redacted]" in result

    def test_redact_file_content_phone(self, redactor_mod):
        content = "Phone: 555-123-4567"
        result = redactor_mod.redact_file_content(content, "", "")
        assert "555-123-4567" not in result
        assert "[phone_redacted]" in result

    def test_redact_file_content_discord(self, redactor_mod):
        content = "Discord: @CoolGamer#1234"
        result = redactor_mod.redact_file_content(content, "", "")
        assert "@CoolGamer#1234" not in result

    def test_redact_jsonl_file(self, redactor_mod, tmp_path):
        filepath = tmp_path / "test.jsonl"
        filepath.write_text(json.dumps({"player": "TestUser", "chat": "hello"}) + "\n")
        count = redactor_mod.redact_jsonl_file(filepath, "TestUser", "player_abc12345")
        assert count >= 1
        content = filepath.read_text()
        assert "TestUser" not in content
        assert "player_abc12345" in content

    def test_redact_session_dry_run(self, redactor_mod, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        gs = session_dir / "game_state.jsonl"
        gs.write_text(json.dumps({"player": "TestPlayer"}) + "\n")

        stats = redactor_mod.redact_session(session_dir, dry_run=True)
        assert stats["dry_run"] is True
        assert stats["player_username"] == "TestPlayer"

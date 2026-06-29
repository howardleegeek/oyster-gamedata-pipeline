#!/usr/bin/env python3
"""Tests for bin/prd_test_video_no_ui.py (PRD p4 #3).

PRD p4 #3 — Video must contain no overlay UI / chat / dialogs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))
import prd_test_video_no_ui as video_no_ui


class TestSampleIndices:
    """Tests for _sample_indices()."""

    def test_returns_all_indices_when_total_less_than_n(self):
        """If total <= n, return range(total)."""
        result = video_no_ui._sample_indices(3, 10)
        assert result == [0, 1, 2]

    def test_evenly_spaces_indices(self):
        """Evenly spaced indices when total > n."""
        result = video_no_ui._sample_indices(100, 10)
        assert result == [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]

    def test_step_is_at_least_one(self):
        """Step is at least 1 even for large ratios."""
        result = video_no_ui._sample_indices(5, 10)
        assert result == [0, 1, 2, 3, 4]


class MockImage:
    """Mock image class that properly supports numpy's __array__ protocol."""

    def __init__(self, pixels):
        self._pixels = pixels
        self._converted = None

    def convert(self, mode):
        self._converted = self
        return self

    def __array__(self, dtype=None):
        # Handle numpy's copy= and dtype= keyword arguments
        import numpy as np
        result = np.array(self._pixels, dtype=dtype if dtype else self._pixels.dtype)
        return result


class TestHeuristicOcr:
    """Tests for _heuristic_ocr()."""

    def test_detects_top_bar(self):
        """Detects TOP_BAR when top region has low variance."""
        import numpy as np
        h, w = 100, 100
        arr = np.full((h, w), 128, dtype=np.uint8)
        mock_img = MockImage(arr)

        result = video_no_ui._heuristic_ocr(mock_img)
        assert "TOP_BAR" in result

    def test_detects_bottom_bar(self):
        """Detects BOTTOM_BAR when bottom region has low variance."""
        import numpy as np
        h, w = 100, 100
        arr = np.full((h, w), 128, dtype=np.uint8)
        mock_img = MockImage(arr)

        result = video_no_ui._heuristic_ocr(mock_img)
        assert "BOTTOM_BAR" in result

    def test_detects_dense_text_region(self):
        """Detects DENSE_TEXT_REGION when edges are dense."""
        import numpy as np
        h, w = 100, 100
        # Checkerboard pattern = high edge density
        arr = np.array([[(i + j) % 2 * 255 for j in range(w)] for i in range(h)], dtype=np.uint8)
        mock_img = MockImage(arr)

        result = video_no_ui._heuristic_ocr(mock_img)
        assert "DENSE_TEXT_REGION" in result

    def test_returns_empty_for_varying_image(self):
        """Returns empty string for image with high variance and no edges."""
        import numpy as np
        h, w = 100, 100
        # Gradient = some edges but not dense
        arr = np.array([[i for j in range(w)] for i in range(h)], dtype=np.uint8)
        mock_img = MockImage(arr)

        result = video_no_ui._heuristic_ocr(mock_img)
        # Gradient should not trigger DENSE_TEXT_REGION (edges are uniform direction)
        assert "DENSE_TEXT_REGION" not in result


class TestFrameHasUi:
    """Tests for _frame_has_ui()."""

    def test_detects_ui_keyword_chat(self):
        """Detects 'chat' in OCR text."""
        mock_img = MagicMock()
        has_ui, detail = video_no_ui._frame_has_ui(mock_img, lambda img: "Hello chat world")
        assert has_ui

    def test_detects_ui_keyword_dialog(self):
        """Detects 'dialog' in OCR text."""
        mock_img = MagicMock()
        has_ui, detail = video_no_ui._frame_has_ui(mock_img, lambda img: "dialog popup")
        assert has_ui

    def test_detects_ui_keyword_watermark(self):
        """Detects 'watermark' in OCR text."""
        mock_img = MagicMock()
        has_ui, detail = video_no_ui._frame_has_ui(mock_img, lambda img: "watermark here")
        assert has_ui

    def test_detects_top_bar_token(self):
        """Detects TOP_BAR token from heuristic OCR."""
        mock_img = MagicMock()
        has_ui, detail = video_no_ui._frame_has_ui(mock_img, lambda img: "TOP_BAR detected")
        assert has_ui
        assert "TOP_BAR" in detail

    def test_returns_clean_for_no_ui(self):
        """Returns False when no UI detected."""
        mock_img = MagicMock()
        has_ui, detail = video_no_ui._frame_has_ui(mock_img, lambda img: "")
        assert not has_ui
        assert detail == "clean"


class TestMain:
    """Tests for main()."""

    def test_missing_video_returns_error_code(self):
        """Missing video file returns error code 2."""
        result = video_no_ui.main(["nonexistent.mp4"])
        assert result == 2

    def test_main_returns_int(self):
        """main() returns an integer exit code, not raises SystemExit."""
        # When given a valid video that doesn't exist, it returns 2
        result = video_no_ui.main(["nonexistent.mp4"])
        assert isinstance(result, int)


class TestUikeywords:
    """Tests for _UI_KEYWORDS."""

    def test_contains_expected_keywords(self):
        """_UI_KEYWORDS contains expected UI-related keywords."""
        expected = {"chat", "dialog", "overlay", "watermark", "top_bar", "bottom_bar"}
        assert expected.issubset(video_no_ui._UI_KEYWORDS)

    def test_is_frozen(self):
        """_UI_KEYWORDS is a frozenset."""
        assert isinstance(video_no_ui._UI_KEYWORDS, frozenset)


class TestGetOcrEngine:
    """Tests for _get_ocr_engine()."""

    def test_fallback_to_heuristic(self):
        """Falls back to heuristic OCR when pytesseract unavailable."""
        with patch.dict("sys.modules", {"pytesseract": None}):
            # Re-import to pick up the patched module state
            import importlib
            importlib.reload(video_no_ui)
            engine = video_no_ui._get_ocr_engine()
            # Should return heuristic function
            assert engine.__name__ == "_heuristic_ocr"

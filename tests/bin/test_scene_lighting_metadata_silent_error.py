"""
Regression tests for silent error swallows in bin/scene_lighting_metadata.py.

These tests verify that a failed PIL image open inside
`infer_weather_from_image` is logged at debug level (binding the
exception) rather than silently swallowed. The function must still
return a WeatherState with default values so the pipeline continues.
"""

import ast
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


class TestSceneLightingMetadataSilentError:
    """Tests for silent error handling in scene_lighting_metadata.py."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "scene_lighting_metadata.py"
        ).read_text()

    def test_no_bare_except_in_infer_weather(self):
        """The infer_weather_from_image function must not have a bare
        ``except Exception:`` (no ``as`` binding) that hides the error."""
        source = self._read_source()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "infer_weather_from_image"
            ):
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        if child.type is not None:
                            type_src = ast.unparse(child.type)
                            if "Exception" in type_src and child.name is None:
                                pytest.fail(
                                    "Found bare 'except Exception:' "
                                    "(no 'as' binding) in "
                                    "infer_weather_from_image. "
                                    "Bind the exception and log it "
                                    "via logger.debug(...)."
                                )

    def test_logger_imported(self):
        """A module-level logger must be defined so the exception can be logged."""
        source = self._read_source()
        assert "import logging" in source, "logging import missing"
        assert "logger = logging.getLogger" in source, (
            "module-level logger definition missing"
        )

    def test_image_open_failure_logs_at_debug(self, caplog):
        """When PIL Image.open raises, the exception is surfaced via
        a debug log AND the function still returns default WeatherState."""
        # Ensure clean import
        sys.modules.pop("bin.scene_lighting_metadata", None)

        # Patch PIL.Image.open to raise a synthetic error
        with patch("PIL.Image.open", side_effect=RuntimeError("synthetic image load failure")):
            # Import after patching
            import bin.scene_lighting_metadata as slm

            with caplog.at_level(logging.DEBUG, logger="bin.scene_lighting_metadata"):
                result = slm.infer_weather_from_image(Path("/nonexistent/image.png"))

        # Should return default weather state (overcast, since avg_brightness=0.5 is not > 0.5)
        assert result.condition == "overcast", (
            f"Expected default condition 'overcast' (brightness=0.5 is not > 0.5), got {result.condition}"
        )

        # The debug log must contain the exception message
        assert any(
            "synthetic image load failure" in record.message
            for record in caplog.records
        ), "Exception message not found in debug log"

    def test_valid_image_still_works(self):
        """Regression: valid images should still produce correct weather inference."""
        sys.modules.pop("bin.scene_lighting_metadata", None)

        # Create a fake image that returns bright pixels
        with patch("PIL.Image.open") as mock_open:
            mock_img = mock_open.return_value.__enter__.return_value
            mock_img.convert.return_value.get_flattened_data.return_value = [200] * 100

            import bin.scene_lighting_metadata as slm
            result = slm.infer_weather_from_image(Path("/fake/bright.png"))

        # Bright image -> clear weather
        assert result.condition == "clear"

"""
Regression tests for silent error swallows in bin/generate_manifest.py.

These tests verify that failed metadata extraction calls are logged at debug
level (binding the exception) rather than silently swallowed.
"""

import ast
import json
import os
import sys
import tarfile
from io import BytesIO
from pathlib import Path
from unittest import mock

import pytest

# Add bin to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bin.generate_manifest import extract_clip_metadata


class TestGenerateManifestSilentError:
    """Tests for silent error handling in generate_manifest.py."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "generate_manifest.py"
        ).read_text()

    def test_logger_imported(self):
        """The module must import logger for debug logging."""
        source = self._read_source()
        assert "logger = logging.getLogger(__name__)" in source, \
            "Module must import and define logger for debug logging"

    def test_no_bare_except_in_extract_clip_metadata(self):
        """The extract_clip_metadata function must not have a bare
        ``except Exception:`` (no ``as`` binding) that hides the error."""
        source = self._read_source()
        tree = ast.parse(source)

        # Find the extract_clip_metadata function
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "extract_clip_metadata"
            ):
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        if child.type is not None:
                            type_src = ast.unparse(child.type)
                            if "Exception" in type_src and child.name is None:
                                pytest.fail(
                                    "Found bare 'except Exception:' "
                                    "(no 'as' binding) in "
                                    "extract_clip_metadata. "
                                    "Bind the exception and log it "
                                    "via logger.debug(...)."
                                )

    def test_extract_clip_metadata_logs_json_error(self, tmp_path):
        """If JSON parsing fails, the error should be logged via debug."""
        # Create a tarball with malformed JSON in action_camera.json
        tarball_path = tmp_path / "vendor-001_batch-2026-05-A_clip-00001_v1.tar.gz"
        with tarfile.open(tarball_path, "w:gz") as tar:
            # Add a malformed JSON file
            json_content = b'{"invalid": json'
            info = tarfile.TarInfo(name="action_camera.json")
            info.size = len(json_content)
            tar.addfile(info, BytesIO(json_content))

        # Patch logger to capture debug calls
        with mock.patch("bin.generate_manifest.logger") as mock_logger:
            result = extract_clip_metadata(str(tarball_path))
            # Should return default values
            assert result["clip_id"] == "clip-00001"  # parsed from filename
            assert result["duration_sec"] == 0.0
            # Should have logged the error
            mock_logger.debug.assert_called()
            call_args = mock_logger.debug.call_args
            assert "JSONDecodeError" in str(call_args) or "action_camera.json" in str(call_args)

    def test_extract_clip_metadata_logs_openpyxl_error(self, tmp_path):
        """If openpyxl parsing fails, the error should be logged via debug."""
        # Create a tarball with an Excel file that causes an error
        tarball_path = tmp_path / "vendor-001_batch-2026-05-A_clip-00002_v1.tar.gz"
        with tarfile.open(tarball_path, "w:gz") as tar:
            # Add action_camera.json with valid content
            json_content = json.dumps({
                "duration_sec": 300.0,
                "frame_count": 9000,
                "route_type": 1
            }).encode()
            info = tarfile.TarInfo(name="action_camera.json")
            info.size = len(json_content)
            tar.addfile(info, BytesIO(json_content))

            # Add a corrupt/invalid Excel file
            excel_content = b"This is not a valid Excel file"
            info2 = tarfile.TarInfo(name="gameinfo.xlsx")
            info2.size = len(excel_content)
            tar.addfile(info2, BytesIO(excel_content))

        # Patch logger to capture debug calls
        with mock.patch("bin.generate_manifest.logger") as mock_logger:
            result = extract_clip_metadata(str(tarball_path))
            # Should return values from JSON, but empty scene/operator_id
            assert result["duration_sec"] == 300.0
            assert result["frame_count"] == 9000
            # Should have logged the error
            mock_logger.debug.assert_called()
            call_args = mock_logger.debug.call_args
            assert "gameinfo.xlsx" in str(call_args) or "Failed to extract" in str(call_args)

    def test_extract_clip_metadata_openpyxl_not_available(self, tmp_path, monkeypatch):
        """If openpyxl is not available, should log debug and continue."""
        # Create a tarball with an Excel file
        tarball_path = tmp_path / "vendor-001_batch-2026-05-A_clip-00003_v1.tar.gz"
        with tarfile.open(tarball_path, "w:gz") as tar:
            json_content = json.dumps({
                "duration_sec": 300.0,
                "frame_count": 9000,
                "route_type": 1
            }).encode()
            info = tarfile.TarInfo(name="action_camera.json")
            info.size = len(json_content)
            tar.addfile(info, BytesIO(json_content))

            excel_content = b"dummy excel"
            info2 = tarfile.TarInfo(name="gameinfo.xlsx")
            info2.size = len(excel_content)
            tar.addfile(info2, BytesIO(excel_content))

        # Force `import openpyxl` to raise ImportError by injecting a
        # placeholder that always raises on attribute access. We use
        # sys.modules (the actual mechanism the function relies on) so
        # we don't need to monkey-patch __import__ (which is a dict in
        # module scope and can't be set with monkeypatch.setattr).
        class _RaisingOpenpyxl:
            def __getattr__(self, name):
                raise ImportError("No module named 'openpyxl'")

        monkeypatch.setitem(sys.modules, "openpyxl", _RaisingOpenpyxl())

        # Patch logger to capture debug calls
        with mock.patch("bin.generate_manifest.logger") as mock_logger:
            result = extract_clip_metadata(str(tarball_path))
            # Should return values from JSON
            assert result["duration_sec"] == 300.0
            # Should have logged about openpyxl not being available
            mock_logger.debug.assert_called()
            call_args = mock_logger.debug.call_args
            assert "openpyxl" in str(call_args).lower()

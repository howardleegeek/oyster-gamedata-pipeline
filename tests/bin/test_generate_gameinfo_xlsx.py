#!/usr/bin/env python3
"""
Tests for bin/generate_gameinfo_xlsx.py
"""

import os
import sys
import tempfile
import pytest
from datetime import date
from unittest.mock import patch, MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bin.generate_gameinfo_xlsx import (
    build_gameinfo_dict,
    write_xlsx,
    read_xlsx,
    main,
    validate_route_type,
    FIELD_NAMES,
)


class TestBuildGameinfoDict:
    """Tests for build_gameinfo_dict function."""
    
    def test_build_dict_has_14_fields(self):
        """Test that build_gameinfo_dict returns exactly 14 fields."""
        result = build_gameinfo_dict()
        
        # Check we have exactly 14 fields
        assert len(result) == 14, f"Expected 14 fields, got {len(result)}"
        
        # Check all expected field names are present
        for field in FIELD_NAMES:
            assert field in result, f"Missing field: {field}"
    
    def test_recording_date_defaults_to_today(self):
        """Test that recording_date defaults to today's ISO date when None."""
        result = build_gameinfo_dict(recording_date=None)
        
        today_iso = date.today().isoformat()
        assert result["recording_date"] == today_iso, \
            f"Expected today's date {today_iso}, got {result['recording_date']}"
    
    def test_recording_date_preserved_when_provided(self):
        """Test that recording_date is preserved when explicitly provided."""
        result = build_gameinfo_dict(recording_date="2024-01-15")
        
        assert result["recording_date"] == "2024-01-15"
    
    def test_all_fields_have_correct_defaults(self):
        """Test that all fields have their expected default values."""
        result = build_gameinfo_dict()
        
        assert result["game_name"] == "Minecraft"
        assert result["game_version"] == "1.20.4"
        assert result["platform"] == "Java Edition"
        assert result["scene_name"] == "flat-overworld"
        assert result["weather"] == "clear"
        assert result["time_of_day"] == "day"
        assert result["character_name"] == "DataPilot"
        assert result["character_class"] == "spectator"
        assert result["operator_id"] == "vendor-001-op-A"
        assert result["total_frames"] == 9000
        assert result["video_duration_sec"] == 300.0
        assert result["route_type"] == 1
        assert result["notes"] == ""


class TestWriteReadRoundtrip:
    """Tests for write_xlsx and read_xlsx functions."""
    
    def test_write_then_read_roundtrip(self):
        """Test that writing and reading an XLSX file produces the same data."""
        # Create test data
        test_data = build_gameinfo_dict(
            game_name="TestGame",
            game_version="2.0",
            platform="TestPlatform",
            scene_name="test-scene",
            weather="rainy",
            time_of_day="night",
            character_name="TestPlayer",
            character_class="warrior",
            operator_id="test-op-001",
            recording_date="2024-06-01",
            total_frames=5000,
            video_duration_sec=180.0,
            route_type=2,
            notes="Test notes",
        )
        
        # Write to temporary file
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as f:
            temp_path = f.name
        
        try:
            write_xlsx(test_data, temp_path)
            
            # Check file size is under 100KB
            file_size = os.path.getsize(temp_path)
            assert file_size < 100 * 1024, \
                f"File size {file_size} exceeds 100KB limit"
            
            # Read back
            result = read_xlsx(temp_path)
            
            # Verify all fields match
            for field in FIELD_NAMES:
                assert field in result, f"Missing field after read: {field}"
                assert result[field] == test_data[field], \
                    f"Field {field} mismatch: {result[field]} != {test_data[field]}"
        
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


class TestNoOpenpyxlFallback:
    """Tests for fallback when openpyxl is not available."""
    
    def test_no_openpyxl_fallback(self):
        """Test that the fallback works when openpyxl is not available."""
        # Mock the import to fail
        import sys
        original_modules = sys.modules.copy()
        
        # Create a fake import error for openpyxl
        fake_openpyxl = MagicMock()
        fake_openpyxl.__side_effect = ImportError("No module named 'openpyxl'")
        
        try:
            # Remove openpyxl from sys.modules if present
            if 'openpyxl' in sys.modules:
                del sys.modules['openpyxl']
            
            # Patch the import to raise ImportError
            with patch.dict(sys.modules, {'openpyxl': None}):
                # Reload the module to pick up the patch
                import importlib
                import bin.generate_gameinfo_xlsx as module
                importlib.reload(module)
                
                # Test that write_xlsx still works
                test_data = build_gameinfo_dict()
                
                with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as f:
                    temp_path = f.name
                
                try:
                    module.write_xlsx(test_data, temp_path)
                    
                    # Verify file was created
                    assert os.path.exists(temp_path), "Fallback file was not created"
                    
                    # Verify we can read it back
                    result = module.read_xlsx(temp_path)
                    assert len(result) == 14, "Fallback did not write all fields"
                
                finally:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
        
        finally:
            # Restore original modules
            sys.modules.clear()
            sys.modules.update(original_modules)


class TestMain:
    """Tests for main CLI function."""
    
    def test_main_writes_file(self):
        """Test that main() writes a file when given valid arguments."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "gameinfo.xlsx")
            
            result = main([
                "--output", output_path,
                "--scene-name", "flat-overworld",
                "--route-type", "1",
                "--total-frames", "9000",
            ])
            
            assert result == 0, f"main() returned non-zero: {result}"
            assert os.path.exists(output_path), "Output file was not created"
            
            # Verify file is valid XLSX
            file_size = os.path.getsize(output_path)
            assert file_size > 0, "Output file is empty"
            assert file_size < 100 * 1024, f"File too large: {file_size}"
    
    def test_main_with_all_args(self):
        """Test main() with all arguments specified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "gameinfo.xlsx")
            
            result = main([
                "--output", output_path,
                "--game-name", "Terraria",
                "--game-version", "1.4.4",
                "--platform", "Steam",
                "--scene-name", "world",
                "--weather", "storm",
                "--time-of-day", "dusk",
                "--character-name", "Hero",
                "--character-class", "mage",
                "--operator-id", "op-999",
                "--recording-date", "2023-12-25",
                "--total-frames", "10000",
                "--video-duration-sec", "400.0",
                "--route-type", "3",
                "--notes", "Full test",
            ])
            
            assert result == 0
            
            # Verify content
            data = read_xlsx(output_path)
            assert data["game_name"] == "Terraria"
            assert data["game_version"] == "1.4.4"
            assert data["platform"] == "Steam"
            assert data["scene_name"] == "world"
            assert data["weather"] == "storm"
            assert data["time_of_day"] == "dusk"
            assert data["character_name"] == "Hero"
            assert data["character_class"] == "mage"
            assert data["operator_id"] == "op-999"
            assert data["recording_date"] == "2023-12-25"
            assert data["total_frames"] == 10000
            assert data["video_duration_sec"] == 400.0
            assert data["route_type"] == 3
            assert data["notes"] == "Full test"


class TestRouteTypeValidation:
    """Tests for route_type validation."""
    
    def test_route_type_validation_valid(self):
        """Test that route_type 1, 2, 3 are valid."""
        assert validate_route_type(1) is True
        assert validate_route_type(2) is True
        assert validate_route_type(3) is True
    
    def test_route_type_validation_invalid(self):
        """Test that route_type values other than 1, 2, 3 are invalid."""
        assert validate_route_type(0) is False
        assert validate_route_type(4) is False
        assert validate_route_type(-1) is False
        assert validate_route_type(100) is False
    
    def test_main_rejects_invalid_route_type(self):
        """Test that main() rejects invalid route_type values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "gameinfo.xlsx")
            
            result = main([
                "--output", output_path,
                "--route-type", "5",
            ])
            
            assert result == 1, "main() should return 1 for invalid route_type"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

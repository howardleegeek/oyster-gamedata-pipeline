#!/usr/bin/env python3
"""
Tests for route_planner.py

Run with: python3 -m pytest tests/test_route_planner.py -v
"""

import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import bin.route_planner as rp


class TestRoutePlanner:
    """Test suite for route planner functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.test_scene = "Overworld_NewWorld"
        self.test_batch_id = "test-batch-001"
        
    def test_get_scene_quota_default(self):
        """Test that unknown scenes get default quota."""
        quota = rp.get_scene_quota("UnknownScene")
        assert quota == rp.DEFAULT_QUOTA
        
    def test_get_scene_quota_specific(self):
        """Test that known scenes get specific quota."""
        quota = rp.get_scene_quota("Overworld_NewWorld")
        assert quota == {"1": 10, "2": 10, "3": 10, "4": 5}
        
    def test_count_sessions_empty(self):
        """Test counting sessions in empty manifest."""
        manifest = {"sessions": []}
        counts = rp.count_sessions_by_route_type(manifest)
        assert counts == {"1": 0, "2": 0, "3": 0, "4": 0}
        
    def test_count_sessions_with_data(self):
        """Test counting sessions with actual data."""
        manifest = {
            "sessions": [
                {"route_type": 1},
                {"route_type": 1},
                {"route_type": 2},
                {"route_type": 3},
                {"route_type": 3},
                {"route_type": 3},
            ]
        }
        counts = rp.count_sessions_by_route_type(manifest)
        assert counts == {"1": 2, "2": 1, "3": 3, "4": 0}
        
    def test_pick_next_route_type_empty(self):
        """Test picking route type when no sessions exist."""
        manifest = {"sessions": []}
        route_type, reason, counts = rp.pick_next_route_type(self.test_scene, manifest)
        # Should pick the one with highest deficit (all have same deficit, so first one)
        assert route_type in [1, 2, 3, 4]
        assert "scene quota" in reason
        
    def test_pick_next_route_type_highest_deficit(self):
        """Test picking route type with highest deficit is prioritized."""
        # Type 4 has quota 5, 0 sessions -> deficit 5
        # Type 1,2,3 have quota 10, 2 sessions each -> deficit 8
        # So type 1, 2, or 3 should be picked (highest deficit)
        manifest = {
            "sessions": [
                {"route_type": 1}, {"route_type": 1},
                {"route_type": 2}, {"route_type": 2},
                {"route_type": 3}, {"route_type": 3},
            ]
        }
        route_type, reason, counts = rp.pick_next_route_type(self.test_scene, manifest)
        # Type 1, 2, 3 all have deficit of 8, type 4 has deficit of 5
        # Should pick one of 1, 2, or 3 (highest deficit)
        assert route_type in [1, 2, 3]
        assert "scene quota" in reason
        
    def test_pick_next_route_type_needs_type3(self):
        """Test that route type with highest deficit is picked."""
        manifest = {
            "sessions": [
                {"route_type": 1}, {"route_type": 1}, {"route_type": 1},
                {"route_type": 1}, {"route_type": 1}, {"route_type": 1},
                {"route_type": 1}, {"route_type": 1}, {"route_type": 1},
                {"route_type": 1},  # Type 1: 10 (quota met)
                {"route_type": 2}, {"route_type": 2}, {"route_type": 2},
                {"route_type": 2}, {"route_type": 2}, {"route_type": 2},
                {"route_type": 2}, {"route_type": 2},  # Type 2: 8 (needs 2)
                {"route_type": 3}, {"route_type": 3}, {"route_type": 3},
                {"route_type": 3},  # Type 3: 4 (needs 6 - highest deficit)
            ]
        }
        route_type, reason, counts = rp.pick_next_route_type(self.test_scene, manifest)
        assert route_type == 3
        assert "has only 4 of 10 needed" in reason
        
    def test_pick_next_route_type_type4_needed(self):
        """Test that type 4 is picked when it has highest deficit."""
        # Create a scenario where type 4 has highest deficit
        manifest = {
            "sessions": [
                {"route_type": 1}, {"route_type": 1}, {"route_type": 1},
                {"route_type": 1}, {"route_type": 1}, {"route_type": 1},
                {"route_type": 1}, {"route_type": 1}, {"route_type": 1},
                {"route_type": 1},  # Type 1: 10 (quota met)
                {"route_type": 2}, {"route_type": 2}, {"route_type": 2},
                {"route_type": 2}, {"route_type": 2}, {"route_type": 2},
                {"route_type": 2}, {"route_type": 2}, {"route_type": 2},
                {"route_type": 2},  # Type 2: 10 (quota met)
                {"route_type": 3}, {"route_type": 3}, {"route_type": 3},
                {"route_type": 3}, {"route_type": 3}, {"route_type": 3},
                {"route_type": 3}, {"route_type": 3}, {"route_type": 3},
                {"route_type": 3},  # Type 3: 10 (quota met)
                # Type 4: 0 (needs 5 - highest deficit)
            ]
        }
        route_type, reason, counts = rp.pick_next_route_type(self.test_scene, manifest)
        assert route_type == 4
        assert "has only 0 of 5 needed" in reason
        
    def test_manifest_loads_existing(self):
        """Test that existing manifest is loaded correctly."""
        manifest = rp.load_batch_manifest("2026-05-batch-1")
        assert "batch_id" in manifest
        assert "sessions" in manifest
        assert "quota" in manifest
        
    def test_manifest_persistence(self):
        """Test that manifest is saved and loaded correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Override BATCH_DIR for this test
            original_batch_dir = rp.BATCH_DIR
            rp.BATCH_DIR = Path(tmpdir)
            
            try:
                # Create and save manifest
                manifest = rp.load_batch_manifest("persist-test")
                manifest["scene"] = "TestScene"
                manifest["operator_id"] = "test-operator"
                manifest["sessions"].append({
                    "id": "test-session-1",
                    "route_type": 1,
                    "grade": "PASS",
                    "duration_s": 300,
                    "audit_score": "100/105",
                    "uploaded": False
                })
                rp.save_batch_manifest(manifest)
                
                # Load again
                loaded = rp.load_batch_manifest("persist-test")
                assert loaded["scene"] == "TestScene"
                assert loaded["operator_id"] == "test-operator"
                assert len(loaded["sessions"]) == 1
                assert loaded["sessions"][0]["id"] == "test-session-1"
            finally:
                rp.BATCH_DIR = original_batch_dir


class TestLauncherIntegration:
    """Test suite for launcher integration."""
    
    def test_route_type_info_exists(self):
        """Test that all route types have info."""
        # Import from launcher_integration
        sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))
        import launcher_integration as li
        
        for route_type in [1, 2, 3, 4]:
            assert route_type in li.ROUTE_TYPE_INFO
            info = li.ROUTE_TYPE_INFO[route_type]
            assert "name" in info
            assert "description" in info
            assert "instructions" in info
            
    def test_generate_banner(self):
        """Test banner generation."""
        sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))
        import launcher_integration as li
        
        banner = li.generate_banner(3)
        assert "ROUTE TYPE 3" in banner
        assert "Special" in banner
        
    def test_generate_overlay_text(self):
        """Test overlay text generation."""
        sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))
        import launcher_integration as li
        
        # Special route types should mention manual
        overlay = li.generate_overlay_text(3)
        assert "ROUTE TYPE 3" in overlay
        assert "see manual" in overlay
        
        # Normal route types should not mention manual
        overlay = li.generate_overlay_text(1)
        assert "ROUTE TYPE 1" in overlay
        assert "see manual" not in overlay


class TestBatchManifest:
    """Test suite for batch manifest structure."""
    
    def test_manifest_structure(self):
        """Test that manifest has required fields."""
        manifest_path = Path(__file__).parent.parent / "batch_manifest.json"
        
        if manifest_path.exists():
            with open(manifest_path) as f:
                manifest = json.load(f)
            
            # Check required fields
            assert "batch_id" in manifest
            assert "scene" in manifest
            assert "operator_id" in manifest
            assert "quota" in manifest
            assert "sessions" in manifest
            
            # Check quota structure
            for rt in ["1", "2", "3", "4"]:
                assert rt in manifest["quota"]
                assert isinstance(manifest["quota"][rt], int)
                
            # Check session structure
            for session in manifest["sessions"]:
                assert "id" in session
                assert "route_type" in session
                assert "grade" in session
                assert "duration_s" in session
                assert "audit_score" in session
                assert "uploaded" in session


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
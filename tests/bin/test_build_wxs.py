#!/usr/bin/env python3
"""Tests for bin/build_wxs.py — WiX .wxs file generator."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Generator

import pytest

# Ensure bin/ is on sys.path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "bin"))

from build_wxs import build_components_xml, component_guid, component_id, file_id


class TestComponentGuid:
    """Tests for component_guid()."""

    def test_deterministic_output(self):
        """Same input should produce same UUID."""
        result1 = component_guid("path/to/file.txt")
        result2 = component_guid("path/to/file.txt")
        assert result1 == result2

    def test_different_paths_different_guids(self):
        """Different paths should produce different UUIDs."""
        result1 = component_guid("path/to/file1.txt")
        result2 = component_guid("path/to/file2.txt")
        assert result1 != result2

    def test_valid_uuid_format(self):
        """Output should be valid uppercase UUID."""
        import uuid

        result = component_guid("some/path/file.exe")
        # Should not raise
        uuid_obj = uuid.UUID(result)
        assert result == result.upper()
        assert len(result) == 36  # standard UUID length

    def test_namespace_oid_used(self):
        """Should use NAMESPACE_OID for deterministic namespacing."""
        import uuid

        key = "oyster-recorder:test/path/file.jar"
        expected = str(uuid.uuid5(uuid.NAMESPACE_OID, key)).upper()
        result = component_guid("test/path/file.jar")
        assert result == expected


class TestComponentId:
    """Tests for component_id()."""

    def test_simple_filename(self):
        """Basic filename should work."""
        result = component_id("file.exe")
        assert result == "Cmp_file_exe"

    def test_path_with_separators(self):
        """Path with separators should be flattened."""
        result = component_id("mods/some-mod.jar")
        assert result == "Cmp_mods_some_mod_jar"

    def test_filename_with_dots(self):
        """Multiple dots should be replaced with underscores."""
        result = component_id("my.mod.file.jar")
        assert result == "Cmp_my_mod_file_jar"

    def test_starts_with_alphanumeric(self):
        """Should prefix with underscore if starts with digit."""
        result = component_id("123mod.jar")
        assert result.startswith("Cmp_")

    def test_underscore_prefix_preserved(self):
        """Existing underscore prefix should be preserved."""
        result = component_id("_hidden_file.exe")
        assert result == "Cmp__hidden_file_exe"

    def test_special_chars_converted(self):
        """Special characters should be converted to underscores."""
        result = component_id("file@#$%.jar")
        # All special chars should be underscores
        assert "@" not in result
        assert "#" not in result
        assert "$" not in result
        assert "%" not in result


class TestFileId:
    """Tests for file_id()."""

    def test_simple_filename(self):
        """Basic filename should work."""
        result = file_id("file.exe")
        assert result == "File_file_exe"

    def test_path_with_separators(self):
        """Path with separators should be flattened."""
        result = file_id("dir/subdir/file.jar")
        assert result == "File_dir_subdir_file_jar"

    def test_starts_with_digit(self):
        """Should prefix with underscore if starts with digit."""
        result = file_id("123.txt")
        assert result.startswith("File_")

    def test_different_from_component_id(self):
        """file_id and component_id should have different prefixes."""
        comp_result = component_id("test.exe")
        file_result = file_id("test.exe")
        assert comp_result.startswith("Cmp_")
        assert file_result.startswith("File_")


class TestBuildComponentsXml:
    """Tests for build_components_xml()."""

    def test_empty_inputs(self):
        """Empty recorder_exe and mods_dir should return empty string."""
        xml, mod_count, exe_count = build_components_xml("", "")
        assert xml == ""
        assert mod_count == 0
        assert exe_count == 0

    def test_nonexistent_recorder_exe(self):
        """Nonexistent recorder exe should be skipped."""
        xml, mod_count, exe_count = build_components_xml("/nonexistent/fake.exe", "")
        assert xml == ""
        assert exe_count == 0

    def test_nonexistent_mods_dir(self):
        """Nonexistent mods directory should return empty."""
        xml, mod_count, exe_count = build_components_xml("", "/nonexistent/mods")
        assert xml == ""
        assert mod_count == 0

    @pytest.fixture
    def temp_dirs(self) -> Generator[tuple[tempfile.TemporaryDirectory, tempfile.TemporaryDirectory], None, None]:
        """Create temp directories with test files."""
        with tempfile.TemporaryDirectory() as tmp_recorder:
            with tempfile.TemporaryDirectory() as tmp_mods:
                # Create a fake exe
                exe_path = os.path.join(tmp_recorder, "OysterRecorder.exe")
                Path(exe_path).write_text("fake exe content")
                
                # Create some mod jars
                Path(os.path.join(tmp_mods, "mod1.jar")).write_text("mod1")
                Path(os.path.join(tmp_mods, "mod2.jar")).write_text("mod2")
                Path(os.path.join(tmp_mods, "readme.txt")).write_text("not a jar")
                
                yield (tmp_recorder, tmp_mods)

    def test_recorder_exe_only(self, temp_dirs):
        """Single recorder exe should produce one Component."""
        tmp_recorder, tmp_mods = temp_dirs
        exe_path = os.path.join(tmp_recorder, "OysterRecorder.exe")
        
        xml, mod_count, exe_count = build_components_xml(exe_path, "")
        
        assert exe_count == 1
        assert mod_count == 0
        assert "Component" in xml
        assert "File" in xml
        assert "OysterRecorder.exe" in xml

    def test_mods_dir_only(self, temp_dirs):
        """Mods directory should produce Component for each .jar."""
        tmp_recorder, tmp_mods = temp_dirs
        
        xml, mod_count, exe_count = build_components_xml("", tmp_mods)
        
        assert mod_count == 2  # only .jar files
        assert exe_count == 0
        assert "Component" in xml
        assert "mod1.jar" in xml
        assert "mod2.jar" in xml

    def test_both_exe_and_mods(self, temp_dirs):
        """Both exe and mods should produce combined Components."""
        tmp_recorder, tmp_mods = temp_dirs
        exe_path = os.path.join(tmp_recorder, "OysterRecorder.exe")
        
        xml, mod_count, exe_count = build_components_xml(exe_path, tmp_mods)
        
        assert exe_count == 1
        assert mod_count == 2
        assert "OysterRecorder.exe" in xml
        assert "mod1.jar" in xml
        assert "mod2.jar" in xml

    def test_mods_sorted(self, temp_dirs):
        """Mod jars should be sorted alphabetically."""
        tmp_recorder, tmp_mods = temp_dirs
        
        # Add mods in reverse alphabetical order
        Path(os.path.join(tmp_mods, "zzz-mod.jar")).write_text("zzz")
        Path(os.path.join(tmp_mods, "aaa-mod.jar")).write_text("aaa")
        
        xml, mod_count, exe_count = build_components_xml("", tmp_mods)
        
        # Should have 4 mods now (2 + 2)
        assert mod_count == 4
        # Check ordering - aaa should come before zzz
        aaa_pos = xml.find("aaa-mod.jar")
        zzz_pos = xml.find("zzz-mod.jar")
        assert aaa_pos < zzz_pos

    def test_case_insensitive_jar_extension(self, temp_dirs):
        """Should handle .JAR uppercase extension."""
        tmp_recorder, tmp_mods = temp_dirs
        
        Path(os.path.join(tmp_mods, "UPPER.JAR")).write_text("upper")
        
        xml, mod_count, exe_count = build_components_xml("", tmp_mods)
        
        # Should include .JAR (case insensitive)
        assert mod_count == 3  # 2 lowercase + 1 uppercase

    def test_xml_contains_guid(self, temp_dirs):
        """Component should contain a Guid attribute."""
        tmp_recorder, tmp_mods = temp_dirs
        exe_path = os.path.join(tmp_recorder, "OysterRecorder.exe")
        
        xml, _, _ = build_components_xml(exe_path, "")
        
        assert 'Guid="' in xml

    def test_xml_contains_keypath(self, temp_dirs):
        """File should have KeyPath="yes" attribute."""
        tmp_recorder, tmp_mods = temp_dirs
        exe_path = os.path.join(tmp_recorder, "OysterRecorder.exe")
        
        xml, _, _ = build_components_xml(exe_path, "")
        
        assert 'KeyPath="yes"' in xml


class TestIntegration:
    """Integration tests for the build_wxs module."""

    def test_module_imports(self):
        """Module should import without errors."""
        import build_wxs
        assert hasattr(build_wxs, "component_guid")
        assert hasattr(build_wxs, "component_id")
        assert hasattr(build_wxs, "file_id")
        assert hasattr(build_wxs, "build_components_xml")

    def test_constants_defined(self):
        """Required constants should be defined."""
        import build_wxs
        assert hasattr(build_wxs, "UPGRADE_CODE")
        assert hasattr(build_wxs, "WIX_NS")
        # Validate UUID format for upgrade code
        import uuid
        uuid.UUID(build_wxs.UPGRADE_CODE)

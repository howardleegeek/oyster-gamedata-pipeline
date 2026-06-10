"""Tests for bin/build_wxs.py."""

import os
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "bin", "build_wxs.py")
TEMPLATE = os.path.join(
    os.path.dirname(__file__), "..", "installer", "oyster-recorder.wxs.template"
)


def _make_fake_dist(tmp_path):
    """Create a fake dist/ with an exe and 2 mod jars."""
    dist = tmp_path / "dist"
    mods = dist / "mods"
    mods.mkdir(parents=True, exist_ok=True)

    exe = dist / "OysterRecorder.exe"
    exe.write_text("fake exe binary", encoding="utf-8")

    jar1 = mods / "fabric-api-0.91.0.jar"
    jar1.write_text("fake jar 1", encoding="utf-8")

    jar2 = mods / "sodium-mc1.20.4.jar"
    jar2.write_text("fake jar 2", encoding="utf-8")

    return dist, mods, exe


def _run_build_wxs(tmp_path, extra_args=None):
    """Run build_wxs.py with standard args, return CompletedProcess."""
    dist, mods, exe = _make_fake_dist(tmp_path)
    output = tmp_path / "output.wxs"

    cmd = [
        sys.executable,
        SCRIPT,
        "--version",
        "0.4.0",
        "--recorder-exe",
        str(exe),
        "--mods-dir",
        str(mods),
        "--template",
        TEMPLATE,
        "--output",
        str(output),
    ]
    if extra_args:
        cmd.extend(extra_args)

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result, output


# ---------------------------------------------------------------------------
# Test: output .wxs exists
# ---------------------------------------------------------------------------
def test_output_wxs_exists(tmp_path):
    result, output = _run_build_wxs(tmp_path)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert output.exists()
    assert output.stat().st_size > 0


# ---------------------------------------------------------------------------
# Test: parse output XML, assert <File> entries match input files
# ---------------------------------------------------------------------------
def test_xml_file_entries(tmp_path):
    result, output = _run_build_wxs(tmp_path)
    assert result.returncode == 0

    tree = ET.parse(str(output))
    root = tree.getroot()

    # WiX 4 namespace
    ns = {"w": "http://wixtoolset.org/schemas/v4/wxs"}

    files = root.findall(".//w:File", ns)
    # 1 exe + 2 jars = 3 files
    assert len(files) == 3

    # Check that each file has a Source attribute
    sources = {f.get("Source") for f in files}
    assert len(sources) == 3

    # Verify the exe is present
    assert any("OysterRecorder.exe" in s for s in sources)
    # Verify both jars are present
    assert any("fabric-api-0.91.0.jar" in s for s in sources)
    assert any("sodium-mc1.20.4.jar" in s for s in sources)


# ---------------------------------------------------------------------------
# Test: deterministic GUIDs — same inputs produce same GUIDs
# ---------------------------------------------------------------------------
def test_deterministic_guids(tmp_path):
    result1, output1 = _run_build_wxs(tmp_path)
    assert result1.returncode == 0
    content1 = output1.read_text(encoding="utf-8")

    # Run again
    result2, output2 = _run_build_wxs(tmp_path)
    assert result2.returncode == 0
    content2 = output2.read_text(encoding="utf-8")

    assert content1 == content2, "GUIDs are not deterministic across runs"


# ---------------------------------------------------------------------------
# Test: UPGRADE_CODE is the fixed value
# ---------------------------------------------------------------------------
def test_upgrade_code(tmp_path):
    result, output = _run_build_wxs(tmp_path)
    assert result.returncode == 0

    content = output.read_text(encoding="utf-8")
    assert 'UpgradeCode="7E5C1A20-3D8F-4F4E-AC91-F9B6E1C8D2B4"' in content


# ---------------------------------------------------------------------------
# Test: Version is substituted correctly
# ---------------------------------------------------------------------------
def test_version_substitution(tmp_path):
    result, output = _run_build_wxs(tmp_path)
    assert result.returncode == 0

    content = output.read_text(encoding="utf-8")
    assert 'Version="0.4.0"' in content


# ---------------------------------------------------------------------------
# Test: missing --recorder-exe → exit 1 with stderr
# ---------------------------------------------------------------------------
def test_missing_recorder_exe(tmp_path):
    dist, mods, exe = _make_fake_dist(tmp_path)
    output = tmp_path / "output.wxs"

    cmd = [
        sys.executable,
        SCRIPT,
        "--version",
        "0.4.0",
        "--recorder-exe",
        str(tmp_path / "nonexistent.exe"),
        "--mods-dir",
        str(mods),
        "--template",
        TEMPLATE,
        "--output",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 1
    assert "Error" in result.stderr or "error" in result.stderr.lower()


# ---------------------------------------------------------------------------
# Test: template missing → exit 1 with stderr
# ---------------------------------------------------------------------------
def test_missing_template(tmp_path):
    dist, mods, exe = _make_fake_dist(tmp_path)
    output = tmp_path / "output.wxs"

    cmd = [
        sys.executable,
        SCRIPT,
        "--version",
        "0.4.0",
        "--recorder-exe",
        str(exe),
        "--mods-dir",
        str(mods),
        "--template",
        str(tmp_path / "nonexistent.template"),
        "--output",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 1
    assert "Error" in result.stderr or "error" in result.stderr.lower()


# ---------------------------------------------------------------------------
# Test: summary line printed to stdout
# ---------------------------------------------------------------------------
def test_summary_line(tmp_path):
    result, output = _run_build_wxs(tmp_path)
    assert result.returncode == 0
    assert "Generated wxs with 2 mod jars + 1 exe" in result.stdout
    assert "ProductVersion=0.4.0" in result.stdout


# ---------------------------------------------------------------------------
# Test: XML is well-formed (ElementTree can parse it)
# ---------------------------------------------------------------------------
def test_xml_wellformed(tmp_path):
    result, output = _run_build_wxs(tmp_path)
    assert result.returncode == 0
    # If this doesn't raise, XML is well-formed
    tree = ET.parse(str(output))
    root = tree.getroot()
    assert root.tag == "{http://wixtoolset.org/schemas/v4/wxs}Wix"


# ---------------------------------------------------------------------------
# Test: Component GUIDs use uuid5 with NAMESPACE_OID
# ---------------------------------------------------------------------------
def test_component_guid_algorithm(tmp_path):
    """Verify that the GUID for a known path matches uuid5(NAMESPACE_OID, ...)."""
    result, output = _run_build_wxs(tmp_path)
    assert result.returncode == 0

    content = output.read_text(encoding="utf-8")

    # The exe relative path is "OysterRecorder.exe"
    expected_guid = str(
        uuid.uuid5(uuid.NAMESPACE_OID, "oyster-recorder:OysterRecorder.exe")
    ).upper()
    assert expected_guid in content

    # A mod jar relative path is "mods/fabric-api-0.91.0.jar"
    expected_guid_jar = str(
        uuid.uuid5(uuid.NAMESPACE_OID, "oyster-recorder:mods/fabric-api-0.91.0.jar")
    ).upper()
    assert expected_guid_jar in content


# ---------------------------------------------------------------------------
# Test: AutoStartReg component is present
# ---------------------------------------------------------------------------
def test_autostart_reg_present(tmp_path):
    result, output = _run_build_wxs(tmp_path)
    assert result.returncode == 0

    content = output.read_text(encoding="utf-8")
    assert 'Id="AutoStartReg"' in content
    assert "OysterRecorder.exe --tray" in content

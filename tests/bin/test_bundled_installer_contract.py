from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ISS = REPO_ROOT / "bin" / "build_bundled_installer" / "installer.iss"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-recorder-installer.yml"


def test_bundled_installer_shortcuts_point_to_oysterplay_only() -> None:
    text = ISS.read_text(encoding="utf-8")

    assert '#define AppExeName     "OysterPlay.exe"' in text
    assert 'Filename: "{app}\\\\{#AppExeName}"' in text
    assert "launch_mc.bat" not in text
    assert "launch_mc_fabric.py" not in text


def test_bundled_installer_installs_only_matching_minecraft_mod() -> None:
    text = ISS.read_text(encoding="utf-8")

    assert '#define BundledMcVersion "1.21.4"' in text
    assert 'Excludes: "\\\\mods\\\\*"' in text
    assert "oyster-recorder-mod-*-mc{#BundledMcVersion}.jar" in text
    assert "fabric-api.jar" in text


def test_bundled_workflow_fails_if_matching_mod_is_absent() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "Get-ChildItem 'bundle/mc-instance/mods' -Filter '*-mc1.21.4.jar'" in text
    assert "Could not find a 1.21.4 mod jar to bundle into the recorder onedir" in text
    assert "$bundleCount -lt 9" in text

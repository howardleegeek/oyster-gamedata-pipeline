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


def test_bundled_installer_launches_oysterplay_after_install_by_default() -> None:
    text = ISS.read_text(encoding="utf-8")
    run_section = text.split("[Run]", 1)[1].split("[UninstallDelete]", 1)[0]

    assert 'Filename: "{app}\\\\{#AppExeName}"' in run_section
    assert 'Description: "Launch {#AppName} now"' in run_section
    assert "nowait postinstall skipifsilent" in run_section
    assert "unchecked" not in run_section


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
    assert "Could not find a 1.21.4 mod jar to bundle into the Minecraft instance" in text
    assert "$bundleRecorderMods.Count -ne 1" in text
    assert "Expected exactly one bundled recorder mod" in text
    assert "$extraCount -lt 9" in text
    assert "$bundleCount -lt 9" not in text


def test_local_bundled_build_stages_single_matching_mod() -> None:
    text = (REPO_ROOT / "bin" / "build_bundled_installer" / "build_all.ps1").read_text(
        encoding="utf-8"
    )

    assert '$BundledMcVersion = "1.21.4"' in text
    assert "Stage recorder mod jar for MC" in text
    assert "Expected exactly one bundled recorder mod" in text
    assert "oyster-recorder-mod-*+mc${BundledMcVersion}.jar" in text
    assert "oyster-recorder-mod-*-mc${BundledMcVersion}.jar" in text

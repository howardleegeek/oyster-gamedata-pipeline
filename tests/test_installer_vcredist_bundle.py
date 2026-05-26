"""S130: validate VC++ Redist bundle wiring in installer."""
from pathlib import Path


def _iss() -> str:
    return Path("installer/oyster-recorder.iss").read_text()


def test_iss_references_vc_redist() -> None:
    """ISS must Source vc_redist.x64.exe into {tmp}."""
    iss = _iss()
    assert "vc_redist.x64.exe" in iss
    assert "deleteafterinstall" in iss


def test_iss_has_vcredist_check_function() -> None:
    """ISS must define IsVCRuntimeInstalled Pascal function."""
    iss = _iss()
    assert "IsVCRuntimeInstalled" in iss
    assert "VisualStudio\\14.0\\VC\\Runtimes\\x64" in iss


def test_iss_runs_vcredist_silently() -> None:
    """ISS [Run] must invoke vc_redist with /quiet /norestart."""
    iss = _iss()
    assert "/install /quiet /norestart" in iss
    assert "waituntilterminated" in iss


def test_workflow_downloads_vcredist() -> None:
    """build-recorder-windows.yml must download vc_redist.x64.exe."""
    yml = Path(".github/workflows/build-recorder-windows.yml").read_text()
    assert "vc_redist.x64.exe" in yml
    assert "aka.ms/vs/17/release/vc_redist.x64.exe" in yml

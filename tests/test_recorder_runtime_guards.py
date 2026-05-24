"""Static checks for recorder runtime guardrails used by Windows smoke tests."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RECORDER = REPO_ROOT / "vendor" / "recorder"


def test_windows_runtime_suppresses_blocking_error_dialogs() -> None:
    cargo = (RECORDER / "Cargo.toml").read_text()
    main = (RECORDER / "src" / "main.rs").read_text()

    assert '"Win32_System_Diagnostics_Debug"' in cargo
    assert "SetErrorMode" in main
    assert "SEM_FAILCRITICALERRORS" in main
    assert "SEM_NOGPFAULTERRORBOX" in main
    assert "SEM_NOOPENFILEERRORBOX" in main
    assert main.index("SetErrorMode") < main.index("SetDefaultDllDirectories")


def test_ci_auto_record_uses_detected_game_hwnd_not_foreground_helper() -> None:
    app_state = (RECORDER / "src" / "app_state.rs").read_text()
    tokio_thread = (RECORDER / "src" / "tokio_thread.rs").read_text()
    recorder = (RECORDER / "src" / "record" / "recorder.rs").read_text()

    assert "pub hwnd: HWND" in app_state
    assert "hwnd," in tokio_thread
    assert "game.hwnd" in tokio_thread
    assert "GetForegroundWindow" in tokio_thread
    assert "prefer known game processes" in recorder
    assert "if let Some(running_game) = find_running_game()?" in recorder

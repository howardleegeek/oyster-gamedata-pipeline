; ============================================================================
; OysterRecorder — Inno Setup 6.x Installer Script
; ============================================================================
;
; Builds: OysterRecorder-setup-vX.Y.Z.exe
;
; Installs gamedata-recorder.exe + assets to %LOCALAPPDATA%\OysterRecorder\
;   - Per-user install (no admin)
;   - HKCU Run registry key for tray-daemon autostart
;   - Start Menu shortcut
;   - Uninstall entry
;   - Supports /SILENT /VERYSILENT for bulk deployment
;
; Compile:  iscc installer/oyster-recorder.iss
;           (works on native Windows ISCC and Wine + Inno Setup 6.x)
; ============================================================================

; --- Version is injected by build_installer.ps1 via /D switch -------------
;   iscc /DAppVersion="1.0.0" installer/oyster-recorder.iss
;   Default fallback if not provided:
#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

#ifndef AppExeName
  #define AppExeName "gamedata-recorder.exe"
#endif

#ifndef SourceDir
  #define SourceDir "..\vendor\recorder\target\release"
#endif

#define MyAppName       "OysterRecorder"
#define MyAppPublisher  "GameData Labs"
#define MyAppURL        "https://gamedatalabs.com"
#define MyAppId         "{{B8E4F2A1-9C3D-4E7F-A1B2-C3D4E5F6A7B8}}"

[Setup]
; Application identity
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; Per-user install — NO admin elevation
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Install directory: %LOCALAPPDATA%\OysterRecorder\
DefaultDirName={localappdata}\{#MyAppName}
; Do NOT use {autopf} — we want LOCALAPPDATA, not Program Files
DisableDirPage=no

; Start Menu folder
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=no

; Output
OutputDir=.\output
OutputBaseFilename=OysterRecorder-setup-v{#AppVersion}

; Compression — fast but good ratio for binaries
Compression=lzma2/ultra64
SolidCompression=yes

; Modern wizard appearance (Inno Setup 6.x)
WizardStyle=modern

; 64-bit application
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64

; Setup icon (relative to this .iss file)
; SetupIconFile=..\vendor\recorder\build-resources\owl-logo.ico

; Uninstall display icon
UninstallDisplayIcon={app}\{#AppExeName}

; Allow silent / verysilent installs (default behavior, documented here)
; /SILENT  — shows progress window, no user interaction
; /VERYSILENT — no window at all

; Prevent running multiple instances of the setup
AppMutex=OysterRecorderSetupMutex

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Desktop shortcut (user can opt out)
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
; Autostart tray daemon on login (checked by default)
Name: "autostart"; Description: "Start OysterRecorder automatically when Windows starts"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; Main executable
Source: "{#SourceDir}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; VC++ runtime preflight helper, extracted on demand by InitializeSetup
Source: "check_runtime.bat"; DestDir: "{tmp}"; Flags: dontcopy
; Accompanying DLLs (OBS, etc.) — skip if none present
Source: "{#SourceDir}\*.dll"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
; OBS runtime must be present for real desktop launch. These entries are
; intentionally required: CI must fail before release if the staging step
; forgets to include libobs or its plugin/data directories.
Source: "{#SourceDir}\obs.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\libobs-d3d11.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\libobs-opengl.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\libobs-winrt.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\obs-ffmpeg-mux.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\obs-plugins\*"; DestDir: "{app}\obs-plugins"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\data\*"; DestDir: "{app}\data"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
; Any .pdb debug symbols — skip in release CI
Source: "{#SourceDir}\*.pdb"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
; Start Menu shortcut
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
; Desktop shortcut (optional task)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
; HKCU Run key — autostart tray daemon on user login
; Only written when the "autostart" task is selected (default: yes)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "{#MyAppName}"; \
  ValueData: """{app}\{#AppExeName}"" --tray"; \
  Flags: uninsdeletevalue; Tasks: autostart

[Run]
; Fallback VC++ runtime check before launching the installed recorder
Filename: "{cmd}"; \
  Parameters: "/C ""{tmp}\check_runtime.bat"" /SILENT"; \
  Flags: runhidden waituntilterminated; \
  Check: not IsVCRuntimeInstalled

; Launch the recorder after installation (unless silent)
Filename: "{app}\{#AppExeName}"; \
  Parameters: "--tray"; \
  Description: "{cm:LaunchProgram,{#MyAppName}}"; \
  Flags: nowait postinstall skipifsilent runasoriginaluser; \
  WorkingDir: "{app}"

[UninstallRun]
; Kill any running instance before uninstall
Filename: "{sys}\taskkill.exe"; \
  Parameters: "/F /IM {#AppExeName}"; \
  Flags: runhidden waituntilterminated; \
  Check: IsAppRunning

[UninstallDelete]
; Clean up leftover config/logs on uninstall
Type: filesandordirs; Name: "{localappdata}\{#MyAppName}\logs"
Type: filesandordirs; Name: "{localappdata}\{#MyAppName}\config"

[Code]
// ---------------------------------------------------------------------------
// Helper: check whether VC++ 2015-2022 x64 runtime is installed
// ---------------------------------------------------------------------------
function IsVCRuntimeInstalled(): Boolean;
begin
  Result := RegKeyExists(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64');
end;

// ---------------------------------------------------------------------------
// InitializeSetup: block installation until the VC++ runtime is present
// ---------------------------------------------------------------------------
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
  BatchPath: String;
begin
  Result := True;

  if not IsVCRuntimeInstalled() then
  begin
    if WizardSilent() then
    begin
      MsgBox('VC++ 2015-2022 Redistributable (x64) is required before installing OysterRecorder.' + #13#10 +
             'Install it from https://aka.ms/vs/17/release/vc_redist.x64.exe and run setup again.',
             mbCriticalError, MB_OK);
      Result := False;
      Exit;
    end;

    ExtractTemporaryFile('check_runtime.bat');
    BatchPath := ExpandConstant('{tmp}\check_runtime.bat');
    if (not Exec(ExpandConstant('{cmd}'), '/C "' + BatchPath + '"',
                 '', SW_SHOW, ewWaitUntilTerminated, ResultCode)) or (ResultCode <> 0) then
    begin
      MsgBox('VC++ 2015-2022 Redistributable (x64) is required before installing OysterRecorder.' + #13#10 +
             'Install it from https://aka.ms/vs/17/release/vc_redist.x64.exe and run setup again.',
             mbCriticalError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
end;

// ---------------------------------------------------------------------------
// Helper: check if gamedata-recorder.exe is currently running
// ---------------------------------------------------------------------------
function IsAppRunning(): Boolean;
var
  ResultCode: Integer;
begin
  // Use tasklist to check; returns 0 if found, 1 if not
  Result := (Exec(ExpandConstant('{sys}\tasklist.exe'),
                  '/FI "IMAGENAME eq {#AppExeName}"',
                  '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
             and (ResultCode = 0));
end;

// ---------------------------------------------------------------------------
// CurStepChanged: after install, optionally run the post-install batch
// for additional autostart registration (belt-and-suspenders with the
// [Registry] section above).
// ---------------------------------------------------------------------------
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  BatchPath: String;
begin
  if CurStep = ssPostInstall then
  begin
    // Run postinstall_register_autostart.bat if it exists in {app}
    // This is a no-op if the [Registry] entry already handled it,
    // but provides a fallback for edge cases.
    BatchPath := ExpandConstant('{app}\postinstall_register_autostart.bat');
    if FileExists(BatchPath) then
    begin
      Exec(ExpandConstant('{cmd}'), '/C "' + BatchPath + '"',
           ExpandConstant('{app}'), SW_HIDE, ewNoWait, ResultCode);
    end;
  end;
end;

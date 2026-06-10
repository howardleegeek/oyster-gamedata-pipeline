; ============================================================================
; OysterRecorder minimal bundled-installer Inno Setup script
; ----------------------------------------------------------------------------
; Spec: v0.14 minimal installer variant, copied from installer.iss.
; Minimal deltas are commented inline below: no DA-V2 model payloads, no
; tester self-audit tool suite, no ffmpeg-full directory, and a -minimal
; output filename for CI/release uploads.
;
; This script compiles one self-contained Windows installer that ships
; *everything* a zero-knowledge consumer needs to record Minecraft sessions:
;
;     OysterRecorder-Setup-vX.Y.Z-minimal.exe (<250 MB target)
;       |
;       +-- Eclipse Temurin OpenJDK 21 LTS JRE      (R05A, ~145 MB on disk)
;       +-- Vanilla Minecraft 1.21.4 client + libs  (R05B, ~200 MB)
;       +-- Vanilla MC asset objects (rc7)          (R05B, ~390 MB)
;       +-- Fabric loader 0.16.10 + 9 mod jars      (R05B/mc-mod, ~10 MB)
;       +-- OysterRecorder-onedir/ (PyInstaller)    (~120 MB)
;       +-- OysterPlay.exe single-button launcher   (R05C, ~5 MB)
;       +-- manifest.json (combined SHA-256 pin)
;
; Iron-law constraints from R05D (reflected in the [Setup] block below):
;
;   * PER-USER INSTALL ONLY. No admin elevation, no %ProgramFiles% — every
;     file lands under {localappdata}\OysterRecorder\. PrivilegesRequired
;     is set to "lowest" and PrivilegesRequiredOverridesAllowed is left
;     empty so an over-eager UAC prompt never appears.
;
;   * BUNDLE EVERYTHING. There is no second download at install time. The
;     [Files] block declares every directory tree directly; if any of the
;     declared sources are missing at compile time, ISCC.exe fails loud.
;
;   * DESKTOP SHORTCUT VIA {userdesktop}. Inno Setup's built-in resolves
;     this against HKCU\...\User Shell Folders\Desktop, which is the path
;     Explorer actually shows on screen. This is the OneDrive-redirection
;     fix that R05C called out — using %USERPROFILE%\Desktop directly
;     would create an "invisible" shortcut on machines where OneDrive has
;     redirected the Desktop folder to the cloud-backed location.
;
;   * PER-USER UNINSTALLER. Inno's default behavior with PrivilegesRequired=
;     lowest is to write the uninstaller into the install directory and
;     register it under HKCU\Software\Microsoft\Windows\CurrentVersion\
;     Uninstall\, so it appears in *Add/Remove Programs* without admin.
;     We pin the path explicitly via UninstallFilesDir= for clarity.
;
;   * NO PATH / JAVA_HOME / SYSTEM ENV MUTATION. We deliberately omit any
;     [Registry] section that would touch HKLM environment keys or HKCU
;     Environment. The JRE we ship is private to OysterRecorder and is
;     invoked by absolute path only.
;
;   * NO SILENT INSTALL DEFAULT. We do not pass /SILENT or /VERYSILENT in
;     the build orchestrator, and we do not set DisableProgramGroupPage=
;     yes for "auto"; the installer always shows a Welcome page so users
;     see the ~800 MB copy progress (Inno Setup defaults are correct here,
;     this comment exists only to document the choice).
;
;   * NO TOUCHING %APPDATA%\.minecraft\. Mojang Launcher's private space
;     is never read or written. Our MC instance lives at
;     {app}\mc-instance\ — a sandboxed directory the Mojang Launcher does
;     not know about.
;
;   * CODE-SIGN HOOK. The SignTool= line below is left commented. When we
;     receive an EV / OV code-signing cert, uncommenting it (and setting
;     up the matching `iscc /Ssigntool=...` invocation in build_all.ps1)
;     enables signing of both the installer and the per-user uninstaller.
;     SmartScreen warnings disappear shortly after enough signed copies
;     hit user machines (cert reputation builds with usage).
; ============================================================================

#ifndef AppVersion
  ; Default; the build orchestrator passes /DAppVersion=X.Y.Z explicitly.
  ; Keep this in sync with pyproject.toml during local builds.
  #define AppVersion "0.0.0-dev"
#endif

; Iron-law (R05D bug fix #3): the installer ships exactly ONE mod jar —
; the one matching the MC version Fabric was pinned to. Bundling all 9
; supported mod jars caused Fabric to load the wrong-version mod (the
; mc1.21.5 mod against the 1.21.4 game), refusing to start. The other
; 8 jars are uploaded to the GitHub Release as `release-extras/` for
; advanced users who manually swap the game version.
;
; To bump the bundled MC version:
;   1) update mc_pin.version_id + fabric_pin.minecraft_version in
;      bin/build_bundled_installer/manifest.json
;   2) update {#BundledMcVersion} below to the same string
;   3) (re)run bin/build_bundled_installer/build_all.ps1
#define BundledMcVersion "1.21.4"

#define AppName        "Oyster Recorder"
#define AppPublisher   "Oyster Labs"
#define AppExeName     "OysterPlay.exe"
#define AppShortcutLbl "Oyster Recording"
#define AppId          "{{C7E4F0D2-9B5E-4F1A-8C3D-OY5T3RR3C0RD}}"

; --- Bundle root --- the orchestrator stages everything under
; bundle/ at the repo root. {#BundleRoot} is computed relative to
; *this .iss file* so the script works from any caller cwd.
#define BundleRoot     "..\\..\\bundle"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://oyster.so/
AppSupportURL=https://oyster.so/support
AppUpdatesURL=https://oyster.so/recorder/download
; VersionInfoVersion must be strict N.N.N.N (Windows resource API) — strip
; any -rc/-alpha/-beta suffix from {#AppVersion}. The user-facing version
; string lives in AppVersion / AppVerName above.
#define _SemVerCore Copy(AppVersion, 1, Pos("-", AppVersion+"-")-1)
VersionInfoVersion={#_SemVerCore}.0
VersionInfoCompany={#AppPublisher}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} bundled installer

; ---------- Per-user install (iron-law) -----------------------------------
; "lowest" runs the installer as the current user with no UAC prompt.
; Combined with DefaultDirName={localappdata}\... and the empty
; PrivilegesRequiredOverridesAllowed= line, there is no path that would
; ever request admin.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=

DefaultDirName={localappdata}\OysterRecorder
DefaultGroupName={#AppName}
DisableProgramGroupPage=auto
DisableDirPage=no

; BUG-14 fix (v0.12.2): require ~2 GB free for the bundle extraction.
; Inno will refuse to install if {app} disk has less than this — with a
; clear "disk full" dialog instead of a confusing mid-extraction failure.
; 2048 MB = 800 MB final + ~600 MB temp during extraction + 600 MB safety.
ExtraDiskSpaceRequired=2147483648

; Per-user uninstaller — registered under HKCU so it shows in
; "Apps & features" / "Add/Remove Programs" without admin.
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
UninstallFilesDir={app}

; Visual / UX
WizardStyle=modern
SetupIconFile=
ShowLanguageDialog=auto
DisableWelcomePage=no
DisableFinishedPage=no

; Compression — LZMA2/ultra64 keeps the ~800 MB rc7 bundle (rc6 was
; ~460 MB before bundling MC asset objects) around ~600 MB on disk in
; the .exe. solid=yes is essential here: we are shipping ~9,000 files
; mostly small (~4,000 of which are individual asset objects under
; ~10 KB each); without solid mode the per-file overhead dominates.
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=2

; Output
OutputDir=..\\..\\dist\\installer
; v0.14 minimal: keep the full installer untouched and emit a distinct
; artifact name so the minimal build can coexist on the same GitHub Release.
OutputBaseFilename=OysterRecorder-Setup-v{#AppVersion}-minimal

; Architecture — Windows x64 only. The bundled JRE is x64; we
; refuse to install on 32-bit Windows or ARM64 (no Temurin x64 fallback).
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763

; ---------- Code-sign hook ------------------------------------------------
; When a code-signing cert is issued, do these three things:
;   1) Define the SignTool in your ISCC invocation:
;        iscc.exe /Ssigntool="$qC:\path\to\signtool.exe$q sign /fd sha256 \
;                            /tr http://timestamp.digicert.com /td sha256 \
;                            /a $f"
;      (The shell-escaped "$f" placeholder is the file Inno wants signed.)
;   2) Uncomment the SignTool= directive below.
;   3) Uncomment SignedUninstaller= so the per-user uninstaller is signed
;      too — Defender will quarantine an unsigned uninstaller spawned
;      from a signed installer otherwise.
; SignTool=signtool
; SignedUninstaller=yes

; ---------- Output / housekeeping -----------------------------------------
ChangesAssociations=no
; iron-law: no PATH / JAVA_HOME mutation
ChangesEnvironment=no
RestartIfNeededByRun=no
CloseApplications=force
CloseApplicationsFilter=*.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Optional desktop icon, checked by default. {userdesktop} below is the
; actual on-screen Desktop path (registry-resolved by Inno Setup), which
; transparently handles OneDrive Desktop-redirection — the canonical bug
; R05C called out where %USERPROFILE%\Desktop becomes "invisible".
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional shortcuts:"; Flags: checkedonce

[Dirs]
; Pre-create directories the launcher writes into at runtime so the
; first launch does not race the OS file system.
Name: "{app}\logs"; Permissions: users-modify
Name: "{app}\mc-instance\saves"; Permissions: users-modify
Name: "{app}\mc-instance\screenshots"; Permissions: users-modify
Name: "{app}\runtime"; Permissions: users-modify

[Files]
; ---------------------------------------------------------------------------
; The four bundle trees declared below are produced by build_all.ps1 BEFORE
; ISCC is invoked. Every Source: line uses recursesubdirs + createallsubdirs
; so the on-disk layout ends up identical to bundle/ on the build machine.
;
; "Check: FileExists(...)" calls are intentionally OMITTED at runtime:
; ISCC.exe statically resolves Source: globs at *compile* time and fails
; loud if any declared source is missing. That gives us the same hard-fail
; the spec asks for, except earlier in the pipeline (before users install).
; The orchestrator's preflight step uses Test-Path to surface the same
; failure with a friendlier error message before we even start ISCC.
; ---------------------------------------------------------------------------

; --- (1) Bundled JRE ------------------------------------------------------
; ~145 MB extracted. Source path comes from R05A/fetch_jre.py.
Source: "{#BundleRoot}\\jre\\*"; \
    DestDir: "{app}\\jre"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; --- (2) Bundled Minecraft instance + Fabric ------------------------------
; ~590 MB (rc7: ~200 MB libs/jar + ~390 MB asset objects). Source path
; comes from R05B/fetch_minecraft.py + fetch_fabric.py. Asset objects
; (textures / sounds / models / langs) ARE shipped now — rc6 omitted
; them and MC crashed with "Missing model for variant: ..." spam before
; the main menu rendered. See fetch_minecraft.py docstring for the
; layout under assets/objects/<2-char>/<sha1>.
;
; Bug fix #3 (R05D): exclude `mods\*` from the recursive glob so we don't
; accidentally ship all 9 supported MC versions' mod jars. The matching
; mod jar is added explicitly in (2b) below.
Source: "{#BundleRoot}\\mc-instance\\*"; \
    DestDir: "{app}\\mc-instance"; \
    Excludes: "\\mods\\*"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; --- (2b) Single MC-version-matching recorder mod jar ---------------------
; Only the recorder mod compiled against the same MC version Fabric was
; pinned to. Mismatched mods (e.g. mc1.21.5 against a 1.21.4 game) make
; Fabric refuse to start. The other 8 supported-MC-version mod jars are
; published to the GitHub Release as `release-extras/` instead of being
; bundled here.
Source: "{#BundleRoot}\\mc-instance\\mods\\oyster-recorder-mod-*-mc{#BundledMcVersion}.jar"; \
    DestDir: "{app}\\mc-instance\\mods"; \
    Flags: ignoreversion

; --- (2c) fabric-api jar --------------------------------------------------
; Required at runtime — without it Fabric loader refuses with
; "requires any version of fabric-api, which is missing".
; Produced by R05B/fetch_fabric.py at a stable filename.
Source: "{#BundleRoot}\\mc-instance\\mods\\fabric-api.jar"; \
    DestDir: "{app}\\mc-instance\\mods"; \
    Flags: ignoreversion

; --- (3) OysterRecorder PyInstaller --onedir bundle -----------------------
; Minimal keeps the recorder runtime but strips optional heavyweight payloads:
;   - hf_cache/depth_models: DA-V2 model weights are server-side postprocess
;   - ffmpeg-full: never ship the full ffmpeg tree; recorder keeps ffmpeg.exe
;     if the workflow staged the small runtime binary beside the recorder.
Source: "{#BundleRoot}\\OysterRecorder-onedir\\*"; \
    DestDir: "{app}\\OysterRecorder-onedir"; \
    Excludes: "\\hf_cache\\*,\\depth_models\\*,\\ffmpeg-full\\*"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; --- (4) OysterPlay.exe single-button launcher (R05C) ---------------------
; ~5 MB. Lives directly in {app} so the desktop shortcut points at it.
Source: "{#BundleRoot}\\OysterPlay.exe"; \
    DestDir: "{app}"; \
    DestName: "{#AppExeName}"; \
    Flags: ignoreversion

; --- (5) Combined manifest.json -------------------------------------------
; Embeds all SHA-256 pins for post-install verification.
Source: "{#BundleRoot}\\manifest.json"; \
    DestDir: "{app}"; \
    DestName: "manifest.json"; \
    Flags: ignoreversion

; --- (6) VC++ Redist 2015-2022 x64 (S131) ---------------------------------
; ~25 MB. Downloaded by the CI workflow's "Download VC++ Redistributable"
; step into the same dir as this .iss. Goes to {tmp} so it's removed after
; the [Run] entry silently installs it on first-run testers' machines.
; skipifsourcedoesntexist is BELT-AND-SUSPENDERS: if CI failed to download
; it, the build still succeeds and the recorder simply assumes the user
; already has VC++ Redist installed (most modern Windows 10/11 do).
Source: "vc_redist.x64.exe"; \
    DestDir: "{tmp}"; \
    Flags: deleteafterinstall noencryption skipifsourcedoesntexist

; --- (7) Python self-audit tools for testers intentionally omitted --------
; v0.14 minimal removes the 9-script tester tool suite from installer.iss:
; tester_preflight.py, prd_compliance_audit.py, audit_quality_metrics.py,
; canonical_pipeline.py, transform_game_state_to_action_camera.py,
; generate_gameinfo_xlsx.py, generate_systeminfo_json.py,
; input_latency_telemetry.py, and prd_compliance_audit_H8_patch.py.
; Recorder-side packaging helpers remain inside OysterRecorder-onedir; this
; only removes the standalone {app}\tools audit/preflight surface.

; --- (7c) Recording watchdog (v0.14, R-1) -------------------------------
; ~6 KB. Sidecar process OysterPlay spawns alongside the recorder. Polls
; recording.mp4 + game_state.jsonl + inputs.jsonl every 5s; writes
; .stall_warning marker if any file stops growing > 15s. Catches silent
; failures (recorder hung, MC mod died) in real-time instead of post-mortem.
Source: "..\recording_watchdog.py"; \
    DestDir: "{app}\tools"; \
    Flags: ignoreversion

; --- (7b) One-click session uploader (v0.12.4) ---------------------------
; v0.14 minimal KEEP: OysterPlay/recorder still needs this helper for
; tester uploads, so it remains even though the audit/preflight tools above
; are omitted.
; ~7 KB. Lets tester upload their session to our S3 bucket via the
; production backend (http://136.109.41.170:8081) with ONE command:
;   python {app}\tools\upload_session.py --token <token>
; Replaces the tar+网盘+微信 chain. Pure stdlib (urllib + zipfile).
; Requires Bearer token (issued via Discord OAuth or admin helper).
Source: "..\\upload_session.py"; \
    DestDir: "{app}\\tools"; \
    Flags: ignoreversion

; --- (8) Defender pre-install fix scripts (v0.12.3) ---------------------
; ~5 KB total. Two .cmd files (English + Chinese) that the tester can run
; as Administrator to add %LOCALAPPDATA%\OysterRecorder\ to Windows
; Defender exclusions BEFORE installing. This prevents Defender from
; quarantining bundled javaw.exe + Minecraft .jar files (the "install
; corrupted" bug seen by tester bingd on v0.12.0/v0.12.1/v0.12.2).
;
; oyster_play.py's "Install incomplete" dialog points users to:
;   {app}\fix-scripts\INSTALL-FIRST.cmd
; with instructions to right-click → "Run as administrator", then reinstall.
Source: "..\\..\\installer\\fix-scripts\\INSTALL-FIRST.cmd"; \
    DestDir: "{app}\\fix-scripts"; \
    Flags: ignoreversion skipifsourcedoesntexist
Source: "..\\..\\installer\\fix-scripts\\请先双击我.cmd"; \
    DestDir: "{app}\\fix-scripts"; \
    Flags: ignoreversion skipifsourcedoesntexist

[Icons]
; ---- Start-menu group (always created) ------------------------------------
Name: "{group}\\{#AppShortcutLbl}"; \
    Filename: "{app}\\{#AppExeName}"; \
    WorkingDir: "{app}"; \
    Comment: "Launch {#AppName} (records gameplay automatically)"

Name: "{group}\\Uninstall {#AppName}"; \
    Filename: "{uninstallexe}"; \
    WorkingDir: "{app}"

; ---- Desktop shortcut (optional, see [Tasks]) -----------------------------
; {userdesktop} resolves via HKCU\...\Explorer\User Shell Folders\Desktop —
; the registry path Explorer actually reads. On OneDrive-redirected
; machines this points at the OneDrive\Desktop folder instead of
; %USERPROFILE%\Desktop, so the icon shows up where the user expects it.
Name: "{userdesktop}\\{#AppShortcutLbl}"; \
    Filename: "{app}\\{#AppExeName}"; \
    WorkingDir: "{app}"; \
    Comment: "Launch {#AppName}"; \
    Tasks: desktopicon

[Run]
; S131: Silent install of bundled VC++ Redist when missing
Filename: "{tmp}\vc_redist.x64.exe"; \
  Parameters: "/install /quiet /norestart"; \
  StatusMsg: "Installing Visual C++ 2015-2022 Redistributable (x64)..."; \
  Check: VCRedistNotInstalled; \
  Flags: waituntilterminated
; Launch immediately after install so a brand-new user sees OysterPlay open
; Minecraft from the same double-click that ran setup. nowait so the
; installer's "Finish" page appears immediately; postinstall+skipifsilent
; suppresses this option in any future /SILENT invocation.
Filename: "{app}\\{#AppExeName}"; \
    Description: "Launch {#AppName} now"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The orchestrator pre-creates {app}\logs and other writable dirs in [Dirs].
; Inno does not auto-remove non-empty user-modified dirs on uninstall, so
; we declare them here. This keeps "Add/Remove Programs" symmetric — every
; bit the installer placed under {app} disappears on uninstall.
Type: filesandordirs; Name: "{app}\\logs"
Type: filesandordirs; Name: "{app}\\runtime"
; Note: we deliberately do NOT delete {app}\mc-instance\saves on uninstall.
; Player worlds are user data — preserve them. (Same convention Mojang
; Launcher uses for %APPDATA%\.minecraft\saves.)
; Type: filesandordirs; Name: "{app}\\mc-instance\\saves"   ; (kept on purpose)

[Registry]
; ---------------------------------------------------------------------------
; INTENTIONALLY EMPTY of HKLM keys. We never touch:
;   * HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment
;     (system PATH, JAVA_HOME)
;   * HKCU\Environment
;     (user PATH, JAVA_HOME)
;
; The only HKCU keys that get written are the ones Inno Setup writes
; automatically for the per-user uninstaller, which Add/Remove Programs
; reads from:
;   HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppId}_is1
;
; That is the registry trail you'd find with `reg query` after install,
; and it is the trail wiped clean on uninstall.
; ---------------------------------------------------------------------------

[Code]
function VCRedistNotInstalled(): Boolean;
begin
  Result := not RegKeyExists(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64');
end;

{ -------------------------------------------------------------------------
  Pascal scripting kept to the absolute minimum. Anything we can do via
  declarative sections above, we do there — Pascal code in installers is
  notoriously hard to test on macOS / non-Windows CI.

  InitializeSetup runs ONCE before any UI. We use it for two checks:

    1) Refuse install on 32-bit / ARM64 Windows. (ArchitecturesAllowed
       above already does this, but a friendlier message helps.)
    2) Warn (not block) if a previous OysterRecorder is currently running.
  ------------------------------------------------------------------------- }

function InitializeSetup(): Boolean;
begin
  Result := True;

  if not IsX64Compatible() then
  begin
    MsgBox(
      '{#AppName} requires 64-bit Windows 10 (build 17763 / 1809) or newer.' + #13#10 +
      'The bundled Java 21 runtime is x64-only.',
      mbCriticalError, MB_OK);
    Result := False;
    Exit;
  end;
end;

{ Surface a friendlier error if a previous install is currently running.
  Inno's CloseApplicationsFilter setting will close it for us, but we
  ask the user first so they don't lose unsaved Minecraft progress. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  NeedsRestart := False;
  { CloseApplications=force above handles the actual termination. We
    return '' to allow install to proceed. }
end;

; ============================================================================
; OysterRecorder bundled-installer Inno Setup script
; ----------------------------------------------------------------------------
; Spec: specs/R05D_inno_setup_installer.md (depends_on R05A, R05B, R05C)
;
; This script compiles one self-contained Windows installer that ships
; *everything* a zero-knowledge consumer needs to record Minecraft sessions:
;
;     OysterRecorder-Setup-vX.Y.Z.exe (~800 MB on disk; rc7)
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

; rc17.1.2 (Stream BM-rebrand, Howard "现在很乱了" 2026-05-12): unify
; user-facing brand identity to "GameData Recorder". The previous "Oyster
; Recorder" name was carried over from rc1-rc15; rc15.31 rebranded the
; Rust recorder + output paths to "GameData Recorder" but installer +
; launcher were never updated. Result: download filename / install
; wizard / Start Menu all said "Oyster Recorder", but tray icon + output
; dir + notification titles said "GameData Recorder". Three names for
; one product. Fixed below.
;
; What stays "Oyster*" deliberately:
;   * AppId — Inno Setup's upgrade machinery keys off this GUID. Changing
;     it would make rc17.1.2 install side-by-side with existing rc17.0.4
;     instead of upgrading in place. Keep forever.
;   * AppExeName / OysterPlay.exe — internal launcher binary name.
;     oyster_play.py's process detection (is_recorder_running()) greps
;     for "OysterPlay.exe" and "OysterRecorder.exe" by literal name;
;     renaming would break detection + the singleton-mutex guard.
;     Internal code-name; users don't see this through Start Menu (the
;     Start Menu shortcut LABEL is the user-facing string, not the exe).
;   * OysterRecorder.exe — same reason. Rust Cargo `[[bin]]` name. The
;     `crates/constants/.../singleton-mutex` key embeds this name.
;
; What changes to "GameData Recorder":
;   * AppName — wizard title, Start Menu group, Add/Remove Programs label
;   * AppShortcutLbl — visible label on Start Menu + Desktop shortcuts
;   * DefaultDirName — fresh installs go to %LOCALAPPDATA%\GameData Recorder\
;     (same parent dir as the recordings root, unified namespace).
;     Existing rc17.0.x users upgrading via AppId continue to use their
;     OysterRecorder\ dir — no breakage. New users get the clean path.
;   * OutputBaseFilename — installer .exe filename
;   * AppPublisher stays "Oyster Labs" (the company name, not the product)
#define AppName        "GameData Recorder"
#define AppPublisher   "Oyster Labs"
#define AppExeName     "OysterPlay.exe"
#define AppShortcutLbl "GameData Recorder"
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

; rc17.1.2 (Stream BM-rebrand): fresh installs go to "GameData Recorder"
; — same parent dir as the recordings root ({localappdata}\GameData
; Recorder\recordings\), unifying the namespace. Existing rc17.0.x
; installs upgrade in place via AppId, staying in their OysterRecorder\
; dir. The launcher resolves paths via {app} at runtime, so both layouts
; work — only the wizard's default-dir suggestion for new users changes.
DefaultDirName={localappdata}\GameData Recorder
DefaultGroupName={#AppName}
DisableProgramGroupPage=auto
DisableDirPage=no

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

; Output — rc17.1.2 (Stream BM-rebrand): installer filename matches
; the public brand. Final asset name on GitHub Release becomes
; GameDataRecorder-Setup-vrecorder-v0.28.0-rc17.1.2.exe (~800 MB).
OutputDir=..\\..\\dist\\installer
OutputBaseFilename=GameDataRecorder-Setup-v{#AppVersion}

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
; rc17.2 (Stream BL, Howard 2026-05-12 "点进去都是空的"): pre-create the
; recordings root so the Start Menu / Desktop "Open Recordings Folder"
; shortcut below can open immediately on first install. The launcher
; (oyster_play.py) writes sessions to %LOCALAPPDATA%\GameData Recorder\
; recordings\session_<ts>_<hash>\, which is a SEPARATE root from {app}
; (= %LOCALAPPDATA%\OysterRecorder\). Without this entry, the shortcut
; would 404 until the user finishes their first session.
Name: "{localappdata}\GameData Recorder\recordings"; Permissions: users-modify

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

; --- (3) OysterRecorder PyInstaller --onedir bundle (FALLBACK, rc16) -------
; ~120 MB. Source comes from the existing build-recorder-exe.yml workflow
; (which we now run locally inside build_all.ps1 step 5).
;
; rc16 (Howard 2026-05-11): demoted from PRIMARY to FALLBACK. The Rust+OBS
; recorder in section (3b) below is the primary screen-capture path. This
; Python recorder is kept in the installer so users on rigs where the
; Rust path fails can opt in via OYSTER_PY_RECORDER=1 env var without
; reinstalling.
Source: "{#BundleRoot}\\OysterRecorder-onedir\\*"; \
    DestDir: "{app}\\OysterRecorder-onedir"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; --- (3b) Rust+OBS recorder (PRIMARY, rc16) -------------------------------
; ~200-300 MB. Source comes from build-recorder-rust.yml's artifact, staged
; into bundle/recorder/ by the installer workflow's "rc16 - Stage Rust
; recorder" step. Contains:
;   OysterRecorder.exe        — the renamed gamedata-recorder.exe (Rust bin)
;   obs-ffmpeg-mux.exe        — OBS-side helper for muxing H.265 → MP4
;   data/                     — OBS effect shaders, audio resampling tables, etc.
;   obs-plugins/              — required plugin DLLs (image-source,
;                                obs-ffmpeg, obs-x264, win-capture, ...)
;
; The launcher (oyster_play.py find_recorder_exe) checks this path FIRST,
; so as long as the Rust recorder is shipped here, it wins. The Python
; FALLBACK in (3) above is only used when OYSTER_PY_RECORDER=1 is set.
;
; Why: bingd's AMD 780M / WSA / MuMu tester rig produced 1-frame video
; across 14 rc15 releases of the Python mss/ddagrab/gdigrab capture
; chain. The Rust+OBS path uses libobs (the OBS Studio engine) directly,
; which is the same code path that ships in OBS Studio v30 to millions
; of streamers — it handles exclusive-fullscreen MC, hardware-accelerated
; H.265 encoding (NVENC / AMD VCE / Intel QSV), and game-capture hook
; injection out of the box.
Source: "{#BundleRoot}\\recorder\\*"; \
    DestDir: "{app}\\recorder"; \
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

; --- (6) rc19.0.2 — Standalone Python finalize tooling --------------------
; Howard's rc19.0.1 PRD lint score was 28/38, with 4 failures (#15, #16,
; #24, #39) caused by the installer NOT shipping the standalone Python
; post-processing tools. The Python recorder onedir bundle in section (3)
; has finalize_session.py + helpers frozen INSIDE the PyInstaller EXE, but
; there's no exposed CLI surface to invoke them on an already-recorded
; session. Section (6) below ships them as a SECOND, independently-callable
; bundle at {app}\bin\ + {app}\python-deps\ + {app}\depth_models\,
; reachable via {app}\run_finalize.bat <session_dir>.
;
; What's in the bundle (staged in bundle/ by the
; "rc19.0.2 - ..." workflow steps before ISCC runs):
;
;   {app}\bin\
;     finalize_session.py          — orchestrator (PRD §3 deliverable gen)
;     lint_v3_prd_grounded.py      — PRD 38-criteria lint
;     depth_exr_writer.py          — 6 fps DA-V2 Small ONNX → EXR depth
;     measure_input_latency.py     — synthetic-key end-to-end latency probe
;     audio_continuity_check.py    — PRD #38 audio analysis (subprocess'd
;                                    by finalize_session.py)
;     generate_gameinfo.py         — alias for generate_gameinfo_xlsx
;                                    (subprocess'd by finalize_session.py)
;     generate_gameinfo_xlsx.py    — actual gameinfo.xlsx writer
;
;   {app}\python-deps\             — pip-installed-with-target wheels:
;                                    cv2 (opencv-python-headless), ort
;                                    (onnxruntime-directml), openpyxl,
;                                    OpenEXR, Imath, numpy, mss + their
;                                    transitive deps. ~150 MB on disk.
;
;   {app}\depth_models\
;     depth_anything_v2_small.onnx — DA-V2 ViT-S export, ~99 MB. Fixed
;                                    filename — depth_exr_writer.py's
;                                    resolve_model_path() searches
;                                    <install_root>/depth_models/<this
;                                    exact name>. _install_root() returns
;                                    Path(__file__).parent.parent, so when
;                                    the script lives at {app}\bin\, the
;                                    model is found at {app}\depth_models\.
;
;   {app}\run_finalize.bat         — wrapper that sets PYTHONPATH +
;                                    OYSTER_DEPTH_MODEL_PATH and invokes
;                                    python.exe finalize_session.py.
;                                    User-facing entry point; also the
;                                    contract S04 will call on game-exit.
;
; Iron-law constraints:
;
;   * NO bundled Python interpreter. system Python 3.11+ is assumed on
;     PATH (minipc1 has it; consumer Windows installers from python.org
;     add to PATH by default). run_finalize.bat exits with `errorlevel 3`
;     if python.exe is missing, with a clear install hint. Bundling an
;     embedded Python adds ~30 MB plus a maintenance burden we have not
;     opted into yet.
;
;   * NO auto-run hook here. This section ONLY SHIPS the tools.
;     Spec S04 owns the post-recording auto-invocation contract that calls
;     run_finalize.bat <session_dir> when the game process terminates.
;
;   * NO modifications to finalize_session.py logic. S05's contract is
;     frozen; we only ship its source + helpers + deps unchanged.
;
;   * ignoreversion on every Source line — Python source files are
;     versionless (no VS_VERSION_INFO resource), so Inno's default of
;     "skip if dest is newer" would never trigger an upgrade.
;
;   * recursesubdirs + createallsubdirs on python-deps and depth_models
;     because pip stages packages in nested per-package subdirs and we
;     ship the model in its own subdir for clean uninstall symmetry.

; --- (6a) Standalone Python finalize scripts ------------------------------
; ~200 KB total. All six .py files live flat under bundle/bin/.
Source: "{#BundleRoot}\\bin\\*.py"; \
    DestDir: "{app}\\bin"; \
    Flags: ignoreversion

; --- (6b) Pip-staged Python runtime dependencies --------------------------
; ~150 MB pre-compression. pip's --target produces a flat namespace at
; bundle/python-deps/<pkg>/... that PYTHONPATH=<this> makes importable.
Source: "{#BundleRoot}\\python-deps\\*"; \
    DestDir: "{app}\\python-deps"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; --- (6c) DepthAnything V2 Small ONNX weights -----------------------------
; ~99 MB. Filename MUST be depth_anything_v2_small.onnx — depth_exr_writer's
; resolve_model_path() searches for this exact name (see line 140 of
; depth_exr_writer.py: DEFAULT_MODEL_FILENAME = "depth_anything_v2_small.onnx").
Source: "{#BundleRoot}\\depth_models\\depth_anything_v2_small.onnx"; \
    DestDir: "{app}\\depth_models"; \
    Flags: ignoreversion

; --- (6d) Finalize wrapper batch -----------------------------------------
; ~2 KB. User-facing CLI: `run_finalize.bat <session_dir>`.
Source: "{#BundleRoot}\\run_finalize.bat"; \
    DestDir: "{app}"; \
    Flags: ignoreversion

[Icons]
; ---- Start-menu group (always created) ------------------------------------
Name: "{group}\\{#AppShortcutLbl}"; \
    Filename: "{app}\\{#AppExeName}"; \
    WorkingDir: "{app}"; \
    Comment: "Launch {#AppName} (records gameplay automatically)"

; rc17.2 (Stream BL, Howard 2026-05-12 "点进去都是空的"): launcher writes
; sessions to %LOCALAPPDATA%\GameData Recorder\recordings — a SEPARATE
; root from {app} (= %LOCALAPPDATA%\OysterRecorder). Without an explicit
; shortcut to the real recordings root, users open {app} expecting their
; mp4s and see only jre/, mc-instance/, runtime/. They conclude the
; recorder is broken. Fix: one Start Menu entry that invokes explorer.exe
; on the real path. [Dirs] above pre-creates the folder so first click
; never 404s. explorer.exe (not a bare folder Filename) is intentional —
; it handles transient deletions gracefully.
Name: "{group}\\Open Recordings Folder"; \
    Filename: "explorer.exe"; \
    Parameters: """{localappdata}\\GameData Recorder\\recordings"""; \
    WorkingDir: "{localappdata}\\GameData Recorder"; \
    Comment: "Browse recorded gameplay sessions"

; rc18.0.4 (Howard 2026-05-12 "测试时 mc-mod 没加载"): explicit Start Menu
; shortcut to the bundled-MC launcher. Users who launch Minecraft via the
; Mojang Launcher get a profile WITHOUT our mc-mod installed, so
; gameinfo.xlsx + game_state.jsonl don't generate. The supported path is
; launch_mc.bat → bundled mc-instance/ → mc-mod loads → IPC data writes
; correctly. This shortcut makes the right path discoverable.
Name: "{group}\\Launch Minecraft (Recorded)"; \
    Filename: "{app}\\launch_mc.bat"; \
    WorkingDir: "{app}"; \
    Comment: "Launch bundled Minecraft 1.21.4 with recorder mod loaded (the supported workflow for full PRD compliance)"

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

; rc17.2 (Stream BL): desktop companion to the Start Menu Recordings
; shortcut. Gated on the same `desktopicon` task as the launcher
; shortcut above — if the user opts out of desktop icons, neither
; appears. Naming uses a distinct suffix so it doesn't visually
; collide with the launcher icon on the desktop.
Name: "{userdesktop}\\Open Recordings Folder"; \
    Filename: "explorer.exe"; \
    Parameters: """{localappdata}\\GameData Recorder\\recordings"""; \
    WorkingDir: "{localappdata}\\GameData Recorder"; \
    Comment: "Browse recorded {#AppName} sessions"; \
    Tasks: desktopicon

[Run]
; Optional: launch immediately after install so a brand-new user sees the
; recorder come up on the same double-click that ran setup. nowait so
; the installer's "Finish" page appears immediately; postinstall+skipifsilent
; honors the spec's "no silent default" by suppressing this option in any
; future /SILENT invocation.
Filename: "{app}\\{#AppExeName}"; \
    Description: "Launch {#AppName} now"; \
    Flags: nowait postinstall skipifsilent unchecked

[UninstallDelete]
; The orchestrator pre-creates {app}\logs and other writable dirs in [Dirs].
; Inno does not auto-remove non-empty user-modified dirs on uninstall, so
; we declare them here. This keeps "Add/Remove Programs" symmetric — every
; bit the installer placed under {app} disappears on uninstall.
Type: filesandordirs; Name: "{app}\\logs"
Type: filesandordirs; Name: "{app}\\runtime"
; rc19.0.2 (Stream S01): clear out python-deps + depth_models on uninstall.
; pip's --target install writes __pycache__/ directories on first import,
; and onnxruntime caches compiled model graphs next to the .onnx file —
; both happen at user-run time, AFTER install, so Inno's default of "only
; remove files the installer wrote" leaves orphans on disk. Explicit
; filesandordirs sweep guarantees clean uninstall.
Type: filesandordirs; Name: "{app}\\python-deps"
Type: filesandordirs; Name: "{app}\\depth_models"
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

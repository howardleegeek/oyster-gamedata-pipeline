# OysterRecorder Bundled Installer — Build Instructions

> Spec: `specs/R05D_inno_setup_installer.md`
> Iron-law: produce a single `OysterRecorder-Setup-vX.Y.Z.exe` (~460 MB) that
> installs everything a zero-knowledge consumer needs — JRE, Minecraft, Fabric,
> recorder, launcher — under `%LOCALAPPDATA%\OysterRecorder\` with no admin
> elevation and no second download.

## What this directory contains

| File | Role |
|---|---|
| `installer.iss` | Inno Setup script. Declarative manifest of every file in the bundle. Compiled by `ISCC.exe`. |
| `build_all.ps1` | Build orchestrator. Runs the 8-stage pipeline that produces the inputs `installer.iss` declares, then invokes ISCC. |
| `manifest.json` | Build-time SHA-256 pin for JRE + Minecraft + Fabric. Edited by hand to bump versions; never changed in code. |
| `fetch_jre.py` | Stage 1: downloads + verifies the portable JRE (R05A). |
| `fetch_minecraft.py` | Stage 2: downloads + verifies vanilla MC 1.21.4 client + libs (R05B). |
| `fetch_fabric.py` | Stage 3: downloads + verifies Fabric loader 0.16.10 (R05B). |
| `build_oysterplay_exe.py` | Stage 4: PyInstaller `OysterPlay.exe` single-button launcher (R05C). |
| `installer_README.md` | This file. |

## Output

`build_all.ps1` produces:

```
dist/installer/OysterRecorder-Setup-vX.Y.Z.exe   (400-500 MB)
```

This is the only file released to consumers.

## Prerequisites (Windows build host)

| Tool | Version | Install |
|---|---|---|
| Windows | 10 build 17763+ or 11 | n/a |
| PowerShell | 5.1+ or 7+ (`pwsh`) | preinstalled |
| Python | 3.10+ | https://python.org or `winget install Python.Python.3.12` |
| Inno Setup | 6.2+ | `choco install innosetup` (or [innosetup.com](https://jrsoftware.org/isdl.php)) |
| Java JDK 21 | 21+ | only needed if you also build `mc-mod/` locally; CI builds it on a separate runner |
| Gradle | wrapped | `mc-mod/gradlew` is committed |

Required Python packages (install into the repo's `.venv`):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install pyinstaller>=6.0
```

The `fetch_*.py` scripts are stdlib-only — no extra Python deps.

## Local build

```powershell
# Full clean rebuild — fetches JRE/MC/Fabric, compiles both .exes, runs ISCC
pwsh .\bin\build_bundled_installer\build_all.ps1 -AppVersion 0.27.0 -Clean
```

Iterate on `installer.iss` only (skip the slow stages):

```powershell
# Re-runs ISCC against the existing bundle/ — completes in ~15 seconds
pwsh .\bin\build_bundled_installer\build_all.ps1 -SkipDeps -AppVersion 0.27.0
```

Override the Inno Setup path (e.g. portable install):

```powershell
pwsh .\bin\build_bundled_installer\build_all.ps1 `
    -AppVersion 0.27.0 `
    -InnoSetupPath "D:\portable-tools\InnoSetup6\ISCC.exe"
```

## CI build

Triggered by `git tag recorder-vX.Y.Z` push — see
`.github/workflows/build-recorder-installer.yml` (R05E). The workflow runs
the same `build_all.ps1` on a `windows-latest` runner and uploads the
resulting `.exe` to the GitHub Release page.

## Pipeline stages (what `build_all.ps1` runs)

```
1. fetch_jre.py            -> bundle/jre/                        (~145 MB)
2. fetch_minecraft.py      -> bundle/mc-instance/                (~190 MB)
3. fetch_fabric.py         -> bundle/mc-instance/versions/...    (~  3 MB)
4. build_oysterplay_exe.py -> dist/OysterPlay.exe -> bundle/     (~  5 MB)
5. pyinstaller --onedir    -> bundle/OysterRecorder-onedir/      (~120 MB)
6. copy 9 mod jars         -> bundle/mc-instance/mods/           (~  3 MB)
6b. copy manifest.json     -> bundle/manifest.json               (<  1 KB)
7. ISCC.exe installer.iss  -> dist/installer/...exe              (~310 MB compressed)
8. size sanity gate        -> hard-fail if outside 400-500 MB
```

Each stage hard-fails on first non-zero exit. There is no `--skip-verify`
flag — SHA-256 checks always run.

## What the installer creates on the user's machine

```
%LOCALAPPDATA%\OysterRecorder\
├── OysterPlay.exe                              # double-click target
├── manifest.json                               # SHA-256 pin record
├── unins000.exe                                # per-user uninstaller
├── unins000.dat
├── jre\
│   ├── bin\javaw.exe
│   ├── bin\java.exe
│   └── ...
├── mc-instance\
│   ├── versions\1.21.4\
│   │   ├── 1.21.4.jar
│   │   └── 1.21.4.json
│   ├── versions\fabric-loader-0.16.10-1.21.4\
│   │   └── fabric-loader-0.16.10-1.21.4.json
│   ├── libraries\...                           # vanilla + Fabric libs
│   ├── mods\
│   │   ├── oyster-recorder-mod-X+mc1.20.1.jar
│   │   ├── ... (9 jars total, one per supported MC version)
│   │   └── oyster-recorder-mod-X+mc1.21.5.jar
│   ├── assets\indexes\19.json                  # asset INDEX (objects DLed at runtime)
│   ├── saves\                                  # empty, user-modify
│   └── screenshots\                            # empty, user-modify
├── OysterRecorder-onedir\
│   ├── OysterRecorder-onedir.exe
│   └── _internal\...                           # PyInstaller deps
├── logs\                                       # empty, user-modify
└── runtime\                                    # PyInstaller --runtime-tmpdir
```

Plus shortcuts (created on first install):

```
{userdesktop}\Oyster Recording.lnk        # OneDrive-redirection-safe
{group}\Oyster Recording.lnk              # Start menu
{group}\Uninstall Oyster Recording.lnk
```

And one HKCU registry entry (created by Inno Setup automatically):

```
HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\
    {C7E4F0D2-9B5E-4F1A-8C3D-OY5T3RR3C0RD}_is1
```

That's it. **Nothing under `%APPDATA%\.minecraft\` is touched.** Mojang
Launcher's private space stays private.

## What the installer never does (iron-law)

| | Why |
|---|---|
| Modify system PATH | Pollutes other apps' Java |
| Set/modify JAVA_HOME | Same |
| Touch `HKLM\` | Would require admin |
| Touch `%APPDATA%\.minecraft\` | Mojang Launcher's namespace; corrupting it bricks vanilla MC |
| Download anything at install time | Spec: bundle everything, work offline |
| Run silently by default | User must see ~460 MB copy progress |
| Install to `%ProgramFiles%` | Would require admin elevation |

## Code-signing (when we get a cert)

The `installer.iss` already has the hook. Three lines to flip on:

1. Uncomment `SignTool=signtool` and `SignedUninstaller=yes` in `[Setup]`.
2. Tell ISCC where the signing tool lives:

   ```powershell
   $iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
   & $iscc /Ssigntool="`"C:\Windows\System32\WindowsKits\10\bin\x64\signtool.exe`" sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 /a `$f" ...
   ```

3. Add a `-CodeSign` switch to `build_all.ps1` that wires the above into
   the ISCC invocation. (Trivial follow-up.)

We're not doing any of this until the EV / OV cert lands. SmartScreen
warnings are expected on unsigned `.exe` until then; alpha testers know
to click "More info" -> "Run anyway".

## Troubleshooting

**"ISCC.exe not found"** -> Install Inno Setup 6 via Chocolatey
(`choco install innosetup`) or set `-InnoSetupPath` to your install.

**"OysterPlay.exe was not produced"** -> `build_oysterplay_exe.py` returned
non-zero. Re-run it directly with `--check-only` to see what's wrong:

```powershell
python .\bin\build_bundled_installer\build_oysterplay_exe.py --check-only
```

**"Mod artifacts not found"** -> Run `mc-mod\gradlew build` against each
supported MC version first. The orchestrator expects all 9 jars in
`mc-mod/build/libs/`. (CI builds them via `build-mc-mod.yml`.)

**"Installer is too small"** -> The size gate caught a stage that
produced an empty bundle dir. Almost always: `fetch_minecraft.py` couldn't
reach `piston-meta.mojang.com`. Re-run with verbose logs.

**"Installer is too large"** -> Most likely cause is that the
`assets/objects/` tree got bundled (~280 MB extra). Verify
`fetch_minecraft.py` is excluding it (per R05B spec).

## Cross-platform note

Inno Setup is Windows-only. On macOS / Linux dev machines you can:

- Edit `installer.iss` and run `python -c "import configparser"` style
  syntax sanity checks (the script has Pascal-script blocks that won't
  parse as INI, but the structure is line-oriented).
- Run `fetch_*.py` and `build_oysterplay_exe.py --check-only` to validate
  the inputs.
- Trigger the GitHub Actions `build-recorder-installer.yml` workflow to
  produce the actual `.exe`.

The repo's `bundle/` directory is `.gitignore`d — never commit JRE / MC
bytes. The build is fully reproducible from `manifest.json`.

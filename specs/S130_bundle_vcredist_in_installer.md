---
task_id: S130-bundle-vcredist-in-installer
priority: 1
estimated_minutes: 20
modifies:
  - installer/oyster-recorder.iss
  - .github/workflows/build-recorder-windows.yml
  - tests/test_installer_vcredist_bundle.py
executor: qwen3.6-plus
---

## 目标 — Bundle Visual C++ Redistributable 2015-2022 (x64) into installer so tester's fresh Windows works on double-click

Tester confirmed root cause: minipc1 (Howard dev box) has VC++ Redist installed; tester fresh Windows doesn't → libobs-d3d11.dll load fail → install/launch crash.

## Step-by-step (必须 write_file, 不能只 analyze)

### Step 1 — read current `.github/workflows/build-recorder-windows.yml`

read_file('.github/workflows/build-recorder-windows.yml')

### Step 2 — write_file workflow: add VC++ download step BEFORE `Compile installer`

Add a new step between cargo build success and Inno Setup compile:

```yaml
      - name: Download VC++ Redistributable
        shell: pwsh
        run: |
          $url = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
          $out = "installer/vc_redist.x64.exe"
          Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing
          $size = (Get-Item $out).Length
          if ($size -lt 10000000) {
            Write-Error "vc_redist.x64.exe download too small: $size bytes"
            exit 1
          }
          Write-Host "Downloaded vc_redist.x64.exe ($size bytes)"
```

### Step 3 — read current `installer/oyster-recorder.iss`

read_file('installer/oyster-recorder.iss')

### Step 4 — write_file `installer/oyster-recorder.iss`: add 3 sections

**(a)** Add to `[Files]` section (or create if missing):

```
Source: "vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall noencryption; \
  Check: VCRedistNeedsInstall
```

**(b)** Add to `[Run]` section (or create if missing) BEFORE the OysterRecorder.exe run line:

```
Filename: "{tmp}\vc_redist.x64.exe"; \
  Parameters: "/install /quiet /norestart"; \
  StatusMsg: "Installing Visual C++ 2015-2022 Redistributable (x64)..."; \
  Check: VCRedistNeedsInstall; \
  Flags: waituntilterminated
```

**(c)** Add to `[Code]` section (or create at end of file):

```pascal
[Code]
function VCRedistNeedsInstall(): Boolean;
var
  Installed: Cardinal;
begin
  Result := True;
  if RegQueryDWordValue(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64',
                        'Installed', Installed) then begin
    if Installed = 1 then
      Result := False;
  end;
end;
```

### Step 5 — write_file `tests/test_installer_vcredist_bundle.py`

```python
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
    """ISS must define VCRedistNeedsInstall Pascal function."""
    iss = _iss()
    assert "VCRedistNeedsInstall" in iss
    assert "RegQueryDWordValue" in iss
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
    assert "aka.ms/vs/17/release/vc_redist.x64.exe" in yml or \
           "vc_redist" in yml.lower()
```

### Step 6 — run pytest verify

run_cmd("python3 -m pytest tests/test_installer_vcredist_bundle.py -v")

必须 4 tests pass.

## 约束

- ≤ 15 turns
- 必须 3 个 write_file (workflow + iss + tests)
- 不重写整个 .iss 或 workflow — surgical insertion
- 不删 check_runtime.bat (留作 docs reference)
- 直接 commit 到 branch `feat/S130-bundle-vcredist`

## 验收

- [ ] vc_redist.x64.exe downloaded in CI
- [ ] .iss has Source: vc_redist + [Run] with /quiet /norestart + [Code] VCRedistNeedsInstall
- [ ] 4 pytest tests pass
- [ ] Black + ruff (if py touched)

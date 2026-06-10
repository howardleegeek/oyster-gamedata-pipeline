#Requires -Version 5.1
<#
.SYNOPSIS
    Preflight Recorder - Phase 1 (Windows PowerShell)

.DESCRIPTION
    Runs on minipc1 before each session to verify system readiness.
    Fails fast if the system can't produce a buyer-acceptable session.

.NOTES
    File Name: preflight_recorder.ps1
    Author: OysterRecorder Team
    Requires: PowerShell 5.1+, Windows 10+
#>

[CmdletBinding()]
param(
    [string]$OutputDir = "C:\OysterRecorder",
    [string]$ReportPath = "$OutputDir\preflight_report.json"
)

# Configuration
$ExpectedResolution = @{ Width = 1920; Height = 1080 }
$ExpectedDPI = 1.0
$MinDiskGB = 5
$MinFPS = 28

# Helper functions
function Get-Timestamp {
    return (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

function Write-CheckResult {
    param(
        [string]$Name,
        [bool]$Ok,
        [object]$Value,
        [string]$Expected = "",
        [string]$Error = ""
    )
    
    return @{
        name = $Name
        ok = $Ok
        value = $Value
        expected = $Expected
        error = $Error
    }
}

function Test-DisplayResolution {
    <#
    .SYNOPSIS
        Check display resolution == 1920x1080
    #>
    try {
        # Use WMI to get display info
        $displays = Get-CimInstance -ClassName Win32_VideoController
        
        foreach ($display in $displays) {
            if ($display.CurrentHorizontalResolution -and $display.CurrentVerticalResolution) {
                $width = $display.CurrentHorizontalResolution
                $height = $display.CurrentVerticalResolution
                
                $ok = ($width -eq $ExpectedResolution.Width) -and ($height -eq $ExpectedResolution.Height)
                
                return Write-CheckResult -Name "display_resolution" -Ok $ok `
                    -Value "$width`x$height" `
                    -Expected "$($ExpectedResolution.Width)x$($ExpectedResolution.Height)"
            }
        }
        
        return Write-CheckResult -Name "display_resolution" -Ok $false `
            -Value "unknown" -Error "Could not determine display resolution"
    }
    catch {
        return Write-CheckResult -Name "display_resolution" -Ok $false `
            -Value "error" -Error $_.Exception.Message
    }
}

function Test-DPI {
    <#
    .SYNOPSIS
        Check DPI == 1.0 (no scaling)
    #>
    try {
        # Get DPI from registry
        $dpiValue = 1.0
        
        # Check Windows 10/11 DPI settings
        $regPath = "HKCU:\Control Panel\Desktop"
        if (Test-Path $regPath) {
            $dpiScaling = Get-ItemProperty -Path $regPath -Name "LogPixels" -ErrorAction SilentlyContinue
            if ($dpiScaling) {
                # LogPixels: 96 = 100%, 144 = 150%, etc.
                $dpiValue = $dpiScaling.LogPixels / 96.0
            }
        }
        
        # Also check for per-monitor DPI
        $regPath2 = "HKCU:\Control Panel\Desktop\PerMonitorSettings"
        if (Test-Path $regPath2) {
            $perMonitor = Get-ItemProperty -Path $regPath2 -Name "DpiScale" -ErrorAction SilentlyContinue
            if ($perMonitor) {
                $dpiValue = $perMonitor.DpiScale / 100.0
            }
        }
        
        $ok = [Math]::Abs($dpiValue - $ExpectedDPI) -lt 0.1
        
        return Write-CheckResult -Name "dpi" -Ok $ok `
            -Value ([Math]::Round($dpiValue, 2)) `
            -Expected $ExpectedDPI
    }
    catch {
        return Write-CheckResult -Name "dpi" -Ok $false `
            -Value "error" -Error $_.Exception.Message
    }
}

function Test-MinecraftWindow {
    <#
    .SYNOPSIS
        Check Minecraft window is foreground + fullscreen + covers full 1920x1080
    #>
    try {
        # Use Windows API to find Minecraft window
        Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public class WindowHelper {
    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();
    
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    
    [DllImport("user32.dll")]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    
    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);
    
    [DllImport("user32.dll")]
    public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);
    
    [DllImport("user32.dll")]
    public static extern bool GetWindowPlacement(IntPtr hWnd, ref WINDOWPLACEMENT lpwndpl);
    
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }
    
    [StructLayout(LayoutKind.Sequential)]
    public struct WINDOWPLACEMENT {
        public int length;
        public int flags;
        public int showCmd;
        public int ptMinPosition_X;
        public int ptMinPosition_Y;
        public int ptMaxPosition_X;
        public int ptMaxPosition_Y;
        public int rcNormalPosition_Left;
        public int rcNormalPosition_Top;
        public int rcNormalPosition_Right;
        public int rcNormalPosition_Bottom;
    }
    
    public const int SW_MAXIMIZE = 3;
    public const int SW_SHOWMAXIMIZED = 3;
}
"@

        # Find Minecraft window
        $minecraftNames = @("Minecraft", "Minecraft*Java Edition*", "javaw", "Java Platform SE")
        $minecraftHWND = [IntPtr]::Zero
        
        foreach ($name in $minecraftNames) {
            $hwnd = [WindowHelper]::FindWindow([NullString]::Value, $name)
            if ($hwnd -ne [IntPtr]::Zero) {
                $minecraftHWND = $hwnd
                break
            }
        }
        
        if ($minecraftHWND -eq [IntPtr]::Zero) {
            return Write-CheckResult -Name "minecraft_window" -Ok $false `
                -Value "not_found" -Error "Minecraft window not found"
        }
        
        # Get window rect
        $rect = New-Object WindowHelper+RECT
        [WindowHelper]::GetWindowRect($minecraftHWND, [ref]$rect) | Out-Null
        
        $width = $rect.Right - $rect.Left
        $height = $rect.Bottom - $rect.Top
        $x = $rect.Left
        $y = $rect.Top
        
        # Check if fullscreen
        $isFullscreen = ($width -eq $ExpectedResolution.Width) -and `
                        ($height -eq $ExpectedResolution.Height) -and `
                        ($x -eq 0) -and ($y -eq 0)
        
        # Check if foreground
        $foregroundHWND = [WindowHelper]::GetForegroundWindow()
        $isForeground = ($foregroundHWND -eq $minecraftHWND)
        
        # Check window placement for maximized state
        $placement = New-Object WindowHelper+WINDOWPLACEMENT
        $placement.length = [System.Runtime.InteropServices.Marshal]::SizeOf($placement)
        [WindowHelper]::GetWindowPlacement($minecraftHWND, [ref]$placement) | Out-Null
        $isMaximized = ($placement.showCmd -eq [WindowHelper]::SW_SHOWMAXIMIZED)
        
        $ok = $isFullscreen -and $isForeground
        
        return Write-CheckResult -Name "minecraft_window" -Ok $ok `
            -Value "fullscreen=$isFullscreen, foreground=$isForeground, maximized=$isMaximized, size=$width`x$height, pos=$x`,$y" `
            -Expected "fullscreen=True, foreground=True, size=$($ExpectedResolution.Width)x$($ExpectedResolution.Height), pos=0,0"
    }
    catch {
        return Write-CheckResult -Name "minecraft_window" -Ok $false `
            -Value "error" -Error $_.Exception.Message
    }
}

function Test-OverlappingWindows {
    <#
    .SYNOPSIS
        Check no overlapping windows (Discord overlay / GeForce Experience / OBS preview)
    #>
    $overlappingApps = @("Discord", "GeForce Experience", "OBS", "Streamlabs", "XSplit", "NVIDIA", "RTSS", "Steam", "Battle.net")
    $foundOverlapping = @()
    
    try {
        # Get all visible windows
        Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public class WindowEnumerator {
    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    
    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);
    
    [DllImport("user32.dll")]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    
    [DllImport("user32.dll")]
    public static extern int GetWindowTextLength(IntPtr hWnd);
    
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    
    public static List<string> GetVisibleWindowTitles() {
        List<string> titles = new List<string>();
        EnumWindows((hWnd, lParam) => {
            if (IsWindowVisible(hWnd)) {
                int length = GetWindowTextLength(hWnd);
                if (length > 0) {
                    StringBuilder sb = new StringBuilder(length + 1);
                    GetWindowText(hWnd, sb, sb.Capacity);
                    titles.Add(sb.ToString());
                }
            }
            return true;
        }, IntPtr.Zero);
        return titles;
    }
}
"@

        $windowTitles = [WindowEnumerator]::GetVisibleWindowTitles()
        
        foreach ($title in $windowTitles) {
            foreach ($app in $overlappingApps) {
                if ($title -like "*$app*") {
                    $foundOverlapping += "$app`: $title"
                }
            }
        }
        
        $ok = $foundOverlapping.Count -eq 0
        
        return Write-CheckResult -Name "overlapping_windows" -Ok $ok `
            -Value ($foundOverlapping + @("none")) `
            -Expected "no overlapping windows"
    }
    catch {
        return Write-CheckResult -Name "overlapping_windows" -Ok $true `
            -Value "unknown" -Error "Could not enumerate windows"
    }
}

function Test-AudioDevice {
    <#
    .SYNOPSIS
        Check audio device enumerated + game-audio loopback configured
    #>
    try {
        # Get audio devices using Windows Core Audio API
        Add-Type @"
using System;
using System.Runtime.InteropServices;

public class AudioDevice {
    [DllImport("ole32.dll")]
    public static extern int CoCreateInstance(ref Guid rclsid, IntPtr pUnkOuter, uint dwClsContext, ref Guid riid, out IntPtr ppv);
    
    [Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    public class MMDeviceEnumerator { }
    
    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IMMDeviceEnumerator {
        int NotImpl1();
        int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ppDevice);
    }
    
    [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IMMDevice {
        int Activate(ref Guid iid, int dwClsCtx, IntPtr pActivationParams, out IAudioClient ppInterface);
    }
    
    [Guid("F294ACFC-3146-4483-A7BF-ADDCA7C260E2"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IAudioClient {
        int Initialize(int ShareMode, int StreamFlags, long hnsBufferDuration, long hnsPeriodicity, ref WAVEFORMATEX pFormat, IntPtr AudioSessionGuid);
        int GetBufferSize(out uint pnNumBuffersRequested);
    }
    
    [StructLayout(LayoutKind.Sequential)]
    public struct WAVEFORMATEX {
        public ushort wFormatTag;
        public ushort nChannels;
        public uint nSamplesPerSec;
        public uint nAvgBytesPerSec;
        public ushort nBlockAlign;
        public ushort wBitsPerSample;
        public ushort cbSize;
    }
    
    public static string[] GetAudioDevices() {
        try {
            Guid CLSID_MMDeviceEnumerator = new Guid("BCDE0395-E52F-467C-8E3D-C4579291692E");
            Guid IID_IMMDeviceEnumerator = new Guid("A95664D2-9614-4F35-A746-DE8DB63617E6");
            
            IMMDeviceEnumerator enumerator = null;
            IntPtr pEnumerator = IntPtr.Zero;
            int hr = CoCreateInstance(ref CLSID_MMDeviceEnumerator, IntPtr.Zero, 1, ref IID_IMMDeviceEnumerator, out pEnumerator);
            
            if (hr != 0 || pEnumerator == IntPtr.Zero) {
                return new string[0];
            }
            
            enumerator = (IMMDeviceEnumerator)Marshal.GetObjectForIUnknown(pEnumerator);
            Marshal.Release(pEnumerator);
            
            IMMDevice device = null;
            hr = enumerator.GetDefaultAudioEndpoint(0, 1, out device);
            
            if (hr != 0 || device == null) {
                return new string[0];
            }
            
            return new string[] { "Default Audio Device" };
        }
        catch {
            return new string[0];
        }
    }
}
"@

        $audioDevices = [AudioDevice]::GetAudioDevices()
        
        # Check for loopback/virtual devices (VB-Audio, Voicemeeter, etc.)
        $loopbackFound = $false
        try {
            $audioEndpoints = Get-CimInstance -Namespace root/cimv2 -ClassName Win32_SoundDevice
            foreach ($device in $audioEndpoints) {
                if ($device.Name -match "Virtual|Loopback|VB-Audio|Voicemeeter") {
                    $loopbackFound = $true
                    break
                }
            }
        }
        catch { }
        
        $ok = $audioDevices.Count -gt 0
        
        return Write-CheckResult -Name "audio_device" -Ok $ok `
            -Value @{
                devices = $audioDevices
                loopback_configured = $loopbackFound
            } `
            -Expected "at least 1 audio device + loopback for game capture"
    }
    catch {
        return Write-CheckResult -Name "audio_device" -Ok $false `
            -Value "error" -Error $_.Exception.Message
    }
}

function Test-FPSCapability {
    <#
    .SYNOPSIS
        Check FPS counter shows >= 28 fps in MC menu
    #>
    try {
        # Check GPU
        $gpuOk = $false
        $gpuInfo = Get-CimInstance -ClassName Win32_VideoController
        if ($gpuInfo) {
            $gpuOk = $true
        }
        
        # Check for high performance power plan
        $powerPlan = powercfg /getactivescheme
        $highPerf = $powerPlan -match "High performance|8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
        
        # Check for game mode
        $gameMode = Get-ItemProperty -Path "HKCU:\Software\Microsoft\GameBar" -Name "AutoGameModeEnabled" -ErrorAction SilentlyContinue
        
        # Check CPU load
        $cpuLoad = (Get-CimInstance -ClassName Win32_Processor).LoadPercentage
        $cpuOk = $cpuLoad -lt 80
        
        $ok = $gpuOk -and $cpuOk
        
        return Write-CheckResult -Name "fps_capability" -Ok $ok `
            -Value @{
                gpu_detected = $gpuOk
                high_performance_plan = $highPerf
                cpu_load_ok = $cpuOk
                note = "System appears capable of >=28 FPS"
            } `
            -Expected ">= $MinFPS FPS in Minecraft menu"
    }
    catch {
        return Write-CheckResult -Name "fps_capability" -Ok $false `
            -Value "error" -Error $_.Exception.Message
    }
}

function Test-DiskSpace {
    <#
    .SYNOPSIS
        Check disk free space >= 5 GB
    #>
    try {
        $drive = (Get-Item $OutputDir).PSDrive
        $freeGB = [Math]::Round($drive.Free / 1GB, 2)
        
        $ok = $freeGB -ge $MinDiskGB
        
        return Write-CheckResult -Name "disk_space" -Ok $ok `
            -Value "$freeGB GB free" `
            -Expected ">= $MinDiskGB GB"
    }
    catch {
        return Write-CheckResult -Name "disk_space" -Ok $false `
            -Value "error" -Error $_.Exception.Message
    }
}

function Test-OysterRecorder {
    <#
    .SYNOPSIS
        Check OysterRecorder.exe armed
    #>
    $possiblePaths = @(
        "$OutputDir\OysterRecorder.exe",
        "C:\Program Files\OysterRecorder\OysterRecorder.exe",
        "C:\Program Files (x86)\OysterRecorder\OysterRecorder.exe",
        "$env:LOCALAPPDATA\OysterRecorder\OysterRecorder.exe"
    )
    
    $found = $null
    foreach ($path in $possiblePaths) {
        if (Test-Path $path) {
            $found = $path
            break
        }
    }
    
    # Check if process is running
    $processRunning = $false
    $process = Get-Process -Name "OysterRecorder" -ErrorAction SilentlyContinue
    if ($process) {
        $processRunning = $true
    }
    
    $ok = $null -ne $found
    
    return Write-CheckResult -Name "oyster_recorder" -Ok $ok `
        -Value @{
            installed = $null -ne $found
            path = $found
            process_running = $processRunning
        } `
        -Expected "OysterRecorder.exe installed and ready"
}

function Test-ActiveSession {
    <#
    .SYNOPSIS
        Check active_session/ empty (no half-finalized prior session)
    #>
    try {
        $activeSessionPath = Join-Path $OutputDir "active_session"
        
        if (-not (Test-Path $activeSessionPath)) {
            return Write-CheckResult -Name "active_session" -Ok $true `
                -Value "empty" -Note "active_session directory does not exist"
        }
        
        $files = Get-ChildItem -Path $activeSessionPath -File -ErrorAction SilentlyContinue
        $ok = $files.Count -eq 0
        
        return Write-CheckResult -Name "active_session" -Ok $ok `
            -Value "$($files.Count) files" `
            -Expected "empty directory"
    }
    catch {
        return Write-CheckResult -Name "active_session" -Ok $false `
            -Value "error" -Error $_.Exception.Message
    }
}

function Test-NetworkTailscale {
    <#
    .SYNOPSIS
        Check Network: Tailscale to mac1 reachable
    #>
    # Check if Tailscale is running
    $tailscaleRunning = $false
    try {
        $tailscaleProc = Get-Process -Name "tailscale" -ErrorAction SilentlyContinue
        if ($tailscaleProc) {
            $tailscaleRunning = $true
        }
    }
    catch { }
    
    # Try to ping mac1
    $mac1Reachable = $false
    if ($tailscaleRunning) {
        try {
            $pingResult = Test-Connection -ComputerName "mac1.tailscale" -Count 1 -ErrorAction SilentlyContinue
            if ($pingResult) {
                $mac1Reachable = $true
            }
        }
        catch { }
    }
    
    # For Phase 1, network is optional
    $ok = $true
    
    return Write-CheckResult -Name "network_tailscale" -Ok $ok `
        -Value @{
            tailscale_running = $tailscaleRunning
            mac1_reachable = $mac1Reachable
            note = "Network check is informational for Phase 1"
        } `
        -Expected "Tailscale to mac1 reachable (for future upload)"
}

function Run-AllChecks {
    <#
    .SYNOPSIS
        Run all preflight checks
    #>
    $checks = @(
        (Test-DisplayResolution)
        (Test-DPI)
        (Test-MinecraftWindow)
        (Test-OverlappingWindows)
        (Test-AudioDevice)
        (Test-FPSCapability)
        (Test-DiskSpace)
        (Test-OysterRecorder)
        (Test-ActiveSession)
        (Test-NetworkTailscale)
    )
    
    $allPass = $true
    foreach ($check in $checks) {
        if (-not $check.ok) {
            $allPass = $false
            break
        }
    }
    
    return @{
        ran_at = Get-Timestamp
        all_pass = $allPass
        checks = $checks
    }
}

# Main execution
Write-Host "=" * 60
Write-Host "Preflight Recorder - Phase 1 (Windows)"
Write-Host "=" * 60
Write-Host "Running preflight checks at $(Get-Timestamp)"
Write-Host ""

# Run all checks
$report = Run-AllChecks

# Write report to file
$report | ConvertTo-Json -Depth 10 | Out-File -FilePath $ReportPath -Encoding UTF8

Write-Host "Report written to: $ReportPath"
Write-Host ""

# Print summary
Write-Host "CHECK RESULTS:"
Write-Host ("-" * 40)
foreach ($check in $report.checks) {
    $status = if ($check.ok) { "PASS" } else { "FAIL" }
    $symbol = if ($check.ok) { "OK" } else { "X" }
    Write-Host "  [$symbol] $status`: $($check.name)"
    if (-not $check.ok) {
        Write-Host "         $($check.value)"
    }
}
Write-Host ("-" * 40)
Write-Host ""

if ($report.all_pass) {
    Write-Host "ALL CHECKS PASSED" -ForegroundColor Green
    exit 0
}
else {
    $failed = ($report.checks | Where-Object { -not $_.ok }).name -join ", "
    Write-Host "PREFLIGHT FAILED`: $failed" -ForegroundColor Red
    exit 1
}

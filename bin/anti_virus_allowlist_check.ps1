<#
.SYNOPSIS
    Windows PowerShell script verifying Defender exclusions for ffmpeg/Java/paper.jar.
.DESCRIPTION
    Checks if Windows Defender has appropriate exclusions configured for ffmpeg,
    Java runtime, and paper.jar to prevent scanning interference during runtime.
    Outputs PASS/FAIL per item and returns exit code 0 (all pass) or 1 (any fail).
.NOTES
    File: anti_virus_allowlist_check.ps1
    Requires: Windows PowerShell 5.1+ or PowerShell 7+, Administrator privileges
#>

#Requires -RunAsAdministrator

function Get-DefenderExclusionPaths {
    <#
    .SYNOPSIS
        Retrieves Windows Defender exclusion paths.
    .OUTPUTS
        System.Array - Array of exclusion paths configured in Windows Defender.
    #>
    [CmdletBinding()]
    [OutputType([System.Array])]
    param()
    try {
        $prefs = Get-MpPreference -ErrorAction Stop
        return @($prefs.ExclusionPath)
    }
    catch {
        Write-Warning "Failed to retrieve Defender preferences: $($_.Exception.Message)"
        return @()
    }
}

function Test-ExclusionExists {
    <#
    .SYNOPSIS
        Checks if a given path or pattern is in the exclusion list.
    .PARAMETER Exclusions
        Array of exclusion paths from Defender.
    .PARAMETER Pattern
        The pattern or path to check for.
    .OUTPUTS
        System.Boolean - True if pattern found in exclusions.
    #>
    [CmdletBinding()]
    [OutputType([System.Boolean])]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Array]$Exclusions,
        [Parameter(Mandatory = $true)]
        [string]$Pattern
    )
    foreach ($exclusion in $Exclusions) {
        if ($exclusion -like "*$Pattern*") { return $true }
    }
    return $false
}

function Main {
    <#
    .SYNOPSIS
        Main entry point for the antivirus allowlist check.
    .OUTPUTS
        System.Int32 - Exit code: 0 for success, 1 for missing exclusions.
    #>
    [CmdletBinding()]
    [OutputType([System.Int32])]
    param()

    Write-Host "=== Antivirus Allowlist Check ===" -ForegroundColor Cyan
    Write-Host "Checking Windows Defender exclusions...`n"

    $requiredExclusions = @(
        @{Name = "ffmpeg"; Pattern = "ffmpeg"},
        @{Name = "Java"; Pattern = "java"},
        @{Name = "paper.jar"; Pattern = "paper.jar"}
    )

    $exclusions = Get-DefenderExclusionPaths
    if ($exclusions.Count -eq 0) { Write-Warning "No exclusion paths found." }

    $allPassed = $true
    foreach ($item in $requiredExclusions) {
        $found = Test-ExclusionExists -Exclusions $exclusions -Pattern $item.Pattern
        $status = if ($found) { "[PASS]" } else { "[FAIL]" }
        $color = if ($found) { "Green" } else { "Red" }
        Write-Host "$status $($item.Name)" -ForegroundColor $color
        if (-not $found) { $allPassed = $false }
    }

    Write-Host "`n=== Summary ===" -ForegroundColor Cyan
    if ($allPassed) {
        Write-Host "All required exclusions are configured." -ForegroundColor Green
        return 0
    }
    else {
        Write-Host "Some exclusions are missing. Please configure them." -ForegroundColor Red
        return 1
    }
}

# Script entry point
exit Main